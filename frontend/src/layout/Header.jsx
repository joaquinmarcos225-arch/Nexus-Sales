import { useCallback, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { GlobalSearch, useGlobalSearchHotkey } from '../components/search/GlobalSearch.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import { useLinkedInPending } from '../hooks/useLinkedInPending.js'
import { useWhatsAppPending } from '../hooks/useWhatsAppPending.js'
import { useCallPending } from '../hooks/useCallPending.js'
import { useMeetingsPending } from '../hooks/useMeetingsPending.js'
import { usePageBreadcrumbs } from '../hooks/usePageBreadcrumbs.js'
import { requestDesktopNotificationPermission } from '../utils/desktopNotifications.js'
import { UserMenu } from './UserMenu.jsx'
import { HeaderCreditBadge } from './HeaderCreditBadge.jsx'
import { useTutorial } from '../context/TutorialContext.jsx'

const IS_MAC =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.platform)

/**
 * @param {{
 *   onMenuClick?: () => void,
 *   onToggleSidebar?: () => void,
 *   collapsed?: boolean,
 *   compactNav?: boolean,
 * }} props
 */
export function Header({ onMenuClick, onToggleSidebar, collapsed = false, compactNav = false }) {
  const crumbs = usePageBreadcrumbs()
  const navigate = useNavigate()
  const { companyId } = useCompany()
  const { count: linkedInPending, href: linkedInHref } = useLinkedInPending(companyId)
  const { count: whatsAppPending, href: whatsAppHref } = useWhatsAppPending(companyId)
  const { count: callPending, href: callHref } = useCallPending(companyId)
  const { count: meetingsPending, href: meetingsHref } = useMeetingsPending(companyId)
  const notifyPending = linkedInPending + whatsAppPending + callPending + meetingsPending
  const notifyHref = (() => {
    const ranked = [
      { n: whatsAppPending, href: whatsAppHref },
      { n: linkedInPending, href: linkedInHref },
      { n: callPending, href: callHref },
      { n: meetingsPending, href: meetingsHref },
    ].sort((a, b) => b.n - a.n)
    return ranked[0]?.href || linkedInHref || whatsAppHref || callHref || meetingsHref
  })()
  const { startTutorial, active: tutorialActive } = useTutorial()
  const [searchOpen, setSearchOpen] = useState(false)
  const pageTitle = crumbs[crumbs.length - 1]?.label || 'Nexus'

  const openSearch = useCallback(() => setSearchOpen(true), [])
  const closeSearch = useCallback(() => setSearchOpen(false), [])
  useGlobalSearchHotkey(openSearch)

  function handleMenuButton() {
    if (compactNav) {
      onMenuClick?.()
    } else {
      onToggleSidebar?.()
    }
  }

  async function handleNotifications() {
    await requestDesktopNotificationPermission()
    navigate(notifyHref || '/campanas')
  }

  return (
    <>
      <header className="nx-topbar nx-chrome-surface relative sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 px-3 sm:gap-3 sm:px-4 lg:px-5">
        <div className="relative z-10 flex min-w-0 flex-1 items-center gap-2 sm:gap-3">
          <button
            type="button"
            className="nx-topbar-icon-btn"
            aria-label={
              compactNav
                ? 'Abrir menú'
                : collapsed
                  ? 'Expandir menú lateral'
                  : 'Plegar menú lateral'
            }
            onClick={handleMenuButton}
          >
            <svg className="size-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
            </svg>
          </button>

          <p className="min-w-0 flex-1 truncate text-sm font-medium text-white lg:hidden">{pageTitle}</p>

          <nav className="hidden min-w-0 items-center gap-1.5 text-sm lg:flex" aria-label="Ubicación actual">
            {crumbs.map((crumb, index) => {
              const isLast = index === crumbs.length - 1
              return (
                <span key={`${crumb.label}-${index}`} className="flex min-w-0 items-center gap-1.5">
                  {index > 0 ? (
                    <span className="text-zinc-600" aria-hidden>
                      /
                    </span>
                  ) : null}
                  {crumb.to && !isLast ? (
                    <Link to={crumb.to} className="truncate text-zinc-200 transition-colors hover:text-white">
                      {crumb.label}
                    </Link>
                  ) : (
                    <span className={`truncate ${isLast ? 'font-medium text-white' : 'text-zinc-200'}`}>
                      {crumb.label}
                    </span>
                  )}
                </span>
              )
            })}
          </nav>
        </div>

        <div className="relative z-10 flex shrink-0 items-center gap-1 sm:gap-1.5">
          <button
            type="button"
            className="nx-topbar-icon-btn"
            aria-label="Buscar campañas y páginas"
            title={`Buscar (${IS_MAC ? '⌘K' : 'Ctrl+K'})`}
            onClick={openSearch}
          >
            <svg className="size-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
              <path
                fillRule="evenodd"
                d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z"
                clipRule="evenodd"
              />
            </svg>
          </button>

          <button
            type="button"
            className={[
              'nx-topbar-icon-btn hidden sm:inline-flex',
              tutorialActive ? 'text-nx-brand' : '',
            ].join(' ')}
            aria-label="Tutorial de Nexus"
            title="Tutorial guiado"
            onClick={() => startTutorial(0)}
          >
            <svg className="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25"
              />
            </svg>
          </button>

          <HeaderCreditBadge />

          <button
            type="button"
            className="nx-topbar-icon-btn relative"
            aria-label={
              notifyPending > 0
                ? `${notifyPending} pendiente${notifyPending === 1 ? '' : 's'} (LinkedIn ${linkedInPending}, WhatsApp ${whatsAppPending}, Llamadas ${callPending}, Reuniones ${meetingsPending})`
                : 'Notificaciones'
            }
            onClick={handleNotifications}
          >
            <svg className="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"
              />
            </svg>
            {notifyPending > 0 ? (
              <span className="nx-topbar-notify-badge" aria-hidden>
                {notifyPending > 99 ? '99+' : notifyPending}
              </span>
            ) : null}
          </button>

          <Link
            to="/configuracion/integraciones"
            className="nx-topbar-icon-btn hidden sm:inline-flex"
            aria-label="Ayuda e integraciones"
          >
            <svg className="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9 5.25h.008v.008H12v-.008z"
              />
            </svg>
          </Link>

          <UserMenu />
        </div>
      </header>

      <GlobalSearch open={searchOpen} onClose={closeSearch} />
    </>
  )
}
