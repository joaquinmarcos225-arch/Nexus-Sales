"""Playbook MVP — un toque por vez según día, canal y contexto previo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

Channel = Literal["email", "linkedin", "whatsapp"]


@dataclass(frozen=True)
class PlaybookStepDef:
    day: int
    channel: Channel
    objective: str


# Playbook por defecto hasta que el SDR configure playbooks completos.
DEFAULT_MVP_PLAYBOOK: tuple[PlaybookStepDef, ...] = (
    PlaybookStepDef(
        day=1,
        channel="email",
        objective="Día 1 — Email: explicar qué hacemos, qué resultado genera y pedir reunión (único pitch completo).",
    ),
    PlaybookStepDef(
        day=4,
        channel="linkedin",
        objective="Día 4 — LinkedIn: seguimiento humano. Referenciar Día 1. NO re-vender el producto. Preguntar si es la persona indicada o a quién hablar.",
    ),
    PlaybookStepDef(
        day=7,
        channel="whatsapp",
        objective="Día 7 — WhatsApp: contacto rápido. Referenciar mensajes previos. ¿Seguir conversando o dejarlo para más adelante? Sin explicar producto.",
    ),
    PlaybookStepDef(
        day=10,
        channel="email",
        objective="Día 10 — Email: aportar valor (dato, caso o aprendizaje). NO repetir pitch. Invitación suave a conversar.",
    ),
    PlaybookStepDef(
        day=13,
        channel="linkedin",
        objective="Día 13 — LinkedIn: nuevo ángulo (timing/prioridad). ¿Está en agenda este año o no es prioridad ahora?",
    ),
    PlaybookStepDef(
        day=16,
        channel="whatsapp",
        objective="Día 16 — WhatsApp: último intento humano. Referenciar historial. ¿Seguir o dejarlo? Sin explicar producto.",
    ),
    PlaybookStepDef(
        day=19,
        channel="email",
        objective="Día 19 — Email: ruptura elegante. Cerrar sin vender, sin explicar, sin insistir. Puerta abierta.",
    ),
)


def openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def lead_available_channels(
    *,
    email: str | None,
    linkedin_url: str | None,
    phone: str | None,
    whatsapp_number: str | None,
) -> set[Channel]:
    from app.services.lead_sourcing.linkedin_identity import is_personal_linkedin_url

    channels: set[Channel] = set()
    if (email or "").strip() and "@" in (email or ""):
        channels.add("email")
    if is_personal_linkedin_url(linkedin_url):
        channels.add("linkedin")
    if (whatsapp_number or phone or "").strip():
        channels.add("whatsapp")
    return channels


def resolve_next_playbook_step(
    playbook_state: dict[str, Any] | None,
    available: set[Channel],
    *,
    playbook: tuple[PlaybookStepDef, ...] = DEFAULT_MVP_PLAYBOOK,
) -> PlaybookStepDef | None:
    """Próximo paso del playbook según toques completados y canales del lead."""
    state = playbook_state if isinstance(playbook_state, dict) else {}
    if state.get("paused"):
        return None
    completed = state.get("completed") if isinstance(state.get("completed"), list) else []
    done_keys = {
        (int(t.get("day") or 0), str(t.get("channel") or ""))
        for t in completed
        if isinstance(t, dict)
    }
    for step in playbook:
        key = (step.day, step.channel)
        if key in done_keys:
            continue
        if step.channel not in available:
            continue
        return step
    return None


def playbook_step_for_channel(
    channel: Channel,
    *,
    playbook: tuple[PlaybookStepDef, ...] = DEFAULT_MVP_PLAYBOOK,
) -> PlaybookStepDef | None:
    """Primer paso del playbook para un canal (testing por canal individual)."""
    for step in playbook:
        if step.channel == channel:
            return step
    return None


def playbook_steps_for_preview(
    available: set[Channel],
    *,
    playbook: tuple[PlaybookStepDef, ...] = DEFAULT_MVP_PLAYBOOK,
) -> list[tuple[PlaybookStepDef, bool, str | None]]:
    """Lista ordenada de pasos con flag (generable, motivo si no)."""
    out: list[tuple[PlaybookStepDef, bool, str | None]] = []
    for step in playbook:
        if step.channel not in available:
            out.append(
                (
                    step,
                    False,
                    f"Canal {step.channel} no disponible para este lead.",
                )
            )
        else:
            out.append((step, True, None))
    return out


def prior_touches_for_testing_step(
    completed: list[dict[str, Any]],
    step: PlaybookStepDef,
) -> list[dict[str, Any]]:
    """Toques del historial real anteriores al paso que se prueba (contexto AI)."""
    return [
        t for t in completed
        if isinstance(t, dict) and int(t.get("day") or 0) < step.day
    ]


def touch_history_for_ai(completed: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for t in completed:
        if not isinstance(t, dict):
            continue
        body = (t.get("body") or "").strip()
        if not body:
            continue
        ch = t.get("channel") or "?"
        day = t.get("day") or "?"
        subj = (t.get("subject") or "").strip()
        prefix = f"[Día {day} · {ch}]"
        if subj:
            prefix += f" Asunto: {subj}"
        out.append({"role": "assistant", "content": f"{prefix}\n{body}"})
    return out
