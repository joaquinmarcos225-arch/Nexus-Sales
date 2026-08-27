/**
 * Pure helpers mirrored from content-whatsapp.js (smoke without Chrome DOM).
 * Run: node browser-extension/wa-inbound-helpers.test.mjs
 */

function normalizeWaCompare(s) {
  return String(s || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

function namesMatchStrong(a, b) {
  const na = normalizeWaCompare(a).replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim()
  const nb = normalizeWaCompare(b).replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim()
  if (!na || !nb) return false
  if (na === nb) return true
  if (na.length >= 5 && nb.length >= 5 && (na.includes(nb) || nb.includes(na))) return true
  const pa = na.split(' ').filter((p) => p.length >= 2)
  const pb = nb.split(' ').filter((p) => p.length >= 2)
  if (pa.length >= 2 && pb.length >= 2) {
    const setB = new Set(pb)
    const overlap = pa.filter((p) => setB.has(p)).length
    if (overlap >= 2) return true
  }
  return false
}

function namesMatchLoose(a, b) {
  if (namesMatchStrong(a, b)) return true
  const pa = normalizeWaCompare(a)
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter((p) => p.length >= 3)
  const pb = normalizeWaCompare(b)
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter((p) => p.length >= 3)
  if (!pa.length || !pb.length) return false
  return pa.some((p) =>
    pb.some((q) => p === q || (p.length >= 4 && q.startsWith(p)) || (q.length >= 4 && p.startsWith(q))),
  )
}

function cleanWaPreviewText(raw) {
  let t = String(raw || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!t) return ''
  t = t.replace(/^[\u2713\u2714✓✔]+\s*/g, '')
  t = t.replace(/^(t[uú]|you|vos)\s*:\s*/i, '')
  t = t.replace(/^draft\s*:\s*/i, '')
  t = t.replace(/\s+/g, ' ').trim()
  if (
    /^(foto|photo|imagen|image|gif|sticker|audio|video|documento|document|contact card|tarjeta de contacto|gif omitido|multimedia omitido|mensaje eliminado|this message was deleted)$/i.test(
      t,
    )
  ) {
    return ''
  }
  return t
}

function extractInboundAfterOurOutbound(ordered) {
  if (!ordered.length) return []
  let lastOut = -1
  for (let i = 0; i < ordered.length; i += 1) {
    if (ordered[i].kind === 'out') lastOut = i
  }
  if (ordered[ordered.length - 1]?.kind === 'out') return []
  const out = []
  if (lastOut < 0) {
    const last = ordered[ordered.length - 1]
    if (last?.kind === 'in' && last.text) {
      out.push({ text: last.text.slice(0, 500), phone: last.phone || '' })
    }
    return out
  }
  for (const row of ordered.slice(lastOut + 1)) {
    if (row.kind !== 'in' || !row.text) continue
    out.push({ text: row.text.slice(0, 500), phone: row.phone || '' })
  }
  return out.slice(-3)
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

assert(namesMatchStrong('Juan Perez', 'Juan Pérez'), 'accent/name match')
assert(namesMatchStrong('Maria Lopez', 'María López'), 'full name overlap')
assert(!namesMatchStrong('Ana', 'Anabela'), 'short first name alone should not match')
assert(namesMatchLoose('Ivan Braga', 'Ivan'), 'loose Ivan')
assert(namesMatchStrong('Ivan Braga', 'Iván Braga'), 'accent Ivan')
assert(cleanWaPreviewText('Tú: hola che') === 'hola che', 'strip Tu:')
assert(cleanWaPreviewText('✓✓ Ok') === 'Ok', 'strip checks')
assert(cleanWaPreviewText('Foto') === '', 'drop media placeholder')

function isTextEchoOfOutbound(text, ours) {
  const t = String(text || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .replace(/^(t[uú]|you|vos)\s*:\s*/i, '')
  const o = String(ours || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
  if (!t) return true
  if (/^t[uú]:/i.test(t) || /^you:/i.test(t) || /^vos:/i.test(t)) return true
  if (!o || o.length < 8) return false
  if (t === o) return true
  const n = Math.min(48, t.length, o.length)
  if (n >= 16 && (t.slice(0, n) === o.slice(0, n) || t.includes(o.slice(0, n)) || o.includes(t.slice(0, n)))) {
    return true
  }
  if (t.length >= 16 && o.includes(t)) return true
  if (t.includes(o.slice(0, 50)) && t.length >= Math.min(o.length, 30)) return true
  if (o.includes(t) && t.length >= 20) return true
  return false
}

const sampleOut =
  'Hola Ivan, te escribo porque me pareció interesante lo que hacen en tu equipo. ¿Tenés 15 min esta semana?'
assert(!isTextEchoOfOutbound('Dale, mañana a las 15', sampleOut), 'short reply not echo')
assert(!isTextEchoOfOutbound('ok', sampleOut), 'ok not echo of long outbound')
assert(isTextEchoOfOutbound(sampleOut, sampleOut), 'full outbound is echo')
assert(isTextEchoOfOutbound('Tú: ' + sampleOut, sampleOut), 'Tu prefix is echo')

const msgs = [
  { kind: 'out', text: 'Hola, te escribo de Nexus' },
  { kind: 'in', text: 'Hola, me interesa' },
  { kind: 'in', text: '¿Mañana a las 15?' },
]
const got = extractInboundAfterOurOutbound(msgs)
assert(got.length === 2 && got[1].text.includes('15'), 'inbounds after outbound')
assert(extractInboundAfterOurOutbound([...msgs, { kind: 'out', text: 'Dale' }]).length === 0, 'no inbound if we spoke last')

console.log('wa-inbound-helpers.test.mjs: ok')

