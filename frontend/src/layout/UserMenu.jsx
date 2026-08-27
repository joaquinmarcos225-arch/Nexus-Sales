import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { ROLE_LABELS, normalizeRole } from '../data/navigation.js'
import { UserAvatar } from '../components/user/UserAvatar.jsx'

export function UserMenu() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const role = normalizeRole(user?.role)
  const displayName = user?.first_name || user?.name || 'Usuario'

  useEffect(() => {
    if (!open) {
      return undefined
    }
    function onPointerDown(event) {
      if (!rootRef.current?.contains(event.target)) {
        setOpen(false)
      }
    }
    function onKeyDown(event) {
      if (event.key === 'Escape') {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!user) {
    return null
  }

  function handleLogout() {
    setOpen(false)
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        className="nx-user-trigger"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Menú de usuario"
        onClick={() => setOpen((value) => !value)}
      >
        <UserAvatar
          name={displayName}
          avatarUrl={user.avatar_url}
          size="sm"
          className="ring-1 ring-white/10"
        />
        <span className="hidden max-w-[8rem] truncate text-sm font-medium text-zinc-200 md:inline">
          {displayName}
        </span>
        <svg
          className={`size-4 text-zinc-500 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          viewBox="0 0 20 20"
          fill="currentColor"
          aria-hidden
        >
          <path
            fillRule="evenodd"
            d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.24 4.5a.75.75 0 01-1.08 0l-4.24-4.5a.75.75 0 01.02-1.06z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {open ? (
        <div className="nx-user-menu" role="menu">
          <div className="flex items-center gap-2.5 border-b border-white/[0.06] px-3 py-2.5">
            <UserAvatar name={displayName} avatarUrl={user.avatar_url} size="sm" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-white">{displayName}</p>
              <p className="truncate text-xs text-zinc-500">{ROLE_LABELS[role] || user.role}</p>
            </div>
          </div>
          <Link
            to="/mi-perfil"
            role="menuitem"
            className="nx-user-menu-item"
            onClick={() => setOpen(false)}
          >
            Mi perfil
          </Link>
          <button
            type="button"
            role="menuitem"
            className="nx-user-menu-item w-full text-left"
            onClick={handleLogout}
          >
            Cerrar sesión
          </button>
        </div>
      ) : null}
    </div>
  )
}
