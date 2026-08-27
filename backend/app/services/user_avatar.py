"""Avatares de usuario — solo uso interno (UI / equipo), no outbound."""

from __future__ import annotations

import re
from pathlib import Path

from app.database.config import _data_dir as DATA_DIR

AVATAR_DIR = DATA_DIR / "avatars"
MAX_AVATAR_BYTES = 2 * 1024 * 1024
_ALLOWED_CT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"RIFF", ".webp"),  # WebP: RIFF....WEBP
)


def ensure_avatar_dir() -> Path:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    return AVATAR_DIR


def _ext_from_bytes(data: bytes, content_type: str | None) -> str | None:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            if ext == ".webp" and b"WEBP" not in data[:16]:
                continue
            return ext
    ct = (content_type or "").split(";")[0].strip().lower()
    return _ALLOWED_CT.get(ct)


def avatar_abs_path(avatar_key: str | None) -> Path | None:
    key = (avatar_key or "").strip().replace("\\", "/")
    if not key or ".." in key or key.startswith("/"):
        return None
    if not re.fullmatch(r"avatars/\d+\.(jpg|png|webp)", key):
        return None
    path = DATA_DIR / key
    if not path.is_file():
        return None
    return path


def save_user_avatar(*, user_id: int, data: bytes, content_type: str | None) -> str:
    if not data:
        raise ValueError("Archivo vacío.")
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError("La foto puede pesar hasta 2 MB.")
    ext = _ext_from_bytes(data, content_type)
    if not ext:
        raise ValueError("Usá JPG, PNG o WebP.")
    ensure_avatar_dir()
    # Limpia extensiones previas del mismo usuario.
    for old in AVATAR_DIR.glob(f"{int(user_id)}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    filename = f"{int(user_id)}{ext}"
    dest = AVATAR_DIR / filename
    dest.write_bytes(data)
    return f"avatars/{filename}"


def delete_user_avatar_files(avatar_key: str | None) -> None:
    path = avatar_abs_path(avatar_key)
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
    # Por las dudas, limpia cualquier archivo del patrón.
    uid = None
    if avatar_key:
        m = re.search(r"avatars/(\d+)\.", avatar_key)
        if m:
            uid = m.group(1)
    if uid:
        for old in AVATAR_DIR.glob(f"{uid}.*"):
            try:
                old.unlink()
            except OSError:
                pass
