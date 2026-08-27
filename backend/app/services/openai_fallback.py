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
    from app.services.outreach_display_names import prospect_greeting_name

    return prospect_greeting_name(prospect)


def resolve_selling_to_role(prospect: dict[str, Any]) -> str:
    role = (prospect.get("selling_to_role") or prospect.get("role") or "").strip()
    return role if len(role) >= 3 else "Decisor comercial"


def follow_up_response_question_from_body(body: str) -> str:
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    if not lines:
        return "¿Tiene sentido seguir conversando?"
    for line in reversed(lines):
        if "?" in line and len(line) >= 12:
            return line
    for line in reversed(lines):
        words = line.split()
        if len(words) <= 2 and "?" not in line and len(line) <= 28:
            continue
        if len(line) >= 12:
            return line
    return lines[-1]


def normalize_follow_up_internal(
    internal: dict[str, Any],
    *,
    body: str,
    prospect: dict[str, Any],
    step_day: int,
    step_objective: str,
) -> dict[str, Any]:
    out = dict(internal or {})
    if len(str(out.get("why_it_matters") or "").strip()) < 8:
        out["why_it_matters"] = (
            f"Seguimiento Día {step_day}: {step_objective or 'retomar conversación'}"
        )
    if len(str(out.get("response_question") or "").strip()) < 8:
        out["response_question"] = follow_up_response_question_from_body(body)
    if len(str(out.get("selling_to_role") or "").strip()) < 3:
        out["selling_to_role"] = resolve_selling_to_role(prospect)
    return out


def _sender_label(campaign: dict[str, Any]) -> str:
    from app.services.outreach_display_names import sender_first_name

    return sender_first_name(campaign_sender=campaign.get("sender_name"), fallback="Ana")


def _brand_label(campaign: dict[str, Any]) -> str:
    from app.services.outreach_display_names import outreach_company_display

    for key in ("brand_name", "company_name", "seller_company_name"):
        label = outreach_company_display(campaign.get(key))
        if label:
            return label
    return ""


def _product_name(product: dict[str, Any] | None, *, brand: str = "") -> str:
    name = ((product or {}).get("name") or "").strip()
    if name:
        from app.services.outreach_display_names import is_placeholder_name

        if not is_placeholder_name(name):
            return name
    return brand or "nuestra solución"


def _value_proposition_benefit(product: dict[str, Any] | None) -> str:
    """Valor usable en copy: reescrito, no pegado de la ficha."""
    from app.services.message_structure_variants import _rewrite_product_value

    name = ((product or {}).get("name") or "nuestra solución").strip() or "nuestra solución"
    blurb = _rewrite_product_value(product, product_name=name, channel="email")
    t = (blurb or "").strip().rstrip(".")
    if not t:
        return "automatizar la búsqueda y el contacto por email, LinkedIn y WhatsApp"
    # Tras «porque» va minúscula.
    return t[0].lower() + t[1:] if t else t


def _build_problem_line(product: dict[str, Any] | None) -> str:
    """Ángulo de por qué escribís — sin pegar VP ni audiencia fija."""
    benefit = _value_proposition_benefit(product)
    return f"Te escribo porque {benefit}."


def _outcome_sentence(product: dict[str, Any] | None) -> str:
    return _value_proposition_benefit(product)


def _build_day1_sections(
    *,
    prospect: dict[str, Any],
    campaign: dict[str, Any],
    product: dict[str, Any] | None,
    channel: str = "linkedin",
) -> dict[str, str]:
    """Primer contacto: una de 3 variantes automáticas (bloques grandes)."""
    from app.services.message_structure_variants import (
        build_first_touch_sections,
        pick_first_touch_variant,
    )

    variant = pick_first_touch_variant(
        channel=channel,
        prospect_id=prospect.get("id"),
        campaign_id=campaign.get("id") or campaign.get("campaign_id"),
    )
    return build_first_touch_sections(
        channel=channel,
        variant=variant,
        prospect=prospect,
        campaign=campaign,
        product=product,
    )


def _build_day1_internal(sections: dict[str, str]) -> dict[str, str]:
    return {
        "probable_problem": sections.get("problem")
        or "reducir tiempo manual de prospección y agendar más reuniones",
        "why_it_matters": sections["presentation"],
        "hypothesis": sections["solution"],
        "response_question": sections["cta"],
        "selling_to_role": sections.get("_role") or "",
    }


def _greet(first: str, *, suffix: str = ",") -> str:
    first = (first or "").strip()
    return f"Hola {first}{suffix}" if first else f"Hola{suffix}"


def _build_followup_body(
    *,
    step_day: int,
    channel: str,
    prospect: dict[str, Any],
    product: dict[str, Any] | None,
    campaign: dict[str, Any] | None = None,
) -> str:
    from app.services.message_structure_variants import (
        build_follow_up_body,
        pick_follow_up_variant,
    )

    campaign = campaign or {}
    variant = pick_follow_up_variant(
        channel=channel,
        prospect_id=prospect.get("id"),
        campaign_id=campaign.get("id") or campaign.get("campaign_id"),
        step_day=step_day,
    )
    return build_follow_up_body(
        channel=channel,
        variant=variant,
        prospect=prospect,
        campaign=campaign,
        product=product,
        step_day=step_day,
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
    """Fallback = mismo banco determinístico (sin inventar industria)."""
    from app.services.cold_message_bank import first_touch_on_channel, render_cold_bank_touch

    campaign = campaign or {}
    prior = prior_touches or []
    first_on_ch = first_touch_on_channel(prior, channel)
    rendered = render_cold_bank_touch(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product,
        prior_touches=prior,
        first_touch=first_on_ch,
        step_day=step_day,
    )
    role = resolve_selling_to_role(prospect)
    internal = {
        "probable_problem": (rendered.reasoning.probable_problem or "")[:400],
        "why_it_matters": (rendered.reasoning.why_it_matters or step_objective or "")[:400],
        "hypothesis": (rendered.reasoning.hypothesis or "")[:400],
        "response_question": (rendered.reasoning.response_question or "")[:300],
        "selling_to_role": role,
    }
    if not first_on_ch:
        internal = normalize_follow_up_internal(
            internal,
            body=rendered.body,
            prospect=prospect,
            step_day=step_day,
            step_objective=step_objective,
        )
    payload: dict[str, Any] = {"internal": internal, "body": rendered.body}
    if channel == "email":
        payload["subject"] = rendered.subject or "Seguimiento"
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
