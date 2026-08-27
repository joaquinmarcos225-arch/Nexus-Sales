/**
 * MAIN-world helper: leer inbound de chats vigilados vía Store interno de WA Web.
 * No abre chats, no clickea, no mueve foco.
 * Inyectado por background con chrome.scripting (world: MAIN).
 */
(() => {
  const READER_VER = 67
  if (globalThis.__NEXUS_WA_STORE_READER_VER__ === READER_VER) return
  globalThis.__NEXUS_WA_STORE_READER_VER__ = READER_VER
  globalThis.__NEXUS_WA_STORE_READER__ = true

  function digits(s) {
    return String(s || '').replace(/\D/g, '')
  }

  function phoneVariants(raw) {
    const x = digits(raw)
    const out = new Set()
    if (x.length >= 8) out.add(x)
    if (x.startsWith('549') && x.length >= 12) out.add(`54${x.slice(3)}`)
    if (x.startsWith('54') && !x.startsWith('549') && x.length >= 11) out.add(`549${x.slice(2)}`)
    if (x.length >= 10) out.add(x.slice(-10))
    return [...out]
  }

  function phonesMatch(a, b) {
    const va = phoneVariants(a)
    const vb = new Set(phoneVariants(b))
    if (!va.length || !vb.size) return false
    return va.some((x) => vb.has(x) || (x.length >= 10 && [...vb].some((y) => y.length >= 10 && y.slice(-10) === x.slice(-10))))
  }

  function normName(s) {
    return String(s || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, ' ')
      .replace(/\s+/g, ' ')
      .trim()
  }

  function namesMatch(a, b) {
    const na = normName(a)
    const nb = normName(b)
    if (!na || !nb) return false
    if (na === nb) return true
    if (na.length >= 5 && nb.length >= 5 && (na.includes(nb) || nb.includes(na))) return true
    const pa = na.split(' ').filter((p) => p.length >= 3)
    const pb = nb.split(' ').filter((p) => p.length >= 3)
    if (!pa.length || !pb.length) return false
    if (pa.length >= 2 && pb.length >= 2) {
      const setB = new Set(pb)
      if (pa.filter((p) => setB.has(p)).length >= 2) return true
    }
    return pa.some((p) => pb.some((q) => p === q))
  }

  function tryRequire(id) {
    try {
      if (typeof globalThis.require === 'function') return globalThis.require(id)
    } catch {
      /* ignore */
    }
    try {
      if (typeof globalThis.__d === 'function' && typeof globalThis.require === 'function') {
        return globalThis.require(id)
      }
    } catch {
      /* ignore */
    }
    return null
  }

  function getCollections(config) {
    const modules = Array.isArray(config?.storeModuleCandidates)
      ? config.storeModuleCandidates
      : [
          'WAWebCollections',
          'WAWebChatCollection',
          'WAWebContactCollection',
        ]

    for (const mid of modules) {
      if (mid === 'WAWebCollections' || mid.includes('Collections')) {
        const bundled = tryRequire(mid)
        if (bundled?.Chat) {
          return {
            Chat: bundled.Chat,
            Contact: bundled.Contact || null,
            Msg: bundled.Msg || null,
            source: mid,
          }
        }
      }
    }

    const chatMod =
      tryRequire('WAWebChatCollection')?.ChatCollection ||
      tryRequire('WAWebChatCollection')?.default ||
      tryRequire('WAWebChatCollection')
    const contactMod =
      tryRequire('WAWebContactCollection')?.ContactCollection ||
      tryRequire('WAWebContactCollection')?.default ||
      tryRequire('WAWebContactCollection')
    if (chatMod) {
      return { Chat: chatMod, Contact: contactMod, Msg: null, source: 'WAWebChatCollection' }
    }

    // Legacy window.Store (algunas builds / forks)
    try {
      const store = globalThis.Store
      if (store?.Chat) {
        return {
          Chat: store.Chat,
          Contact: store.Contact || null,
          Msg: store.Msg || null,
          source: 'window.Store',
        }
      }
    } catch {
      /* ignore */
    }

    // Webpack: escanear módulos cuando require(id) cambió de nombre.
    try {
      const req = globalThis.require
      const cache = req?.c || req?.m
      if (cache && typeof cache === 'object') {
        for (const mod of Object.values(cache)) {
          const exp = mod?.exports
          if (!exp || typeof exp !== 'object') continue
          if (exp.Chat && (exp.Contact || exp.Msg)) {
            return {
              Chat: exp.Chat,
              Contact: exp.Contact || null,
              Msg: exp.Msg || null,
              source: 'webpack-scan',
            }
          }
          const chat = exp.ChatCollection || exp.default?.ChatCollection
          if (chat) {
            return {
              Chat: chat,
              Contact: exp.ContactCollection || exp.default?.ContactCollection || null,
              Msg: exp.MsgCollection || exp.default?.MsgCollection || null,
              source: 'webpack-scan-chat',
            }
          }
        }
      }
    } catch {
      /* ignore */
    }
    return null
  }

  function modelsOf(col) {
    if (!col) return []
    try {
      if (typeof col.getModelsArray === 'function') return col.getModelsArray() || []
    } catch {
      /* ignore */
    }
    try {
      if (Array.isArray(col.models)) return col.models
    } catch {
      /* ignore */
    }
    try {
      if (col._models) return Object.values(col._models)
    } catch {
      /* ignore */
    }
    return []
  }

  function chatUserDigits(chat) {
    const id = chat?.id
    const ser = String(id?._serialized || id || '')
    // Solo @c.us / whatsapp.net tienen teléfono real; @lid no.
    const m = ser.match(/^(\d{8,15})@(?:c\.us|s\.whatsapp\.net)/i)
    if (m) return m[1]
    const user = digits(id?.user || '')
    if (user.length >= 8 && !/@lid/i.test(ser)) return user
    return ''
  }

  function chatTitle(chat) {
    return String(
      chat?.formattedTitle ||
        chat?.name ||
        chat?.contact?.name ||
        chat?.contact?.pushname ||
        chat?.contact?.verifiedName ||
        chat?.id?.user ||
        '',
    ).trim()
  }

  function contactPhones(contact) {
    const out = []
    if (!contact) return out
    const id = contact.id
    const ser = String(id?._serialized || '')
    const m = ser.match(/^(\d{8,15})@(?:c\.us|s\.whatsapp\.net)/i)
    if (m) out.push(m[1])
    const user = digits(id?.user || contact.userid || '')
    if (user.length >= 8) out.push(user)
    try {
      const pn = contact.phoneNumber || contact.phonenumber
      const d = digits(pn?.user || pn || '')
      if (d.length >= 8) out.push(d)
    } catch {
      /* ignore */
    }
    return out
  }

  function msgBody(msg) {
    if (!msg) return ''
    const t = String(msg.type || '')
    if (t && t !== 'chat' && t !== 'text') {
      // Ignorar media pura por ahora (paso 1 = texto).
      if (!msg.body && !msg.caption) return ''
    }
    const body = String(msg.body || msg.caption || msg.text || '')
      .replace(/\s+/g, ' ')
      .trim()
    return body.slice(0, 500)
  }

  function isFromMe(msg) {
    if (!msg) return false
    if (msg.id?.fromMe === true || msg.id?.fromMe === 1) return true
    if (msg.fromMe === true) return true
    return false
  }

  function msgsOfChat(chat) {
    try {
      if (chat?.msgs) return modelsOf(chat.msgs)
    } catch {
      /* ignore */
    }
    return []
  }

  async function ensureMsgsLoaded(chat) {
    let msgs = msgsOfChat(chat)
    if (msgs.length >= 2) return msgs
    const loaders = [
      'WAWebChatLoadMessages',
      'WAWebConversationMsgs',
      'WAWebChatGetMsgs',
    ]
    for (const id of loaders) {
      const mod = tryRequire(id)
      if (!mod) continue
      try {
        if (typeof mod.loadMessages === 'function') await mod.loadMessages(chat)
        else if (typeof mod.loadEarlierMsgs === 'function') await mod.loadEarlierMsgs(chat)
        else if (typeof mod.fetchMessages === 'function') await mod.fetchMessages(chat)
        else if (mod.default && typeof mod.default.loadMessages === 'function') {
          await mod.default.loadMessages(chat)
        }
      } catch {
        /* ignore */
      }
      msgs = msgsOfChat(chat)
      if (msgs.length) break
    }
    return msgs
  }

  function extractInboundAfterOut(msgs, outboundHint) {
    if (!Array.isArray(msgs) || !msgs.length) return []
    const ordered = msgs
      .map((m) => ({
        fromMe: isFromMe(m),
        text: msgBody(m),
        t: Number(m.t || m.timestamp || 0) || 0,
      }))
      .filter((m) => m.text)
      .sort((a, b) => a.t - b.t)

    if (!ordered.length) return []
    let lastOut = -1
    const hint = String(outboundHint || '')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase()
      .slice(0, 40)

    for (let i = 0; i < ordered.length; i += 1) {
      if (ordered[i].fromMe) lastOut = i
      if (hint && ordered[i].fromMe && ordered[i].text.toLowerCase().includes(hint.slice(0, 24))) {
        lastOut = i
      }
    }
    if (ordered[ordered.length - 1]?.fromMe) return []
    const slice = lastOut >= 0 ? ordered.slice(lastOut + 1) : ordered.slice(-2)
    return slice.filter((m) => !m.fromMe && m.text).map((m) => m.text).slice(-3)
  }

  /** Si hay unreadCount, tomar últimos inbound aunque el orden fromMe falle. */
  function extractByUnread(chat, msgs) {
    const unread = Number(chat?.unreadCount || chat?.unread_count || 0)
    if (unread < 1) return []
    const ordered = (msgs || [])
      .map((m) => ({
        fromMe: isFromMe(m),
        text: msgBody(m),
        t: Number(m.t || m.timestamp || 0) || 0,
      }))
      .filter((m) => m.text && !m.fromMe)
      .sort((a, b) => a.t - b.t)
    return ordered.slice(-Math.min(3, Math.max(1, unread))).map((m) => m.text)
  }

  function matchWatchToChat(watch, chat, contactsByPhone) {
    const wp = digits(watch?.phone)
    const chatPhone = chatUserDigits(chat)
    if (wp.length >= 8 && chatPhone && phonesMatch(wp, chatPhone)) return true

    try {
      const c = chat.contact
      for (const cp of contactPhones(c)) {
        if (wp.length >= 8 && phonesMatch(wp, cp)) return true
      }
    } catch {
      /* ignore */
    }
    if (wp.length >= 8 && contactsByPhone) {
      for (const v of phoneVariants(wp)) {
        const hit = contactsByPhone.get(v)
        if (!hit) continue
        try {
          if (chat.id && hit.id && String(hit.id._serialized) === String(chat.id._serialized)) {
            return true
          }
          if (chat.contact?.id && hit.id && String(hit.id._serialized) === String(chat.contact.id._serialized)) {
            return true
          }
          // Contacto con teléfono vigilado ligado al chat por id.user
          if (chat.id?.user && hit.id?.user && String(chat.id.user) === String(hit.id.user)) {
            return true
          }
        } catch {
          /* ignore */
        }
      }
    }

    const title = chatTitle(chat)
    if (watch?.name && title && namesMatch(watch.name, title)) return true
    // pushname suelto
    try {
      const push = String(chat?.contact?.pushname || '').trim()
      if (watch?.name && push && namesMatch(watch.name, push)) return true
    } catch {
      /* ignore */
    }
    return false
  }

  /**
   * @param {Array<{prospectId?:number, phone?:string, name?:string, outboundText?:string}>} watchList
   * @param {object} [config] JSON OTA (módulos, flags)
   */
  globalThis.__NEXUS_WA_READ_WATCHED__ = async function nexusWaReadWatched(watchList, config) {
    const watches = Array.isArray(watchList) ? watchList : []
    const cfg = config && typeof config === 'object' ? config : {}
    const diag = {
      require: typeof globalThis.require === 'function',
      source: null,
      chats: 0,
      matched: 0,
      inbound: 0,
      error: null,
    }
    if (!watches.length) return { ok: true, rows: [], diag }
    if (cfg.storeEnabled === false) {
      diag.error = 'store_disabled'
      return { ok: false, rows: [], diag }
    }

    const cols = getCollections(cfg)
    if (!cols?.Chat) {
      diag.error = 'no_store'
      return { ok: false, rows: [], diag }
    }
    diag.source = cols.source

    const chats = modelsOf(cols.Chat)
    diag.chats = chats.length

    /** @type {Map<string, any>} */
    const contactsByPhone = new Map()
    for (const c of modelsOf(cols.Contact)) {
      for (const p of contactPhones(c)) {
        for (const v of phoneVariants(p)) contactsByPhone.set(v, c)
      }
    }

    /** @type {{ phone: string, text: string, prospectId?: number, source: string }[]} */
    const rows = []

    const batchSize = Math.min(
      Math.max(Number(cfg.storeWatchBatchSize) || 16, 4),
      Math.max(Number(cfg.storeWatchMax) || watches.length, 4),
    )
    const toScan = watches.slice(0, Math.min(watches.length, Number(cfg.storeWatchMax) || 40))

    for (let bi = 0; bi < toScan.length; bi += batchSize) {
      const batch = toScan.slice(bi, bi + batchSize)
      for (const w of batch) {
      let chat = null
      for (const c of chats) {
        if (matchWatchToChat(w, c, contactsByPhone)) {
          chat = c
          break
        }
      }
      if (!chat) continue
      diag.matched += 1

      let msgs = []
      try {
        msgs = await ensureMsgsLoaded(chat)
      } catch (e) {
        diag.error = String(e?.message || e).slice(0, 120)
        msgs = msgsOfChat(chat)
      }

      let texts = extractInboundAfterOut(msgs, w.outboundText || '')
      if (!texts.length) texts = extractByUnread(chat, msgs)
      // Último recurso: último mensaje no-fromMe del chat (si no es eco del outbound).
      if (!texts.length && msgs.length) {
        const ordered = msgs
          .map((m) => ({
            fromMe: isFromMe(m),
            text: msgBody(m),
            t: Number(m.t || m.timestamp || 0) || 0,
          }))
          .filter((m) => m.text)
          .sort((a, b) => a.t - b.t)
        const last = ordered[ordered.length - 1]
        if (last && !last.fromMe) texts = [last.text]
      }

      for (const text of texts) {
        diag.inbound += 1
        rows.push({
          phone: digits(w.phone) || chatUserDigits(chat) || '',
          text,
          prospectId: Number(w.prospectId || 0) || undefined,
          source: 'wa-store',
        })
      }
      }
    }

    return { ok: true, rows, diag }
  }
})()
