"""Traza manual del validador «cómo lo hacemos» — sin tocar prompts ni mensajes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.lead_sourcing import sdr_playbook_outreach as v

SAMPLE = (
    "Lo hacemos mediante Plataforma Nexus, que consolida prospectos, campañas y reporting "
    "en un solo lugar y automatiza el contacto por Mail, WhatsApp y LinkedIn."
)


def _regex_hits(pattern, text: str) -> list[str]:
    return [m.group(0) for m in pattern.finditer(text)]


def trace_text(field: str, text: str, *, how_context: str = "") -> dict:
    blob = f"{text} {how_context}".strip()
    out: dict = {
        "field": field,
        "text": text,
        "text_len": len(text),
        "how_context_len": len(how_context),
        "checks": [],
        "issues": [],
        "result": "PASS",
    }

    def fail(reason: str) -> None:
        out["checks"].append({"ok": False, "reason": reason})
        out["issues"].append(reason)
        out["result"] = "FAIL"

    def pass_(reason: str) -> None:
        out["checks"].append({"ok": True, "reason": reason})

    if not text.strip():
        fail("texto vacío")
        return out

    global_hits = _regex_hits(v._GLOBAL_BANNED, text)
    if global_hits:
        fail(f'_GLOBAL_BANNED matcheó: {global_hits}')
    else:
        pass_("_GLOBAL_BANNED: sin match")

    pain_hits = _regex_hits(v._PAIN_ASSUMPTION_BANNED, text)
    if pain_hits:
        fail(f'_PAIN_ASSUMPTION_BANNED matcheó: {pain_hits}')
    else:
        pass_("_PAIN_ASSUMPTION_BANNED: sin match")

    corp_hits = _regex_hits(v._GENERIC_CORPORATE, text)
    if corp_hits:
        fail(f'_GENERIC_CORPORATE matcheó: {corp_hits}')
    else:
        pass_("_GENERIC_CORPORATE: sin match")

    what_hits = _regex_hits(v._WHAT_WE_DO_MARKERS, blob)
    how_hits = _regex_hits(v._HOW_IT_WORKS_MARKERS, blob)
    mentions = v._mentions_how_we_do_it(text, how_context=how_context)

    pass_(
        f"_WHAT_WE_DO_MARKERS en blob: {what_hits or '(ninguno)'} | "
        f"_HOW_IT_WORKS_MARKERS en blob: {how_hits or '(ninguno)'} | "
        f"len(blob)={len(blob)}"
    )

    if not mentions:
        fail(
            "_mentions_how_we_do_it() -> False "
            "(requiere _WHAT_WE_DO_MARKERS en blob, O _HOW_IT_WORKS_MARKERS + len(blob)>=35)"
        )
    else:
        pass_("_mentions_how_we_do_it() -> True")

    accum = v._validate_solution_text(text, field)
    if accum.issues:
        for issue in accum.issues:
            fail(f"_validate_solution_text emitió: {issue}")
    else:
        pass_("_validate_solution_text: sin issues")

    return out


def trace_checklist(
    *,
    sections: dict,
    internal: dict,
    issues: list[str],
) -> dict:
    checklist, missing = v._build_first_touch_block_checklist(
        sections=sections, internal=internal, issues=issues
    )
    sol = next(c for c in checklist if c["key"] == "solution")
    targeted = [i for i in issues if v._issue_targets_block(i, "solution")]
    filtered = targeted.copy()
    solution_section = str(sections.get("solution") or "").strip()
    if solution_section and v._mentions_how_we_do_it(solution_section):
        import re as _re

        filtered = [
            i
            for i in filtered
            if not _re.search(r"internal\.hypothesis|body\.qu[eé]_hacemos", i, _re.I)
        ]

    return {
        "checklist_solution": sol,
        "all_issues": issues,
        "issues_targeting_solution_block": targeted,
        "issues_after_hypothesis_filter": filtered,
        "missing_blocks": missing,
        "block_result": "PASS" if sol["ok"] else "FAIL",
        "fail_reason": None if sol["ok"] else (sol.get("issue") or "sin contenido o bloque incumple"),
    }


def main() -> None:
    print("=" * 60)
    print("VALIDADOR «cómo lo hacemos» — traza manual")
    print("=" * 60)
    print("\nFunciones involucradas:")
    print("  - _validate_solution_text()      -> sections.solution / body.qué_hacemos")
    print("  - _validate_internal()           -> internal.hypothesis")
    print("  - _mentions_how_we_do_it()       -> decision PASS/FAIL de marcadores")
    print("  - _build_first_touch_block_checklist() -> checkmarks en UI")
    print("\nRegex _WHAT_WE_DO_MARKERS:")
    print(f"  {v._WHAT_WE_DO_MARKERS.pattern}")
    print("\nRegex _HOW_IT_WORKS_MARKERS:")
    print(f"  {v._HOW_IT_WORKS_MARKERS.pattern}")
    print("\nRegex _GENERIC_CORPORATE (bloquea si matchea):")
    print(f"  {v._GENERIC_CORPORATE.pattern[:120]}...")
    print("\nCondición _mentions_how_we_do_it(text):")
    print("  PASS si _WHAT_WE_DO_MARKERS.search(text+how_context)")
    print("  PASS si _HOW_IT_WORKS_MARKERS.search(blob) AND len(blob)>=35")
    print("  FAIL en caso contrario")

    print("\n" + "=" * 60)
    print("PRUEBA OBLIGATORIA — texto del usuario")
    print("=" * 60)
    print(SAMPLE)
    print()

    t1 = trace_text("sections.solution", SAMPLE)
    print(f"\n>>> sections.solution: {t1['result']}")
    for c in t1["checks"]:
        mark = "OK" if c["ok"] else "XX"
        print(f"  [{mark}] {c['reason']}")

    print("\n--- Simulación checklist (solo solution en sections, hypothesis vacío) ---")
    c1 = trace_checklist(
        sections={"solution": SAMPLE},
        internal={"hypothesis": ""},
        issues=["internal.hypothesis: debe explicar brevemente qué hacemos / cómo lo hacemos"],
    )
    print(f">>> block_checklist solution: {c1['block_result']} — {c1['fail_reason']}")
    print(json.dumps(c1, ensure_ascii=False, indent=2))

    print("\n--- Simulación checklist (issue en body.qué_hacemos con párrafo incorrecto) ---")
    c2 = trace_checklist(
        sections={"solution": SAMPLE},
        internal={"hypothesis": ""},
        issues=['body.qué_hacemos: debe explicar qué hacemos / cómo (ej. "Lo hacemos mediante…")'],
    )
    print(f">>> block_checklist solution: {c2['block_result']} — {c2['fail_reason']}")
    print(json.dumps(c2, ensure_ascii=False, indent=2))

    print("\n--- _validate_solution_text en párrafo equivocado (simula body mal partido) ---")
    wrong_para = "Esto les permite reducir el tiempo manual de prospección."
    t2 = trace_text("body.qué_hacemos", wrong_para, how_context=SAMPLE)
    print(f">>> body.qué_hacemos (párrafo benefits): {t2['result']}")


if __name__ == "__main__":
    main()
