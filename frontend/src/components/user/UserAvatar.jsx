import { useEffect, useState } from 'react'
import { resolveApiUrl } from '../../utils/constants.js'
import { getStoredToken } from '../../utils/authStorage.js'

/**
 * Carga la foto de perfil con Authorization (img src no puede mandar Bearer).
 */
export function useAuthAvatarUrl(avatarPath, cacheKey = '') {
  const [src, setSrc] = useState(null)

  useEffect(() => {
    let cancelled = false
    let objectUrl = null
    if (!avatarPath) {
      setSrc(null)
      return undefined
    }
    const token = getStoredToken()
    if (!token) {
      setSrc(null)
      return undefined
    }
    const url = resolveApiUrl(
      `${avatarPath}${avatarPath.includes('?') ? '&' : '?'}v=${encodeURIComponent(cacheKey || '1')}`,
    )
    ;(async () => {
      try {
        const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
        if (!res.ok) {
          if (!cancelled) setSrc(null)
          return
        }
        const blob = await res.blob()
        objectUrl = URL.createObjectURL(blob)
        if (!cancelled) setSrc(objectUrl)
      } catch {
        if (!cancelled) setSrc(null)
      }
    })()
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [avatarPath, cacheKey])

  return src
}

export function UserAvatar({
  name = '',
  avatarUrl = null,
  cacheKey = '',
  size = 'md',
  className = '',
}) {
  const photo = useAuthAvatarUrl(avatarUrl, cacheKey)
  const initials = String(name || '?')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() || '')
    .join('') || '?'

  const sizeClass =
    size === 'lg'
      ? 'size-20 text-2xl'
      : size === 'sm'
        ? 'size-8 text-[11px]'
        : 'size-10 text-sm'

  return (
    <span
      className={[
        'relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold',
        sizeClass,
        photo ? 'bg-zinc-200' : 'bg-gradient-to-br from-zinc-700 to-zinc-900 text-white',
        className,
      ].join(' ')}
      aria-hidden={!photo}
    >
      {photo ? (
        <img src={photo} alt="" className="size-full object-cover" />
      ) : (
        <span>{initials}</span>
      )}
    </span>
  )
}
