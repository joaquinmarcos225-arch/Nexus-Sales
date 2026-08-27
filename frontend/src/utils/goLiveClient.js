import {
  fetchCampaigns,
  fetchProducts,
  fetchUsers,
  fetchWallet,
} from './api.js'

const PLACEHOLDER = 'describí qué vende tu empresa'

/** Checks de workspace armados en el cliente (fallback si /go-live no existe aún). */
export async function buildGoLiveClientFallback(companyId) {
  const [products, campaigns, users, wallet] = await Promise.all([
    fetchProducts(companyId).catch(() => []),
    fetchCampaigns(companyId).catch(() => []),
    fetchUsers(companyId).catch(() => []),
    fetchWallet(companyId).catch(() => null),
  ])

  const productList = Array.isArray(products) ? products : []
  const productOk = productList.some((p) => {
    const desc = String(p.description || '').trim().toLowerCase()
    return p.is_active !== false && desc && !desc.includes(PLACEHOLDER)
  })
  const hasProduct = productList.length > 0

  const sdrCount = (Array.isArray(users) ? users : []).filter(
    (u) => String(u.role || '').toLowerCase() === 'sdr',
  ).length

  const pool = Number(wallet?.total_balance) || 0
  const assigned = Number(wallet?.assigned_to_sellers) || 0
  const creditsOk = pool > 0 || assigned > 0

  const workspaceChecks = [
    {
      id: 'product',
      label: 'Producto cargado',
      ok: hasProduct,
      hint: 'Completá nombre y descripción en Productos.',
    },
    {
      id: 'product_copy',
      label: 'Descripción de producto usable',
      ok: productOk || hasProduct,
      hint: 'La IA necesita saber qué vendés (no el texto placeholder).',
    },
    {
      id: 'credits',
      label: 'Créditos disponibles',
      ok: creditsOk,
      hint: 'Asigná créditos al SDR en Créditos (mín. ~30 para arrancar).',
    },
    {
      id: 'sdr',
      label: 'Usuario SDR creado',
      ok: sdrCount > 0,
      hint: 'Creá un vendedor en Equipo (no uses sdr@test.com en prod).',
    },
    {
      id: 'campaign',
      label: 'Al menos una campaña',
      ok: (Array.isArray(campaigns) ? campaigns : []).length > 0,
      hint: 'Plantilla LinkedIn → Email → WhatsApp.',
    },
  ]

  const pending = workspaceChecks.filter((c) => !c.ok).length

  return {
    ready: pending === 0,
    pending_count: pending,
    server: {
      prod_ready: false,
      checks: [
        {
          id: 'api',
          label: 'Endpoint go-live del servidor',
          ok: false,
          hint: 'Reiniciá el backend (puerto 8002) para ver checks de deploy.',
        },
      ],
      pending_count: 1,
    },
    workspace: {
      checks: workspaceChecks,
      pending_count: pending,
      credit_pool: pool,
      credits_assigned: assigned,
      sdr_count: sdrCount,
      campaign_count: (Array.isArray(campaigns) ? campaigns : []).length,
    },
    _fallback: true,
  }
}

export function isGoLiveNotFoundError(err) {
  if (Number(err?.status) === 404) return true
  const msg = String(err?.message || err || '').toLowerCase()
  return msg.includes('not found') || msg.includes('404')
}
