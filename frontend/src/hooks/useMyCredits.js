import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext.jsx'
import { useCompany } from '../context/CompanyContext.jsx'
import { normalizeRole, ROLES, isCompanyAdmin } from '../data/navigation.js'
import { currentUserId } from '../utils/campaignUsers.js'
import { fetchCreditAllocations, fetchMyCredits, fetchWallet } from '../utils/api.js'

const POLL_MS = 45_000

async function loadCreditsFallback(companyId, userId, role) {
  if (!companyId || !userId) {
    return null
  }
  if (isCompanyAdmin({ role })) {
    const wallet = await fetchWallet(companyId)
    const assigned = Number(wallet?.assigned_to_sellers) || 0
    const total = Number(wallet?.total_balance) || 0
    const available = Math.max(0, total - assigned)
    return {
      available_balance: available,
      allocated_balance: assigned,
      used_balance: 0,
      role_scope: 'director_pool',
    }
  }
  const rows = await fetchCreditAllocations(companyId)
  const list = Array.isArray(rows) ? rows : []
  const row = list.find((a) => Number(a.seller_id) === userId)
  const allocated = Number(row?.allocated_balance) || 0
  const used = Number(row?.used_balance) || 0
  return {
    available_balance: Math.max(0, allocated - used),
    allocated_balance: allocated,
    used_balance: used,
    role_scope: 'personal',
  }
}

/**
 * Saldo de créditos del usuario logueado.
 * Directora: pool disponible para enviar a managers.
 * Manager / SDR: créditos asignados menos usados.
 */
export function useMyCredits() {
  const { user } = useAuth()
  const { companyId } = useCompany()
  const role = normalizeRole(user?.role)
  const userId = currentUserId(user)
  const showCredits =
    isCompanyAdmin({ role }) || role === ROLES.manager || role === ROLES.sdr

  const [state, setState] = useState({
    available: 0,
    allocated: 0,
    used: 0,
    roleScope: 'personal',
    loading: true,
  })

  const refresh = useCallback(async () => {
    if (!user || !showCredits) {
      setState({
        available: 0,
        allocated: 0,
        used: 0,
        roleScope: 'personal',
        loading: false,
      })
      return
    }
    try {
      let data = null
      try {
        data = await fetchMyCredits()
      } catch {
        if (companyId && userId) {
          data = await loadCreditsFallback(companyId, userId, role)
        }
      }
      if (!data && companyId && userId) {
        data = await loadCreditsFallback(companyId, userId, role)
      }
      setState({
        available: Number(data?.available_balance) || 0,
        allocated: Number(data?.allocated_balance) || 0,
        used: Number(data?.used_balance) || 0,
        roleScope: data?.role_scope === 'director_pool' ? 'director_pool' : 'personal',
        loading: false,
      })
    } catch {
      setState((prev) => ({ ...prev, loading: false }))
    }
  }, [companyId, role, showCredits, user, userId])

  useEffect(() => {
    void refresh()
    const onChange = () => {
      void refresh()
    }
    window.addEventListener('nx:credits-changed', onChange)
    window.addEventListener('focus', onChange)
    const timer = window.setInterval(() => {
      void refresh()
    }, POLL_MS)
    return () => {
      window.removeEventListener('nx:credits-changed', onChange)
      window.removeEventListener('focus', onChange)
      window.clearInterval(timer)
    }
  }, [refresh])

  return { ...state, showCredits, refresh }
}

export function notifyCreditsChanged() {
  window.dispatchEvent(new CustomEvent('nx:credits-changed'))
}
