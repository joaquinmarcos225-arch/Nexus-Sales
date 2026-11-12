import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCompany } from '../../context/CompanyContext.jsx'
import { AlertBanner } from '../../components/AlertBanner.jsx'
import {
  explorarAddToCampaign,
  explorarRemoveFavorite,
  explorarReveal,
  fetchCampaigns,
  fetchExplorarFavorites,
} from '../../utils/api.js'
import { Icon, ICONS, PersonCard, SkeletonCard, personSnapshot } from '../ExplorarPage.jsx'

export default function ExplorarGuardadosPage() {
  const { companyId } = useCompany()
  const [items, setItems] = useState(null)
  const [includesTeam, setIncludesTeam] = useState(false)
  const [owner, setOwner] = useState('all')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revealing, setRevealing] = useState({})
  const [starring, setStarring] = useState({})
  const [selected, setSelected] = useState({})
  const [campaigns, setCampaigns] = useState([])
  const [campaignId, setCampaignId] = useState('')
  const [adding, setAdding] = useState(false)

  const load = useCallback(async () => {
    if (!companyId) return
    try {
      const out = await fetchExplorarFavorites(companyId)
      setItems(out?.results || [])
      setIncludesTeam(Boolean(out?.includes_team))
    } catch (e) {
      setItems([])
      setError(e?.message || 'No se pudieron cargar los guardados.')
    }
  }, [companyId])

  useEffect(() => {
    load()
    if (!companyId) return
    fetchCampaigns(companyId)
      .then((rows) => setCampaigns((Array.isArray(rows) ? rows : []).filter((c) => !c.is_individual_container)))
      .catch(() => {})
  }, [companyId, load])

  const owners = useMemo(() => {
    const map = new Map()
    for (const it of items || []) {
      if (!map.has(it.owner_user_id)) map.set(it.owner_user_id, it.mine ? 'Míos' : it.owner_name || 'Sin nombre')
    }
    return [...map.entries()]
  }, [items])

  const visible = useMemo(
    () => (items || []).filter((it) => owner === 'all' || String(it.owner_user_id) === owner),
    [items, owner],
  )
  const selectedPeople = visible.filter((p) => selected[p.person_id] && !p.prospect_id && !p.in_use)
  const selectedRevealed = selectedPeople.filter((p) => p.revealed && !p.revealed_by_nexus).length

  const updatePerson = (personId, patch) =>
    setItems((list) => (list || []).map((r) => (r.person_id === personId ? { ...r, ...patch } : r)))

  const dropPerson = (personId, ownerUserId = null) =>
    setItems((list) =>
      (list || []).filter((r) => r.person_id !== personId || (ownerUserId != null && r.owner_user_id !== ownerUserId)),
    )

  const reveal = async (p) => {
    setRevealing((m) => ({ ...m, [p.person_id]: true }))
    setError('')
    try {
      const out = await explorarReveal(companyId, personSnapshot(p))
      const person = out?.person || {}
      updatePerson(p.person_id, {
        revealed: true,
        revealed_by_nexus: !person.paid,
        email: person.email,
        phone: person.phone,
        linkedin_url: person.linkedin_url || p.linkedin_url,
      })
    } catch (e) {
      setError(e?.message || 'No se pudo revelar.')
    } finally {
      setRevealing((m) => ({ ...m, [p.person_id]: false }))
    }
  }

  const unstar = async (p) => {
    const key = `${p.owner_user_id}:${p.person_id}`
    setStarring((m) => ({ ...m, [key]: true }))
    setError('')
    try {
      await explorarRemoveFavorite(companyId, p.person_id, p.mine ? null : p.owner_user_id)
      dropPerson(p.person_id, p.owner_user_id)
      setNotice(
        p.mine
          ? `Sacamos a ${p.name} de Guardados. Vuelve a aparecerte en las búsquedas.`
          : `Sacamos a ${p.name} de los guardados de ${p.owner_name || 'ese vendedor'}.`,
      )
    } catch (e) {
      setError(e?.message || 'No se pudo sacar de Guardados.')
    } finally {
      setStarring((m) => ({ ...m, [key]: false }))
    }
  }

  const addToCampaign = async () => {
    if (!campaignId || selectedPeople.length === 0) return
    setAdding(true)
    setError('')
    setNotice('')
    try {
      const out = await explorarAddToCampaign(companyId, Number(campaignId), selectedPeople.map(personSnapshot))
      for (const r of out?.results || []) {
        if (r.ok) dropPerson(r.person_id)
      }
      const failed = (out?.results || []).filter((r) => !r.ok)
      const msg = `Sumamos ${out?.added || 0} a «${out?.campaign_name || 'la campaña'}» y salieron de Guardados.`
      setNotice(
        failed.length ? `${msg} No se pudieron sumar: ${failed.map((f) => `${f.name} (${f.error})`).join('; ')}` : msg,
      )
      setSelected({})
    } catch (e) {
      setError(e?.message || 'No se pudo sumar a la campaña.')
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="pb-24">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-nx-ink sm:text-[1.65rem]">Guardados</h1>
        <p className="mt-1.5 max-w-2xl text-sm text-nx-ink/75">
          Las personas que marcaste con estrella en Explorar. No te vuelven a aparecer en las búsquedas; cuando las
          sumás a una campaña salen de acá.
          {includesTeam ? ' Como director ves los guardados de todo el equipo.' : ''}
        </p>
      </header>

      {includesTeam && owners.length > 1 ? (
        <div className="mb-4 flex flex-wrap gap-1.5">
          {[['all', 'Todos'], ...owners.map(([id, label]) => [String(id), label])].map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => {
                setOwner(id)
                setSelected({})
              }}
              className={[
                'rounded-full px-3 py-1 text-xs font-medium ring-1 transition',
                owner === id
                  ? 'bg-nx-ink text-white ring-nx-ink shadow-sm'
                  : 'bg-white text-nx-ink/80 ring-nx-border hover:bg-nx-bg hover:text-nx-ink',
              ].join(' ')}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      <AlertBanner message={error} />
      {notice ? (
        <p className="mb-4 rounded-lg bg-emerald-50 px-4 py-2.5 text-sm text-emerald-900 ring-1 ring-emerald-200">
          {notice}
        </p>
      ) : null}

      {items === null ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-nx-border bg-white/60 px-6 py-10 text-center">
          <div className="mx-auto grid size-12 place-items-center rounded-full bg-amber-50 text-amber-500">
            <Icon d={ICONS.star} className="size-6" />
          </div>
          <h2 className="mt-3 text-base font-semibold text-nx-ink">Todavía no guardaste a nadie</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-nx-muted">
            En los resultados de búsqueda tocá la estrella de una persona para dejarla guardada acá.
          </p>
          <Link to="/explorar" className="nx-btn nx-btn-secondary mt-4 inline-flex px-4 py-1.5 text-sm">
            Ir a Buscar
          </Link>
        </section>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {visible.map((p) => (
            <PersonCard
              key={`${p.owner_user_id}:${p.person_id}`}
              p={p}
              selected={Boolean(selected[p.person_id])}
              onSelect={(v) => setSelected((s) => ({ ...s, [p.person_id]: v }))}
              revealing={Boolean(revealing[p.person_id])}
              onReveal={() => reveal(p)}
              favorite
              starring={Boolean(starring[`${p.owner_user_id}:${p.person_id}`])}
              onToggleFavorite={() => unstar(p)}
              ownerLabel={includesTeam && !p.mine ? `Guardado por ${p.owner_name || 'otro vendedor'}` : null}
            />
          ))}
        </div>
      )}

      {selectedPeople.length > 0 ? (
        <div className="fixed inset-x-0 bottom-4 z-30 flex justify-center px-4">
          <div className="flex w-full max-w-3xl flex-wrap items-center gap-3 rounded-2xl bg-nx-ink px-4 py-3 text-white shadow-2xl">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{selectedPeople.length} seleccionados</p>
              <p className="text-[11px] text-white/70">
                {selectedRevealed} ya revelados (sin costo extra) · {selectedPeople.length - selectedRevealed} usan 1
                crédito c/u
              </p>
            </div>
            <select
              className="h-9 rounded-lg border border-white/20 bg-white/10 px-2 text-sm text-white outline-none focus:border-white/50"
              value={campaignId}
              onChange={(e) => setCampaignId(e.target.value)}
            >
              <option value="" className="text-nx-ink">
                Elegí campaña…
              </option>
              {campaigns.map((c) => (
                <option key={c.id} value={c.id} className="text-nx-ink">
                  {c.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={adding || !campaignId}
              onClick={addToCampaign}
              className="h-9 rounded-lg bg-white px-4 text-sm font-semibold text-nx-ink transition hover:bg-white/90 disabled:opacity-50"
            >
              {adding ? 'Sumando…' : 'Sumar a campaña'}
            </button>
            <button type="button" onClick={() => setSelected({})} className="text-xs text-white/70 hover:text-white">
              Cancelar
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
