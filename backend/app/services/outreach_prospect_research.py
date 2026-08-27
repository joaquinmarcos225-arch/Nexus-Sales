"""
Investiga prospecto + empresa (web) antes del primer mensaje outbound.
No scrapea LinkedIn logueado: usa búsqueda web + síntesis con IA.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.product import Product
from app.models.prospect import Prospect

logger = logging.getLogger(__name__)

ResearchDepth = Literal["skip", "crm", "light", "full"]

RESEARCH_START = "[NEXUS_OUTREACH_RESEARCH]"
RESEARCH_END = "[/NEXUS_OUTREACH_RESEARCH]"
_MAX_SNIPPETS = 5
_MAX_BRIEF_CHARS = 1200
# Techo real de research (web + síntesis): no bloquear el compose >~25s.
_RESEARCH_WALL_SEC = 22.0
_RESEARCH_QUERY_LIMIT = 2
_RESEARCH_HITS_PER_QUERY = 3


def extract_stored_research(notes: str | None) -> str:
    text = notes or ""
    m = re.search(
        re.escape(RESEARCH_START) + r"(.*?)" + re.escape(RESEARCH_END),
        text,
        flags=re.DOTALL,
    )
    if not m:
        return ""
    return (m.group(1) or "").strip()


def _upsert_research_notes(prospect: Prospect, brief: str) -> None:
    brief = (brief or "").strip()
    if not brief:
        return
    block = f"{RESEARCH_START}\n{brief[:_MAX_BRIEF_CHARS]}\n{RESEARCH_END}"
    notes = (prospect.notes or "").strip()
    if RESEARCH_START in notes:
        notes = re.sub(
            re.escape(RESEARCH_START) + r".*?" + re.escape(RESEARCH_END),
            block,
            notes,
            flags=re.DOTALL,
        ).strip()
    else:
        notes = f"{block}\n{notes}".strip() if notes else block
    prospect.notes = notes


def _research_depth_env_override() -> ResearchDepth | None:
    raw = (os.getenv("NEXUS_RESEARCH_DEPTH") or "").strip().lower()
    if raw in {"skip", "crm", "light", "full"}:
        return raw  # type: ignore[return-value]
    return None


def _research_brave_escalate_enabled() -> bool:
    explicit = (os.getenv("NEXUS_RESEARCH_ESCALATE_BRAVE") or "").strip().lower()
    if explicit in ("0", "false", "no", "off"):
        return False
    if explicit in ("1", "true", "yes", "on"):
        return True
    return True  # default: escalar solo si vale la pena (ver _worth_deep_research)


def _worth_deep_research(prospect: Prospect | None, campaign: Campaign | None) -> bool:
    if prospect is None or campaign is None:
        return False
    score = int(getattr(prospect, "compatibility_score", 0) or 0)
    if score >= 72:
        return True
    try:
        from app.services.campaign_sequence_channels import campaign_requires_whatsapp

        if campaign_requires_whatsapp(campaign):
            return True
    except Exception:
        pass
    return False


def resolve_research_depth(
    *,
    day: int,
    prior_touches: list[dict[str, Any]] | None = None,
    has_stored_brief: bool = False,
    force: bool = False,
    prospect: Prospect | None = None,
    campaign: Campaign | None = None,
) -> ResearchDepth:
    """
    Investigación progresiva: solo en primer compose (día 1, sin toques previos).
    skip → crm → light (cache/fetch) → full (Brave) solo si light vacío y vale la pena.
    """
    env = _research_depth_env_override()
    if env is not None:
        return env
    if force:
        return "full"
    if has_stored_brief:
        return "skip"
    prior = prior_touches or []
    if any(isinstance(t, dict) and int(t.get("day") or 0) >= 1 for t in prior):
        return "skip"
    if int(day) > 1:
        return "skip"
    return "light"


def _crm_only_brief(
    prospect: Prospect,
    campaign: Campaign,
    product: Product | None,
) -> str:
    product_name = (product.name if product else "") or "nuestra solución"
    name = prospect.name or "contacto"
    role = prospect.role or ""
    company = prospect.company_name or "—"
    return (
        f"Contacto {name} ({role}) @ {company}. "
        f"Producto {product_name}. Personalizá con rol/empresa sin inventar. "
        "dato no confirmado"
    )


def _collect_web_snippets(
    prospect: Prospect,
    campaign: Campaign,
    product: Product | None,
    *,
    deadline: float | None = None,
    db: Session | None = None,
    allow_brave: bool = True,
) -> list[str]:
    import time

    try:
        from app.services.lead_sourcing.providers.web_search_backends import (
            configured_backend,
            search_web,
        )
    except Exception:
        return []

    if not configured_backend():
        return []

    name = (prospect.name or "").strip()
    company = (prospect.company_name or "").strip()
    role = (prospect.role or "").strip()
    product_name = (product.name if product else "") or ""
    mode = (getattr(campaign, "outreach_mode", None) or "b2b").strip().lower()
    country = prospect.country or campaign.target_country

    # B2B: reusar snippets de la misma empresa entre prospectos (TTL 7d).
    cache_key = None
    if db is not None and mode != "b2c" and company:
        try:
            from app.services.nexus_research_cache import (
                get_research_payload,
                outreach_snippets_cache_key,
            )

            cache_key = outreach_snippets_cache_key(
                mode=mode, company_name=company, country=str(country) if country else None
            )
            if cache_key:
                cached = get_research_payload(db, cache_key)
                if isinstance(cached, list) and cached:
                    return [str(s) for s in cached if str(s).strip()][:_MAX_SNIPPETS]
        except Exception:
            cache_key = None

    # B2B: fetch directo al sitio si hay dominio (Paso G — sin Brave).
    if mode != "b2c" and company:
        try:
            from app.services.lead_sourcing.nexus_public_fetch import (
                fetch_company_page_signals,
                resolve_domain_hint,
                signals_to_snippet_lines,
            )

            dom = resolve_domain_hint(prospect.company_website)
            if db is not None and not dom:
                from app.services.nexus_contact_cache import find_company_domain_by_name

                hit = find_company_domain_by_name(db, company)
                if hit:
                    dom = hit[0]
            if dom:
                sig = fetch_company_page_signals(dom)
                if sig is not None:
                    own_lines = signals_to_snippet_lines(sig, company_name=company)
                    if own_lines:
                        if db is not None:
                            try:
                                from app.services.nexus_contact_cache import remember_company_domain

                                remember_company_domain(
                                    db,
                                    name=company,
                                    domain=dom,
                                    website_url=sig.url,
                                    industry=(sig.industry_hint or None),
                                    source_provider="nexus_fetch",
                                )
                            except Exception:
                                pass
                            if cache_key:
                                try:
                                    from app.services.nexus_research_cache import (
                                        KIND_OUTREACH_SNIPPETS,
                                        set_research_payload,
                                    )

                                    set_research_payload(
                                        db,
                                        cache_key=cache_key,
                                        kind=KIND_OUTREACH_SNIPPETS,
                                        payload=own_lines,
                                    )
                                except Exception:
                                    pass
                        return own_lines[:_MAX_SNIPPETS]
        except Exception:
            pass

    if not allow_brave:
        return []

    queries: list[str] = []
    if mode == "b2c":
        if name:
            queries.append(f"{name} {role} {company}".strip())
            queries.append(f"{name} LinkedIn")
    else:
        if company:
            queries.append(f"{company} empresa qué hace")
            if product_name:
                queries.append(f"{company} {product_name}")
        if name and company:
            queries.append(f"{name} {company} {role} LinkedIn".strip())
        elif name:
            queries.append(f"{name} {role} LinkedIn".strip())

    snippets: list[str] = []
    seen: set[str] = set()
    for q in queries[:_RESEARCH_QUERY_LIMIT]:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if not q.strip():
            continue
        try:
            hits = search_web(
                q,
                limit=_RESEARCH_HITS_PER_QUERY,
                country=country,
            )
        except Exception:
            logger.debug("outreach research search failed query=%r", q, exc_info=True)
            continue
        for hit in hits or []:
            # SearchHit = (url, title, snippet)
            if isinstance(hit, (tuple, list)) and len(hit) >= 3:
                url, title, snippet = str(hit[0] or ""), str(hit[1] or ""), str(hit[2] or "")
            else:
                url = str(getattr(hit, "url", "") or "")
                title = str(getattr(hit, "title", "") or "")
                snippet = str(getattr(hit, "snippet", "") or "")
            line = " — ".join(p for p in (title.strip(), snippet.strip(), url.strip()) if p)
            key = line[:180].lower()
            if not line or key in seen:
                continue
            seen.add(key)
            snippets.append(line[:420])
            if len(snippets) >= _MAX_SNIPPETS:
                break
        if len(snippets) >= _MAX_SNIPPETS:
            break

    if db is not None and cache_key and snippets and mode != "b2c":
        try:
            from app.services.nexus_research_cache import (
                KIND_OUTREACH_SNIPPETS,
                set_research_payload,
            )

            set_research_payload(
                db,
                cache_key=cache_key,
                kind=KIND_OUTREACH_SNIPPETS,
                payload=snippets,
            )
        except Exception:
            pass
    return snippets


def _research_openai_synth_enabled() -> bool:
    """Síntesis IA del brief: opt-in (default off — plantilla + snippets)."""
    return (os.getenv("NEXUS_RESEARCH_OPENAI_SYNTH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _deterministic_research_brief(
    *,
    prospect: Prospect,
    campaign: Campaign,
    product: Product | None,
    snippets: list[str],
) -> str:
    mode = (getattr(campaign, "outreach_mode", None) or "b2b").strip().lower()
    product_name = (product.name if product else "") or "—"
    name = prospect.name or "contacto"
    company = prospect.company_name or "—"
    role = prospect.role or "—"
    industry = prospect.industry or "—"
    head = (
        f"Modo {mode}. Contacto: {name} ({role}) @ {company}. "
        f"Industria: {industry}. Producto: {product_name}."
    )
    if not snippets:
        return (
            f"{head}\n"
            "Gancho: dato no confirmado (usar rol/empresa CRM).\n"
            "Ángulo producto: anclar al valor del producto sin inventar métricas."
        )
    body = "\n".join(f"- {s}" for s in snippets[:5])
    return (
        f"{head}\n"
        "Hallazgos web (usar solo evidencia explícita; si no hay gancho claro: dato no confirmado):\n"
        f"{body}"
    )


def _synthesize_brief(
    *,
    prospect: Prospect,
    campaign: Campaign,
    product: Product | None,
    snippets: list[str],
) -> str:
    from app.services.openai_service import openai_configured, _raw_chat_with_meta

    mode = (getattr(campaign, "outreach_mode", None) or "b2b").strip().lower()
    product_name = (product.name if product else "") or ""
    product_vp = ((product.value_proposition if product else "") or "")[:400]
    product_desc = ((product.description if product else "") or "")[:400]
    name = prospect.name or "contacto"
    company = prospect.company_name or ""
    role = prospect.role or ""
    industry = prospect.industry or ""
    li = (prospect.linkedin_url or "").strip()

    if not _research_openai_synth_enabled() or not openai_configured():
        return _deterministic_research_brief(
            prospect=prospect,
            campaign=campaign,
            product=product,
            snippets=snippets,
        )

    audience = (
        "venta B2C: el gancho debe hablar de la PERSONA (rol, trayectoria, interés público, LinkedIn). "
        "No inventes hobbies ni logros sin evidencia."
        if mode == "b2c"
        else "venta B2B: el gancho debe hablar de la EMPRESA del prospecto (qué hace, crecimiento, foco, equipo) "
        "y encajar con su rol. No inventes métricas ni news sin evidencia."
    )
    try:
        from app.services.outreach_display_names import company_brand_name

        seller_brand = company_brand_name(campaign) or (campaign.name or "marca")
    except Exception:
        seller_brand = campaign.name or "marca"
    system = (
        "Sos un SDR researcher. Con datos web ruidosos, elaborás un BRIEF corto y accionable "
        "para personalizar el primer mensaje outbound. No inventes hechos: si no hay evidencia, "
        "escribí exactamente 'dato no confirmado'. "
        "IMPORTANTE: la marca que VENDE (remitente) NO es la empresa del prospecto; no los confundas. "
        "Máximo 140 palabras. Español neutro. Sin markdown."
    )
    user = (
        f"Modo campaña: {mode}. {audience}\n"
        f"Marca vendedora (NO es el prospecto): {seller_brand}\n"
        f"Prospecto: {name} | rol: {role or '—'} | empresa del prospecto: {company or '—'} | industria: {industry or '—'}\n"
        f"LinkedIn URL: {li or '—'}\n"
        f"Producto a vender: {product_name}\nValor: {product_vp}\nDesc: {product_desc}\n\n"
        f"Snippets web (pueden ser ruidosos; descartá los que hablen de la marca vendedora como si fuera el empleador del prospecto):\n"
        + ("\n".join(f"- {s}" for s in snippets) if snippets else "- (sin resultados web)")
        + "\n\n"
        "Devolvé SOLO el brief con estas líneas:\n"
        "1) Quién es / qué hace (persona)\n"
        "2) Contexto empresa del prospecto (si B2B) o motivador persona (si B2C)\n"
        "3) Gancho sugerido (1 frase usable en el saludo, SOLO con evidencia; si no hay: 'dato no confirmado')\n"
        "4) Ángulo producto↔ellos (1 frase concreta: problema sectorial o beneficio anclado al producto; "
        "PROHIBIDO inventar % o métricas que no estén en la ficha del producto)\n"
        "5) Dato usable extra (cargo, vertical, señal pública) o 'dato no confirmado'\n"
    )
    try:
        chat = _raw_chat_with_meta(
            system,
            user,
            temperature=0.3,
            max_output_tokens=280,
            fallback_factory=lambda: (
                f"Contacto {name} ({role}) @ {company or '—'}. "
                f"Producto {product_name}. Usá rol/empresa del CRM; sin inventar."
            ),
        )
        text = (chat.text or "").strip()
        return text[:_MAX_BRIEF_CHARS]
    except Exception:
        logger.exception("outreach research synthesize failed prospect_id=%s", prospect.id)
        return (
            f"Contacto {name} ({role}) @ {company or '—'}. "
            f"Producto {product_name}. Personalizá con rol/empresa sin inventar."
        )


def ensure_outreach_research(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    product: Product | None,
    force: bool = False,
    depth: ResearchDepth | None = None,
    prior_touches: list[dict[str, Any]] | None = None,
    day: int = 1,
) -> str:
    """
    Asegura un brief de investigación en notes y lo devuelve.
    Idempotente: reusa brief existente salvo force=True.
    Profundidad progresiva: no Brave/OpenAI en batch/import — solo al compose.
    """
    import time

    existing = extract_stored_research(prospect.notes)
    if depth is None:
        depth = resolve_research_depth(
            day=day,
            prior_touches=prior_touches,
            has_stored_brief=bool(existing),
            force=force,
            prospect=prospect,
            campaign=campaign,
        )
    if depth == "skip":
        try:
            from app.services.lead_sourcing.cogs_runtime_metrics import (
                record_research_skipped,
            )

            record_research_skipped()
        except Exception:
            pass
        return existing or ""

    if existing and not force:
        return existing

    crm_brief = _crm_only_brief(prospect, campaign, product)
    if depth == "crm":
        _upsert_research_notes(prospect, crm_brief)
        try:
            db.flush()
        except Exception:
            logger.debug("flush research notes failed", exc_info=True)
        return crm_brief

    deadline = time.monotonic() + _RESEARCH_WALL_SEC
    allow_brave = depth == "full"
    snippets = _collect_web_snippets(
        prospect,
        campaign,
        product,
        deadline=deadline,
        db=db,
        allow_brave=allow_brave,
    )
    if (
        depth == "light"
        and not snippets
        and _research_brave_escalate_enabled()
        and _worth_deep_research(prospect, campaign)
    ):
        snippets = _collect_web_snippets(
            prospect,
            campaign,
            product,
            deadline=deadline,
            db=db,
            allow_brave=True,
        )
    if time.monotonic() >= deadline:
        # Techo: no esperar OpenAI; compose usa CRM (+ snippets crudos si hay).
        if snippets:
            brief = crm_brief + "\nSeñales web: " + "; ".join(s[:120] for s in snippets[:3])
        else:
            brief = crm_brief
    else:
        brief = _synthesize_brief(
            prospect=prospect,
            campaign=campaign,
            product=product,
            snippets=snippets,
        ) or crm_brief
        if not _research_openai_synth_enabled():
            try:
                from app.services.lead_sourcing.cogs_runtime_metrics import (
                    record_openai_skipped_trivial,
                )

                record_openai_skipped_trivial()
            except Exception:
                pass
    if brief:
        _upsert_research_notes(prospect, brief)
        try:
            db.flush()
        except Exception:
            logger.debug("flush research notes failed", exc_info=True)
    return brief


def research_context_for_prompt(prospect: Prospect) -> str:
    brief = extract_stored_research(prospect.notes)
    if not brief:
        return ""
    return (
        "INVESTIGACIÓN PREVIA (obligatorio usarla para el GANCHO del primer mensaje; "
        "no inventes de más; si dice 'dato no confirmado', usá empresa/rol CRM):\n"
        f"{brief}\n"
    )
