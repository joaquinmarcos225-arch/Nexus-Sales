"""Mensajes mock cuando OpenAI devuelve rate limit (solo desarrollo)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

FALLBACK_MARKER = "[FALLBACK TEST]"


def is_openai_fallback_enabled() -> bool:
    explicit = (os.getenv("NEXUS_OPENAI_FALLBACK_ON_RATE_LIMIT") or "").strip().lower()
    if explicit in ("1", "true", "yes", "on"):
        return True
    if explicit in ("0", "false", "no", "off"):
        return False
    from app.services.testing_reset import is_testing_reset_enabled

    if is_testing_reset_enabled():
        return True
    from app.services import outreach_metrics as om

    cfg = om.outreach_simulation_config()
    return bool(cfg.get("sequence_testing_enabled"))


def _first_name(prospect: dict[str, Any]) -> str:
    name = (prospect.get("name") or "").strip()
    return name.split()[0] if name else "Hola"


def _sender_label(campaign: dict[str, Any]) -> str:
    sender = (campaign.get("sender_name") or "").strip()
    if sender:
        return sender.split()[0]
    return "Ana"


def _brand_label(campaign: dict[str, Any]) -> str:
    return (campaign.get("brand_name") or campaign.get("name") or "Nexus").strip()


def _product_name(product: dict[str, Any] | None) -> str:
    return (product or {}).get("name") or "Plataforma Nexus"


def _outcome_sentence(product: dict[str, Any] | None) -> str:
    vp = (product or {}).get("value_proposition") or ""
    vp = re.sub(r"\s+", " ", vp.strip())
    if vp and len(vp) >= 24:
        low = vp.lower()
        if "ayudamos" in low:
            return vp if vp.endswith(".") else f"{vp}."
        return f"ayudamos a equipos comerciales a {vp.rstrip('.').lower()}"
    return "equipos comerciales a contactar más prospectos en menos tiempo"


def _build_day1_sections(
    *,
    prospect: dict[str, Any],
    campaign: dict[str, Any],
    product: dict[str, Any] | None,
) -> dict[str, str]:
    first = _first_name(prospect)
    sender = _sender_label(campaign)
    brand = _brand_label(campaign)
    product_name = _product_name(product)
    outcome = _outcome_sentence(product)
    role = (prospect.get("role") or prospect.get("selling_to_role") or "tu rol").strip()

    problem = f"Te escribo porque ayudamos a {outcome}."
    solution = (
        f"Lo hacemos mediante {product_name}, que automatiza la búsqueda y el contacto "
        f"por Mail, WhatsApp y LinkedIn desde un solo lugar."
    )
    benefits = (
        "Esto les permite reducir el trabajo manual de prospección y dedicar más tiempo "
        "a conversaciones reales."
    )
    cta = "¿Te interesaría coordinar una reunión breve para mostrarte cómo funciona?"

    return {
        "greeting": f"Hola {first},",
        "presentation": f"Soy {sender} de {brand}.",
        "problem": problem,
        "solution": solution,
        "benefits": benefits,
        "cta": cta,
        "_role": role,
        "_product_name": product_name,
    }


def _build_day1_internal(sections: dict[str, str]) -> dict[str, str]:
    return {
        "probable_problem": (
            "reducir el trabajo manual de prospección y dedicar más tiempo a conversaciones reales"
        ),
        "why_it_matters": sections["problem"],
        "hypothesis": sections["solution"],
        "response_question": sections["cta"],
        "selling_to_role": sections.get("_role") or "",
    }


def _build_followup_body(
    *,
    step_day: int,
    channel: str,
    prospect: dict[str, Any],
    product: dict[str, Any] | None,
) -> str:
    first = _first_name(prospect)
    product_name = _product_name(product)

    if step_day == 4:
        return (
            f"Hola {first},\n"
            "Te había escrito hace unos días por email porque ayudamos a equipos a contactar "
            "más prospectos en menos tiempo.\n"
            "¿Sos la persona indicada para evaluar este tema o debería hablar con alguien más del equipo?"
        )
    if step_day == 7:
        return (
            f"Hola {first}.\n"
            "Retomo mis mensajes anteriores para no insistir por distintos canales sin sentido.\n"
            "¿Tiene sentido seguir conversando sobre este tema o preferís que lo deje para más adelante?"
        )
    if step_day == 10:
        return (
            f"Hola {first},\n\n"
            f"Retomo el contacto porque vimos que equipos similares lograron más oportunidades comerciales "
            f"al centralizar outreach en una sola herramienta como {product_name}.\n\n"
            "Si te sirve, puedo compartirte en una charla breve cómo lo están aplicando."
        )
    if step_day == 13:
        return (
            f"Hola {first},\n"
            "Retomo mis mensajes anteriores sobre prospección comercial.\n"
            "¿Está en agenda para este año o preferís que lo dejemos para más adelante?"
        )
    if step_day == 16:
        return (
            f"Hola {first}.\n"
            "Retomo mis mensajes anteriores por este canal.\n"
            "Último intento por acá: ¿seguimos la conversación sobre outreach o preferís que lo dejemos por ahora?"
        )
    if step_day == 19:
        return (
            f"Hola {first},\n\n"
            "Retomo brevemente porque no tuve respuesta a mis mensajes anteriores.\n"
            "Cierro esta conversación por ahora y quedo a disposición si más adelante "
            "tiene sentido retomar el tema con calma.\n\n"
            "Saludos"
        )

    return (
        f"Hola {first},\n"
        "Retomo mis mensajes anteriores para ver si tiene sentido seguir conversando sobre este tema."
    )


def build_sdr_playbook_fallback_json(
    *,
    channel: str,
    prospect: dict[str, Any],
    product: dict[str, Any] | None,
    step_day: int,
    step_objective: str,
    campaign: dict[str, Any] | None = None,
    prior_touches: list[dict[str, Any]] | None = None,
) -> str:
    from app.services.lead_sourcing.sdr_playbook_outreach import _assemble_first_touch_body

    campaign = campaign or {}
    prior = prior_touches or []
    first_touch = not prior

    if first_touch:
        sections = _build_day1_sections(
            prospect=prospect, campaign=campaign, product=product
        )
        internal = _build_day1_internal(sections)
        body = _assemble_first_touch_body(
            {k: sections[k] for k in ("greeting", "presentation", "problem", "solution", "benefits", "cta")}
        )
        product_name = sections["_product_name"]
        payload: dict[str, Any] = {
            "internal": internal,
            "sections": {
                k: sections[k]
                for k in ("greeting", "presentation", "problem", "solution", "benefits", "cta")
            },
            "body": body,
        }
        if channel == "email":
            payload["subject"] = product_name.split()[0] if product_name else "Seguimiento comercial"
        return json.dumps(payload, ensure_ascii=False)

    body = _build_followup_body(
        step_day=step_day,
        channel=channel,
        prospect=prospect,
        product=product,
    )
    role = (prospect.get("role") or prospect.get("selling_to_role") or "").strip()
    internal = {
        "probable_problem": _product_name(product),
        "why_it_matters": f"Seguimiento Día {step_day}: {step_objective or 'retomar conversación'}",
        "hypothesis": "",
        "response_question": body.split("\n")[-1].strip() if body else "¿Tiene sentido seguir conversando?",
        "selling_to_role": role,
    }
    payload = {"internal": internal, "body": body}
    if channel == "email":
        payload["subject"] = "Seguimiento"
    return json.dumps(payload, ensure_ascii=False)


def apply_fallback_marker_to_body(body: str) -> str:
    text = (body or "").strip()
    if not text:
        return FALLBACK_MARKER
    if text.startswith(FALLBACK_MARKER):
        return text
    return f"{FALLBACK_MARKER}\n\n{text}"


def build_generic_fallback_text(*, system_prompt: str, user_prompt: str) -> str:
    sp = system_prompt or ""
    up = user_prompt or ""

    try:
        from app.services.meeting_slot_parser import parse_meeting_slot

        if parse_meeting_slot(up) is not None:
            return apply_fallback_marker_to_body(
                "Perfecto, recibí tu propuesta de horario. "
                "Estoy confirmando la reunión y te comparto el enlace en un momento."
            )
    except Exception:
        pass

    if "JSON" in sp and "objection" in sp:
        return json.dumps(
            {
                "objection": "none",
                "interest": "medium",
                "wants_meeting": False,
                "explicit_meeting_commitment": False,
                "asks_questions": "?" in up,
                "brushoff": False,
                "prospect_timing_hold": False,
                "defer_resume_at": None,
            },
            ensure_ascii=False,
        )

    if '"subject"' in sp and '"body"' in sp:
        return json.dumps(
            {
                "subject": "Seguimiento",
                "body": apply_fallback_marker_to_body(
                    "Hola, gracias por tu mensaje. Con gusto profundizamos en una llamada breve "
                    "cuando te quede cómodo."
                ),
            },
            ensure_ascii=False,
        )

    return apply_fallback_marker_to_body(
        "Hola, gracias por tu mensaje. Coordinemos una charla breve cuando te quede cómodo."
    )
