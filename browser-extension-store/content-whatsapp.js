;(function () {
  const WA_PENDING_KEY = 'nexusWaPendingSend'

  function composerEl() {
    return (
      document.querySelector('#main footer [contenteditable="true"][data-lexical-editor="true"]') ||
      document.querySelector('#main footer [contenteditable="true"][role="textbox"]') ||
      document.querySelector('footer [contenteditable="true"][role="textbox"]') ||
      document.querySelector('footer div[contenteditable="true"]')
    )
  }

  function isLoggedInUi() {
    if (document.querySelector('canvas[aria-label*="QR"]') || document.querySelector('[data-testid="qrcode"]')) {
      return false
    }
    return Boolean(document.querySelector('#pane-side') || document.querySelector('#side') || composerEl())
  }

  function isSendControl(el) {
    if (!el || !(el instanceof Element)) return false
    const btn =
      el.closest('button') ||
      el.closest('[role="button"]') ||
      el.closest('[data-testid="compose-btn-send"]') ||
      el.closest('[data-icon="send"]') ||
      el.closest('[aria-label*="Enviar"]') ||
      el.closest('[aria-label*="Send"]')
    if (!btn) return false
    const label = `${btn.getAttribute('aria-label') || ''} ${btn.getAttribute('data-testid') || ''} ${btn.getAttribute('title') || ''}`.toLowerCase()
    if (label.includes('send') || label.includes('enviar') || label.includes('compose-btn-send')) return true
    if (btn.querySelector('[data-icon="send"]')) return true
    return false
  }

  async function getPending() {
    try {
      const stored = await chrome.storage.local.get(WA_PENDING_KEY)
      return stored?.[WA_PENDING_KEY] || null
    } catch {
      return null
    }
  }

  async function reportHumanSend() {
    const pending = await getPending()
    const prospectId = Number(pending?.prospectId || 0)
    if (!prospectId) return
    chrome.runtime.sendMessage(
      {
        type: 'NEXUS_WHATSAPP_OUTBOUND_SENT',
        prospectId,
        phoneDigits: String(pending?.phoneDigits || '').replace(/\D/g, ''),
      },
      () => void chrome.runtime.lastError,
    )
  }

  document.addEventListener(
    'keydown',
    (ev) => {
      if (ev.key !== 'Enter' || ev.shiftKey) return
      const el = composerEl()
      if (!el || !el.contains(ev.target) && ev.target !== el) return
      void reportHumanSend()
    },
    true,
  )

  document.addEventListener(
    'click',
    (ev) => {
      if (!isSendControl(ev.target)) return
      void reportHumanSend()
    },
    true,
  )

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'NEXUS_WA_COMPOSER_READY') {
      const loggedIn = isLoggedInUi()
      sendResponse({ ready: loggedIn && Boolean(composerEl()), loggedIn })
      return false
    }
    return false
  })
})()
