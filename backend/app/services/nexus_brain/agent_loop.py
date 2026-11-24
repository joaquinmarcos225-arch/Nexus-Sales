"""Runtime del agente orquestador (OpenAI Responses + tools → especialistas)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_AGENT_ROUNDS = 4

_ORCHESTRATOR_SYSTEM = """Sos el Brain orquestador de Nexus Sales (SDR B2B).
Delegás a especialistas vía tools. NO redactes vos el mensaje final largo:
usá draft_reply / research_competitor / resolve_scheduling y cerrá con finish.

Regla #1 (innegociable):
- Leé TODO el mensaje del prospecto de punta a punta antes de decidir.
- No te quedes con la primera palabra ni con un «Dale/Ok/Perfecto» inicial.
- Si hay saludo + pregunta + pedido, la pregunta/pedido mandan el turno.

Perímetro (si Policy no cortó antes):
- Sin secuencia iniciada, o pausada por el SDR, o reunión ya agendada:
  finish(action=skip_autoresponder). Cero research/draft/agenda. Sacá el foco.
- Solo cuentas/hilos Nexus (nosotros escribimos primero). Nunca inventes
  trabajo sobre chats, mails o WA ajenos.

Otras reglas:
- Si el prospecto hace una pregunta concreta (piloto vs en uso, cómo funciona,
  clientes, integración, etc.) y HAY dato en contexto: draft_reply contestando eso.
  action=normal_reply. NO llames resolve_scheduling solo para empujar agenda.
- Precio/cobro / «necesito el número antes de agendar»: SOLO draft_reply.
  NO llames research_competitor. «antes de agendar» NO es un producto llamado Agendar.
  Sin número en contexto: NO inventes planes ni “por usuario”. Honestidad + call;
  si insiste: sin cifra inventada y pedí día/franja.
- Si el prospecto nombra una herramienta real («ya usamos Apollo», «vs HubSpot»),
  llamá research_competitor. Nunca inventes un competidor desde un verbo (agendar).
- Si habla de horarios, reunión, demo, agenda o link de calendar (y no está priorizando
  una pregunta de producto), llamá resolve_scheduling.
- Para respuesta comercial normal, draft_reply.
- Policy ya corrió: no hace falta revalidar silencio.
- Cerrá SIEMPRE con finish(action, reply_body, reply_objective, notes).
- action ∈ normal_reply|booked|alternatives|offer_hours|calendar_link|ask_email|ask_slot|booking_failed|canceled|escalate|skip_autoresponder
- Español, tono humano SDR. No digas que sos IA. No inventes clientes ni hechos.
- Si preguntan si sos una IA / bot / ChatGPT: NEGÁ con claridad («Jaj no, te escribo yo») y seguí el hilo comercial. Nunca digas «hablando del ángulo IA» ni deferís esa pregunta a una llamada.
"""


def brain_agent_enabled() -> bool:
    raw = (os.getenv("NEXUS_BRAIN_AGENT") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _tool_schemas() -> list[dict[str, Any]]:
    # Responses API: function tools en formato plano (name/description/parameters).
    return [
        {
            "type": "function",
            "name": "research_competitor",
            "description": (
                "Investiga qué es una herramienta/competidor mencionada por el prospecto "
                "y arma contraste vs el producto de la campaña."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_hint": {
                        "type": "string",
                        "description": "Nombre del tool si ya lo sabés; vacío si hay que detectarlo.",
                    }
                },
                "required": ["competitor_hint"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "resolve_scheduling",
            "description": (
                "Especialista Booker: intenta agendar, ofrecer horarios, pedir mail o link de Calendar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "suggested_reply": {
                        "type": "string",
                        "description": "Borrador previo opcional para mezclar con agenda.",
                    }
                },
                "required": ["suggested_reply"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "draft_reply",
            "description": "Especialista Writer: redacta respuesta comercial para el canal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "competitor|schedule|question|reject|followup|other",
                    },
                    "research_contrast": {
                        "type": "string",
                        "description": "Texto de contraste si research ya corrió.",
                    },
                },
                "required": ["intent", "research_contrast"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "finish",
            "description": (
                "Cierra el turno. Preferí reply_body de draft_reply/research/scheduling; "
                "no inventes hechos del producto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "normal_reply|booked|alternatives|offer_hours|calendar_link|"
                            "ask_email|ask_slot|booking_failed|canceled|escalate|skip_autoresponder"
                        ),
                    },
                    "reply_body": {"type": "string"},
                    "reply_objective": {"type": "string"},
                    "response_class": {"type": "string"},
                    "notes": {"type": "string"},
                    "handoff_reason": {"type": "string"},
                },
                "required": [
                    "action",
                    "reply_body",
                    "reply_objective",
                    "response_class",
                    "notes",
                    "handoff_reason",
                ],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]


def _extract_function_calls(response: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in getattr(response, "output", None) or []:
        typ = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if typ != "function_call":
            continue
        if isinstance(item, dict):
            calls.append(
                {
                    "name": item.get("name") or "",
                    "arguments": item.get("arguments") or "{}",
                    "call_id": item.get("call_id") or item.get("id") or "",
                }
            )
        else:
            calls.append(
                {
                    "name": getattr(item, "name", "") or "",
                    "arguments": getattr(item, "arguments", None) or "{}",
                    "call_id": getattr(item, "call_id", None)
                    or getattr(item, "id", None)
                    or "",
                }
            )
    return calls


def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def run_orchestrator_agent_loop(
    *,
    user_brief: str,
    tool_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    max_rounds: int = MAX_AGENT_ROUNDS,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """
    Loop agente: OpenAI elige tools → ejecutamos especialistas → finish.
    Devuelve (finish_payload | None, trace).
    """
    from app.services.openai_service import MODEL_INBOUND, _client, openai_configured

    if not openai_configured():
        return None, [{"event": "skip_agent", "reason": "openai_not_configured"}]

    client = _client()
    tools = _tool_schemas()
    trace: list[dict[str, Any]] = []
    input_items: list[dict[str, Any]] = [
        {"role": "system", "content": _ORCHESTRATOR_SYSTEM},
        {"role": "user", "content": user_brief},
    ]
    finish_payload: dict[str, Any] | None = None

    for round_i in range(max(1, int(max_rounds))):
        try:
            response = client.responses.create(
                model=MODEL_INBOUND,
                input=input_items,
                tools=tools,
                temperature=0.2,
                max_output_tokens=900,
            )
        except Exception as exc:
            logger.warning("nexus brain agent round=%s failed: %s", round_i, type(exc).__name__)
            trace.append({"event": "agent_error", "round": round_i, "error": str(exc)[:240]})
            break

        calls = _extract_function_calls(response)
        if not calls:
            # Sin tools: no aceptar prosa libre del modelo como reply final
            # (los especialistas gap-fill arman el body con criterio).
            text = (getattr(response, "output_text", None) or "").strip()
            trace.append({"event": "no_tool_calls", "round": round_i, "text": text[:200]})
            break

        # Append model output items so the API keeps tool-call context.
        for raw_item in getattr(response, "output", None) or []:
            if hasattr(raw_item, "model_dump"):
                input_items.append(raw_item.model_dump())
            elif isinstance(raw_item, dict):
                input_items.append(raw_item)

        for call in calls:
            name = (call.get("name") or "").strip()
            args = _parse_args(call.get("arguments"))
            call_id = call.get("call_id") or ""
            trace.append({"event": "tool_call", "round": round_i, "name": name, "args": args})

            if name == "finish":
                finish_payload = args
                trace.append({"event": "finish", "payload_keys": list(args.keys())})
                return finish_payload, trace

            handler = tool_handlers.get(name)
            if handler is None:
                result = {"ok": False, "error": f"unknown_tool:{name}"}
            else:
                try:
                    result = handler(args) or {}
                except Exception as exc:
                    logger.exception("nexus brain tool=%s failed", name)
                    result = {"ok": False, "error": str(exc)[:240]}

            trace.append(
                {
                    "event": "tool_result",
                    "round": round_i,
                    "name": name,
                    "ok": result.get("ok", True),
                    "summary": {
                        k: result.get(k)
                        for k in (
                            "action",
                            "found",
                            "competitor",
                            "source",
                            "reply_body",
                            "skip_reason",
                            "error",
                        )
                        if k in result
                    },
                }
            )
            # Truncate huge reply bodies in tool output fed back to the model.
            safe = dict(result)
            rb = safe.get("reply_body")
            if isinstance(rb, str) and len(rb) > 1200:
                safe["reply_body"] = rb[:1200] + "…"
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(safe, ensure_ascii=False)[:4000],
                }
            )

    return finish_payload, trace
