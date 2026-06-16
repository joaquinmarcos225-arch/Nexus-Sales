import { useEffect, useState } from 'react'
import { fetchCampaignProspects, fetchCampaigns, fetchUsers } from '../utils/api.js'

/**
 * Prospectos de todas las campañas de la empresa (para tablas de reportes).
 */
export function useFlattenedProspects(companyId) {
  const [rows, setRows] = useState([])
  const [sellerById, setSellerById] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!companyId) {
      setRows([])
      setLoading(false)
      return
    }
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        const [campaigns, users] = await Promise.all([
          fetchCampaigns(companyId),
          fetchUsers(companyId),
        ])
        if (cancelled) {
          return
        }
        const sellers = {}
        for (const u of users || []) {
          if (u.role === 'seller') {
            sellers[u.id] = u.name
          }
        }
        setSellerById(sellers)
        const cList = Array.isArray(campaigns) ? campaigns : []
        const lists = await Promise.all(
          cList.map((c) =>
            fetchCampaignProspects(c.id).catch(() => []),
          ),
        )
        if (cancelled) {
          return
        }
        const flat = []
        cList.forEach((c, i) => {
          for (const p of lists[i] || []) {
            flat.push({
              ...p,
              campaign_id: c.id,
              campaign_name: c.name,
              campaign_status: c.status,
              seller_id: c.seller_id,
              seller_name: sellers[c.seller_id] ?? '—',
            })
          }
        })
        setRows(flat)
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setRows([])
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [companyId])

  return { rows, sellerById, loading, error }
}
