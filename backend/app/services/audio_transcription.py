"""Transcripción de notas de voz (WhatsApp) con OpenAI."""

from __future__ import annotations

import base64
import binascii
import logging
import os

import httpx

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 8 * 1024 * 1024
VOICE_NOTE_PLACEHOLDER = "[Mandó una nota de voz]"
AUDIO_PREFIX = "🎤 Audio: "

_EXT_BY_MIME = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "m4a",
    "audio/amr": "amr",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
}


def _extension(mime: str | None) -> str:
    base = (mime or "").split(";")[0].strip().lower()
    return _EXT_BY_MIME.get(base, "ogg")


def decode_audio_base64(value: str | None) -> bytes | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not data or len(data) > MAX_AUDIO_BYTES:
        return None
    return data


def transcribe_audio(data: bytes, mime: str | None = None) -> str | None:
    """Devuelve el texto transcripto o None si no se pudo."""
    if not data or len(data) > MAX_AUDIO_BYTES:
        return None
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return None
    try:
        from app.services.openai_service import _client

        client = _client()
        model = (os.getenv("OPENAI_TRANSCRIBE_MODEL") or "gpt-4o-mini-transcribe").strip()
        filename = f"audio.{_extension(mime)}"
        base_mime = (mime or "audio/ogg").split(";")[0].strip() or "audio/ogg"
        result = client.audio.transcriptions.create(
            model=model,
            file=(filename, data, base_mime),
            language="es",
        )
        text = str(getattr(result, "text", "") or "").strip()
        return text or None
    except Exception:
        logger.exception("audio transcription failed (bytes=%s mime=%s)", len(data), mime)
        return None


def transcribed_message(transcript: str | None) -> str:
    text = (transcript or "").strip()
    return f"{AUDIO_PREFIX}{text}" if text else VOICE_NOTE_PLACEHOLDER


def resolve_inbound_body_with_audio(
    *,
    body: str | None,
    audio_base64: str | None = None,
    audio_mime: str | None = None,
) -> str:
    """
    Si viene audio (Meow / extensión), transcribe y deja el texto listo para auto-reply.
    Sin audio → body original (o placeholder vacío).
    """
    plain = (body or "").strip()
    raw_b64 = (audio_base64 or "").strip()
    if not raw_b64:
        return plain
    data = decode_audio_base64(raw_b64)
    transcript = transcribe_audio(data, audio_mime) if data else None
    text = transcribed_message(transcript)
    # Si ya había caption/texto y la transcripción falló, conservá el texto.
    if transcript is None and plain and plain != VOICE_NOTE_PLACEHOLDER:
        return plain
    return text


def download_meta_media(media_id: str) -> tuple[bytes, str] | None:
    """Baja un media de WhatsApp Cloud API (audio del webhook)."""
    from app.services.whatsapp_cloud_service import whatsapp_config

    cfg = whatsapp_config()
    token = cfg.get("access_token") or ""
    if not token or not media_id:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=30.0) as client:
            meta = client.get(
                f"https://graph.facebook.com/{cfg['api_version']}/{media_id}", headers=headers
            )
            if meta.status_code != 200:
                return None
            info = meta.json() or {}
            url = str(info.get("url") or "")
            if not url:
                return None
            size = int(info.get("file_size") or 0)
            if size and size > MAX_AUDIO_BYTES:
                return None
            resp = client.get(url, headers=headers)
            if resp.status_code != 200 or len(resp.content) > MAX_AUDIO_BYTES:
                return None
            return resp.content, str(info.get("mime_type") or "audio/ogg")
    except Exception:
        logger.exception("meta media download failed media_id=%s", media_id)
        return None
