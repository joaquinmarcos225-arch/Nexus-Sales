"""Repaso final del primer mensaje de cada canal: orden (presentación → valor → pedido de reunión) y redacción."""

from __future__ import annotations

import re

from app.services.cold_message_bank import strip_filler_phrases

_MEETING_WORDS_RE = re.compile(
    r"(?i)\b(reuni[oó]n|llamada|charla|videollamada|call|meet|coordin\w*|agend\w*|\d+\s+minutos|"
    r"lo\s+vemos|lo\s+charlamos|hablamos|un\s+rato|un\s+espacio|te\s+queda\s+bien)\b"
)
_SMALL_TALK_RE = re.compile(r"(?i)^¿\s*(c[oó]mo\s+(est[aá]s|and[aá]s|va|te\s+va)|qu[eé]\s+tal)\b[^?]*\?$")
_QUESTION_RE = re.compile(r"¿[^¿?]*\?")
_CLOSING_RE = re.compile(
    r"^\s*(?i:saludos|un saludo|abrazo|un abrazo|gracias|muchas gracias|slds|atte|atentamente|cordialmente|quedo atent)"
)
_GREETING_RE = re.compile(r"(?i)^(hola|buen d[ií]a|buenas|buenos d[ií]as|qu[ée] tal)\b")
_NAME_PAREN_RE = re.compile(
    r"(^|(?<=[.!?][ \t])|(?<=\n))([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?: [A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)?)[ \t]*\(([^()\n]{1,40})\)\."
)
_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_PENSE_RE = re.compile(r"^Pens[eé] en\b", re.I)
_PENSE_HAS_REASON_RE = re.compile(r"(?i)\b(porque|ya que|dado que|para que|por eso)\b")
# Ganchos inventados: afirmar cómo trabajan / manejan X sin brief.
_INVENTED_OBS_RE = re.compile(
    r"(?i)"
    r"(?:,\s*|\s+y\s+|;\s*)?"
    r"(?:"
    r"me\s+llam[oó]\s+la\s+atenci[oó]n\s+(?:c[oó]mo|que)\s+"
    r"|me\s+pareci[oó]\s+(?:ver\s+)?c[oó]mo\s+"
    r"|vi\s+c[oó]mo\s+(?:manejan|gestionan|trabajan|hacen)\s+"
    r")"
    r"[^.?!\n]{6,180}"
)
_LOWERABLE_FIRST = {
    "con", "desde", "nosotros", "somos", "lo", "la", "el", "los", "las", "te", "nuestro", "nuestra",
    "creo", "me", "a", "en", "hoy", "muchos", "muchas", "varios", "varias", "justo", "estamos", "ahí",
    "ayudo", "es", "son", "hay", "sé", "un", "una",
}


def _mergeable_after_pense(target: str) -> bool:
    m = re.match(r"([A-ZÁÉÍÓÚÑa-záéíóúñ][\wáéíóúñ]*)", target)
    if not m:
        return False
    word = m.group(1)
    if word.lower() in {"quería", "vi", "veo", "tengo", "estoy", "trabajo"}:
        return False
    return True


def _is_meeting_question(q: str) -> bool:
    q = q.strip()
    return bool(q) and not _SMALL_TALK_RE.match(q) and bool(_MEETING_WORDS_RE.search(q))


def _lower_first_word(text: str) -> str:
    m = re.match(r"([A-ZÁÉÍÓÚÑ][\wáéíóúñ]*)", text)
    if not m:
        return text
    word = m.group(1)
    if word.lower() in _LOWERABLE_FIRST or re.fullmatch(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+amos", word):
        return word.lower() + text[len(word):]
    return text


def _fix_name_in_parens(text: str) -> str:
    return _NAME_PAREN_RE.sub(lambda m: f"{m.group(1)}Soy {m.group(2)}, de {m.group(3).strip()}.", text)


def _tidy_line(line: str) -> str:
    out = re.sub(r"[ \t]{2,}", " ", line).strip()
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"\.{2,}", ".", out)
    out = re.sub(r",\.", ".", out)
    return out


def _paragraphs(text: str) -> list[list[str]]:
    paras: list[list[str]] = []
    cur: list[str] = []
    for ln in text.split("\n"):
        if ln.strip():
            cur.append(ln)
        elif cur:
            paras.append(cur)
            cur = []
    if cur:
        paras.append(cur)
    return paras


def _join(paras: list[list[str]]) -> str:
    return "\n\n".join("\n".join(p) for p in paras if p)


def _closing_start(paras: list[list[str]]) -> int:
    """Índice del primer párrafo de despedida/firma (o len)."""
    for i, p in enumerate(paras):
        first = p[0].strip()
        if _CLOSING_RE.match(first):
            return i
        if i > 0 and len(p) == 1 and len(first.split()) <= 2 and not re.search(r"[.?!:]$", first):
            return i
    return len(paras)


def _substantive(line: str) -> bool:
    return "?" not in line and len(line.split()) >= 9


def _move_early_meeting_ask(text: str) -> str:
    paras = _paragraphs(text)
    end = _closing_start(paras)
    body, tail = paras[:end], paras[end:]
    flat = [(pi, li) for pi, p in enumerate(body) for li in range(len(p))]
    moved: list[str] = []
    for idx, (pi, li) in enumerate(flat):
        line = body[pi][li]
        later_substance = any(_substantive(body[p2][l2]) for p2, l2 in flat[idx + 1 :])
        if not later_substance:
            continue
        for q in _QUESTION_RE.findall(line):
            if _is_meeting_question(q):
                line = line.replace(q, "", 1)
                moved.append(q.strip())
        body[pi][li] = _tidy_line(line)
    if not moved:
        return text
    body = [[ln for ln in p if ln.strip() and not re.fullmatch(r"[,;:]+", ln.strip())] for p in body]
    body = [p for p in body if p]
    last_lines = [ln for p in body[-1:] for ln in p]
    has_final_ask = any(_is_meeting_question(q) for ln in last_lines for q in _QUESTION_RE.findall(ln))
    if not has_final_ask:
        q = moved[0]
        q = q[0] + q[1:2].upper() + q[2:] if q.startswith("¿") else q
        last = body[-1] if body else []
        if last and "?" not in last[-1] and _MEETING_WORDS_RE.search(last[-1]) and len(last[-1].split()) <= 14:
            last[-1] = q
        else:
            body.append([q])
    return _join(body + tail)


def _capitalize_greeting_name(text: str) -> str:
    return re.sub(
        r"^((?i:hola|buen d[ií]a|buenas|buenos d[ií]as)[ \t]+)([a-záéíóúñ])",
        lambda m: m.group(1) + m.group(2).upper(),
        text,
        count=1,
    )


def _split_sentences(line: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.findall(line) if s.strip()]


def _dedupe_te_escribo(text: str) -> str:
    def fix_line(line: str) -> str:
        sents = _split_sentences(line)
        for i in range(1, len(sents)):
            if sents[i - 1].lower().startswith("te escribo") and sents[i].lower().startswith("te escribo"):
                s = sents[i]
                s = re.sub(r"(?i)^te escribo pensando en", "Pensé en", s)
                s = re.sub(r"(?i)^te escribo por", "Pensé en vos por", s)
                sents[i] = s
        return " ".join(sents) if sents else line

    return "\n".join(fix_line(ln) for ln in text.split("\n"))


def _complete_pense_en(text: str) -> str:
    """«Pensé en X.» suelto → «Pensé en X ya que …» con la frase siguiente (si no es pregunta)."""
    paras = _paragraphs(text)
    for pi in range(len(paras)):
        for li in range(len(paras[pi])):
            sents = _split_sentences(paras[pi][li])
            for si, s in enumerate(sents):
                if not _PENSE_RE.match(s) or _PENSE_HAS_REASON_RE.search(s) or not s.endswith("."):
                    continue
                nxt = None
                if si + 1 < len(sents):
                    nxt = ("same", si + 1)
                elif li + 1 < len(paras[pi]):
                    nxt = ("line", li + 1)
                elif pi + 1 < len(paras):
                    nxt = ("para", pi + 1)
                if nxt is None:
                    continue
                kind, k = nxt
                if kind == "same":
                    target = sents[k]
                else:
                    src = paras[pi][k] if kind == "line" else paras[k][0]
                    tsents = _split_sentences(src)
                    target = tsents[0] if tsents else ""
                if (
                    not target
                    or "?" in target
                    or _GREETING_RE.match(target)
                    or _CLOSING_RE.match(target)
                    or not _mergeable_after_pense(target)
                ):
                    continue
                merged = f"{s[:-1].rstrip()} ya que {_lower_first_word(target)}"
                if kind == "same":
                    sents[si] = merged
                    del sents[k]
                    paras[pi][li] = " ".join(sents)
                else:
                    sents[si] = merged
                    paras[pi][li] = " ".join(sents)
                    if kind == "line":
                        rest = paras[pi][k][len(target):].strip() if paras[pi][k].startswith(target) else ""
                        paras[pi][k] = rest
                    else:
                        rest = paras[k][0][len(target):].strip() if paras[k][0].startswith(target) else ""
                        paras[k][0] = rest
                return _complete_pense_en(_join([[ln for ln in p if ln.strip()] for p in paras]))
    return _join(paras)


def _dedupe_meeting_questions(text: str) -> str:
    """Como máximo un pedido de reunión; prioriza el que trae día/hora."""
    paras = _paragraphs(text)
    end = _closing_start(paras)
    body, tail = paras[:end], paras[end:]
    found: list[tuple[int, int, str]] = []
    for pi, p in enumerate(body):
        for li, line in enumerate(p):
            for q in _QUESTION_RE.findall(line):
                if _is_meeting_question(q):
                    found.append((pi, li, q.strip()))
    if len(found) <= 1:
        return text
    preferred = found[-1]
    for item in found:
        if re.search(
            r"(?i)\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|\d{1,2}:\d{2})\b",
            item[2],
        ):
            preferred = item
            break
    keep_q = preferred[2]
    for pi, li, q in found:
        line = body[pi][li]
        body[pi][li] = _tidy_line(line.replace(q, "", 1))
    body = [[ln for ln in p if ln.strip()] for p in body]
    body = [p for p in body if p]
    q = keep_q
    if q.startswith("¿") and len(q) > 2:
        q = "¿" + q[1:2].upper() + q[2:]
    body.append([q])
    return _join(body + tail)


def _structure_first_touch_paragraphs(text: str) -> str:
    """Saludo | presentación/valor | CTA | (firma). Evita muro de un solo párrafo."""
    paras = _paragraphs(text)
    if not paras:
        return text
    end = _closing_start(paras)
    body, tail = paras[:end], paras[end:]
    flat_lines = [ln.strip() for p in body for ln in p if ln.strip()]
    if not flat_lines:
        return _join(tail) if tail else text

    greeting = ""
    rest_lines = flat_lines
    if _GREETING_RE.match(flat_lines[0]) or flat_lines[0].lower().startswith("hola"):
        greeting = flat_lines[0]
        rest_lines = flat_lines[1:]
        if rest_lines and _SMALL_TALK_RE.match(rest_lines[0]):
            if "¿" not in greeting:
                greeting = f"{greeting.rstrip(',.')} , {rest_lines[0]}".replace(" ,", ",")
            rest_lines = rest_lines[1:]
        # «¿cómo estás? Soy X…» pegados en la misma línea.
        elif rest_lines:
            m_st = re.match(
                r"(?i)^(¿\s*(?:c[oó]mo\s+(?:est[aá]s|and[aá]s|va|te\s+va)|qu[eé]\s+tal)\s*\?)\s+(.*)$",
                rest_lines[0],
            )
            if m_st:
                if "¿" not in greeting:
                    greeting = f"{greeting.rstrip(',.')} , {m_st.group(1)}".replace(" ,", ",")
                rest_lines = [m_st.group(2).strip()] + rest_lines[1:]

    cta = ""
    content: list[str] = []
    for ln in rest_lines:
        qs = [q for q in _QUESTION_RE.findall(ln) if _is_meeting_question(q)]
        if qs:
            cta = qs[-1].strip()
            remnant = ln
            for q in qs:
                remnant = remnant.replace(q, "", 1)
            remnant = _tidy_line(remnant)
            if remnant:
                content.append(remnant)
        else:
            content.append(ln)

    out: list[list[str]] = []
    if greeting:
        out.append([_tidy_line(greeting)])
    if content:
        soy_idx = next(
            (i for i, ln in enumerate(content) if re.match(r"(?i)^soy\b", ln.strip())),
            None,
        )
        if soy_idx is not None and len(content) > soy_idx + 1:
            out.append([content[soy_idx]])
            out.append([" ".join(content[soy_idx + 1 :])])
        elif len(content) == 1:
            line = content[0]
            m = re.match(r"(?i)^(soy\s+[^.]{2,60}\.)\s+(.*)$", line)
            if m and len(m.group(2)) > 40:
                out.append([m.group(1).strip()])
                out.append([m.group(2).strip()])
            else:
                out.append([line])
        else:
            out.append([content[0]])
            if len(content) > 1:
                out.append([" ".join(content[1:])])
    if cta:
        if cta.startswith("¿") and len(cta) > 2:
            cta = "¿" + cta[1:2].upper() + cta[2:]
        out.append([cta])
    return _join(out + tail)


def strip_invented_observation_hooks(body: str | None) -> str:
    """Saca observaciones inventadas («me llamó la atención cómo manejan…») sin brief."""
    text = (body or "").replace("\r\n", "\n")
    if not text.strip():
        return text.strip()
    text = _INVENTED_OBS_RE.sub("", text)
    # «Vi que sos X y .» / «Vi que sos X, .» tras el recorte
    text = re.sub(r"(?i)(vi que sos[^.?\n]{3,80}?)\s*(?:,|\by)\s*[.]", r"\1.", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +\.", ".", text)
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def polish_first_touch(body: str | None) -> str:
    """Idempotente. Solo para el primer mensaje del canal (en respuestas el pedido arriba puede ser válido)."""
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text
    text = _capitalize_greeting_name(_fix_name_in_parens(text))
    text = "\n".join(strip_filler_phrases(ln) if ln.strip() else "" for ln in text.split("\n"))
    text = strip_invented_observation_hooks(text)
    text = _dedupe_te_escribo(text)
    text = _complete_pense_en(text)
    text = _move_early_meeting_ask(text)
    text = _dedupe_meeting_questions(text)
    text = _structure_first_touch_paragraphs(text)
    text = "\n".join(_tidy_line(ln) for ln in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
