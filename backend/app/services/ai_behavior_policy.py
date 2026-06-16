"""Política de comportamiento del SDR IA — configurable desde Educación IA."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_instruction import AIInstruction
from app.schemas.ai_behavior_policy import AiBehaviorPolicyRead, AiBehaviorPolicyUpdate
from app.services.conversation_intelligence import fold_accents, inbound_wants_immediate_booking

BEHAVIOR_INSTRUCTION_TITLE = "Nexus · Comportamiento SDR (sistema)"

_CALENDAR_ASK_RE = re.compile(
    r"\b("
    r"agendemos|agendamos|coordinemos|coordinamos|reservemos|reservamos|"
    r"pasame\s+(el\s+)?(link|calendario|horario|agenda)|"
    r"tenes\s+link|tienes\s+link|tenés\s+link|"
    r"link\s+de\s+(reunion|agenda|calendario)|"
    r"calendario|horarios?\s+disponibles?|disponibilidad|"
    r"me\s+interesa\s+verlo|quiero\s+verlo|demo|videollamada|"
    r"cuando\s+(te\s+)?(va|queda|conviene)"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AiBehaviorPolicy:
    calendar_link: str = "on_request_only"
    commercial_aggressiveness: str = "low"
    cta_frequency: str = "rare"
    response_length: str = "medium"
    technical_level: str = "balanced"
    consultative_priority: str = "value_first"
    meeting_push: str = "minimal"
    follow_up_style: str = "warm"
    formality: str = "neutral"
    humor: str = "professional"
    personality: str = "professional"
    followup_cadence: str = "standard"

    @classmethod
    def from_schema(cls, row: AiBehaviorPolicyRead) -> AiBehaviorPolicy:
        return cls(**row.model_dump())

    def to_schema(self) -> AiBehaviorPolicyRead:
        return AiBehaviorPolicyRead.model_validate(asdict(self))


DEFAULT_POLICY = AiBehaviorPolicy()


def is_behavior_system_instruction(title: str | None) -> bool:
    return (title or "").strip() == BEHAVIOR_INSTRUCTION_TITLE


def prospect_requests_calendar_link(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if inbound_wants_immediate_booking(raw):
        return True
    t = fold_accents(raw)
    if _CALENDAR_ASK_RE.search(t):
        return True
    if "link" in t and any(x in t for x in ("reunion", "agenda", "calendario", "meet", "horario")):
        return True
    return False


def resolve_booking_priority_for_reply(
    policy: AiBehaviorPolicy,
    *,
    inbound_text: str,
    explicit_meeting_commitment: bool = False,
    prospect_wants_meeting: bool = False,
    interest_level: str | None = None,
) -> bool:
    """Alta intención de agenda: solo cuando corresponde según política + señales."""
    text = (inbound_text or "").strip()
    if not text:
        return False
    if prospect_requests_calendar_link(text):
        return True
    intr = (interest_level or "low").lower()
    mode = policy.calendar_link
    if mode == "never":
        return False
    if explicit_meeting_commitment or inbound_wants_immediate_booking(text):
        return True
    if mode == "on_request_only":
        return False
    if mode == "high_intent_only":
        return bool(prospect_wants_meeting and intr in ("high", "medium"))
    if mode == "soft_suggestion":
        return bool(prospect_wants_meeting and intr in ("high", "medium", "low"))
    return False


def should_inject_calendar_link(
    policy: AiBehaviorPolicy,
    *,
    calendar_url: str,
    inbound_text: str,
    timing_soft: bool,
    booking_priority: bool,
    interest_level: str | None = None,
    prospect_wants_meeting: bool = False,
    explicit_meeting_commitment: bool = False,
    substantive_questions: bool = False,
) -> tuple[bool, bool]:
    """
    Devuelve (incluir_link, obligatorio_en_cuerpo).
    obligatorio = el modelo debe poner el link; incluir=False → prohibido en este turno.
    """
    cal = (calendar_url or "").strip()
    if not cal or timing_soft:
        return False, False

    if booking_priority:
        return True, True

    mode = policy.calendar_link
    if mode == "never":
        return False, False

    if prospect_requests_calendar_link(inbound_text):
        return True, True

    if substantive_questions and policy.consultative_priority == "value_first":
        return False, False

    intr = (interest_level or "low").lower()
    if mode == "on_request_only":
        return False, False

    if mode == "high_intent_only":
        if explicit_meeting_commitment and intr in ("high", "medium"):
            return True, False
        if prospect_wants_meeting and intr == "high":
            return True, False
        return False, False

    if mode == "soft_suggestion":
        if policy.meeting_push == "minimal":
            return False, False
        if intr in ("high", "medium") and policy.meeting_push in ("soft", "assertive"):
            return True, False
        return False, False

    return False, False


def behavior_prompt_section(policy: AiBehaviorPolicy) -> str:
    """Bloque estructurado para el system prompt (sin JSON crudo)."""
    cal_rules = {
        "never": "No incluyas link de calendario salvo que el prospecto lo pida explícitamente en este mensaje.",
        "on_request_only": (
            "Link de calendario: SOLO si el prospecto pidió agendar, link, horarios o hay intención clara de reunión. "
            "En mensajes informativos o con preguntas: respondé primero, sin link."
        ),
        "high_intent_only": (
            "Link de calendario: solo con intención clara de reunión o interés alto; nunca en cada mensaje."
        ),
        "soft_suggestion": (
            "Podés sugerir reunión al final sin presión si hay interés; no repitas CTA ni metas link en cada mail."
        ),
    }
    agg = {
        "low": "Tono consultivo, sin presión comercial.",
        "medium": "Comercial equilibrado.",
        "high": "Más directo hacia siguiente paso.",
    }
    cta = {
        "rare": "CTA comercial raro; priorizá valor.",
        "normal": "CTA ocasional al cierre.",
        "frequent": "Podés cerrar con CTA más seguido si encaja.",
    }
    length = {
        "short": "Respuestas cortas (3–4 líneas salvo preguntas complejas).",
        "medium": "Respuestas medias (4–6 líneas).",
        "detailed": "Podés extenderte si el prospecto pide detalle.",
    }
    tech = {
        "plain": "Lenguaje simple, poco jerga.",
        "balanced": "Técnico solo si aporta.",
        "technical": "Podés ser más técnico si el rol lo amerita.",
    }
    consult = {
        "value_first": "Siempre respondé la pregunta con valor antes de sugerir reunión.",
        "balanced": "Equilibrá valor y avance comercial.",
        "meeting_first": "Avanzá hacia reunión pero sin evadir preguntas.",
    }
    push = {
        "minimal": "Insistencia mínima en reunión.",
        "soft": "Cierre suave opcional hacia charla.",
        "assertive": "Cerrá con propuesta concreta de llamada cuando haya apertura.",
    }
    follow = {
        "warm": "Follow-ups cálidos y humanos.",
        "persistent": "Seguimiento firme pero respetuoso.",
        "direct": "Seguimiento directo y breve.",
    }
    formal = {
        "casual": "Tuteo y tono cercano si encaja.",
        "neutral": "Neutro profesional.",
        "formal": "Usted y registro formal.",
    }
    humor = {
        "professional": "Tono profesional, sin humor ni emojis salvo excepción.",
        "light": "Humor muy sutil permitido.",
        "relaxed": "Tono más relajado, sin perder B2B.",
    }
    persona = {
        "professional": "Personalidad profesional y confiable.",
        "friendly": "Personalidad cercana y empática.",
        "challenger": "Personalidad challenger: preguntas que hacen pensar.",
    }
    cadence = {
        "conservative": "Follow-ups espaciados; no insistir pronto.",
        "standard": "Cadencia estándar de seguimiento.",
        "aggressive": "Seguimientos más frecuentes si hay silencio.",
    }
    return (
        "\n\n[Comportamiento SDR — configurado en Educación IA]\n"
        f"- {cal_rules.get(policy.calendar_link, cal_rules['on_request_only'])}\n"
        f"- {agg.get(policy.commercial_aggressiveness, agg['low'])}\n"
        f"- {cta.get(policy.cta_frequency, cta['rare'])}\n"
        f"- {length.get(policy.response_length, length['medium'])}\n"
        f"- {tech.get(policy.technical_level, tech['balanced'])}\n"
        f"- {consult.get(policy.consultative_priority, consult['value_first'])}\n"
        f"- {push.get(policy.meeting_push, push['minimal'])}\n"
        f"- {follow.get(policy.follow_up_style, follow['warm'])}\n"
        f"- {formal.get(policy.formality, formal['neutral'])}\n"
        f"- {humor.get(policy.humor, humor['professional'])}\n"
        f"- {persona.get(policy.personality, persona['professional'])}\n"
        f"- {cadence.get(policy.followup_cadence, cadence['standard'])}\n"
    )


def _policy_json_content(policy: AiBehaviorPolicy) -> str:
    return json.dumps(asdict(policy), ensure_ascii=False, indent=2)


def _parse_policy_json(text: str) -> AiBehaviorPolicy | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    base = asdict(DEFAULT_POLICY)
    for key in base:
        if key in data and data[key] is not None:
            base[key] = str(data[key])
    if base.get("humor") in ("none", "playful"):
        base["humor"] = {"none": "professional", "playful": "relaxed"}[base["humor"]]
    try:
        validated = AiBehaviorPolicyRead.model_validate(base)
    except Exception:
        return DEFAULT_POLICY
    return AiBehaviorPolicy.from_schema(validated)


def load_behavior_policy(db: Session, company_id: int) -> AiBehaviorPolicy:
    row = db.scalar(
        select(AIInstruction).where(
            AIInstruction.company_id == company_id,
            AIInstruction.title == BEHAVIOR_INSTRUCTION_TITLE,
            AIInstruction.is_active.is_(True),
        )
    )
    if row is None:
        return DEFAULT_POLICY
    parsed = _parse_policy_json(row.content)
    return parsed or DEFAULT_POLICY


def save_behavior_policy(
    db: Session,
    company_id: int,
    payload: AiBehaviorPolicyUpdate,
) -> AiBehaviorPolicy:
    current = load_behavior_policy(db, company_id).to_schema()
    merged = payload.merge_into(current)
    policy = AiBehaviorPolicy.from_schema(merged)
    content = _policy_json_content(policy)
    row = db.scalar(
        select(AIInstruction).where(
            AIInstruction.company_id == company_id,
            AIInstruction.title == BEHAVIOR_INSTRUCTION_TITLE,
        )
    )
    if row is None:
        row = AIInstruction(
            company_id=company_id,
            title=BEHAVIOR_INSTRUCTION_TITLE,
            content=content,
            is_active=True,
        )
        db.add(row)
    else:
        row.content = content
        row.is_active = True
    db.commit()
    db.refresh(row)
    return policy


def resolve_booking_priority_from_signals(
    policy: AiBehaviorPolicy,
    *,
    inbound_text: str,
    explicit_meeting_commitment: bool = False,
    prospect_wants_meeting: bool = False,
    interest_level: str | None = None,
) -> bool:
    return resolve_booking_priority_for_reply(
        policy,
        inbound_text=inbound_text,
        explicit_meeting_commitment=explicit_meeting_commitment,
        prospect_wants_meeting=prospect_wants_meeting,
        interest_level=interest_level,
    )
