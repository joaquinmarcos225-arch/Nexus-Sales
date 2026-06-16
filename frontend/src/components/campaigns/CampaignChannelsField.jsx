import { CHANNEL_LABELS, CHANNEL_ORDER, orderChannels } from '../../utils/campaignChannels.js'

function cbClass(checked) {
  return [
    'flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors',
    checked
      ? 'border-nx-brand bg-nx-brand/5 text-nx-ink'
      : 'border-[#e5e7eb] bg-white text-[#6b7280] hover:border-[#cbd5e1]',
  ].join(' ')
}

export function CampaignChannelsField({ value, onChange, disabled, hintClassName }) {
  const ordered = orderChannels(value?.length ? value : CHANNEL_ORDER)
  const set = new Set(ordered)

  function toggle(channelId) {
    if (disabled) {
      return
    }
    const next = new Set(set)
    if (next.has(channelId)) {
      if (next.size <= 1) {
        return
      }
      next.delete(channelId)
    } else {
      next.add(channelId)
    }
    onChange(orderChannels([...next]))
  }

  const hint =
    hintClassName ??
    'mt-1 text-[11px] text-[#9ca3af]'

  return (
    <div>
      <p className="text-xs font-medium text-[#374151]">Canales permitidos</p>
      <p className={hint}>
        Prioridad efectiva entre los habilitados: LinkedIn, luego Email, luego WhatsApp. En el
        futuro esto controlará envíos reales.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {CHANNEL_ORDER.map((id) => {
          const checked = set.has(id)
          return (
            <label key={id} className={cbClass(checked)}>
              <input
                type="checkbox"
                className="h-3.5 w-3.5 rounded border-[#cbd5e1] text-nx-brand focus:ring-nx-brand/25"
                checked={checked}
                disabled={disabled || (checked && set.size <= 1)}
                onChange={() => toggle(id)}
              />
              <span>{CHANNEL_LABELS[id]}</span>
            </label>
          )
        })}
      </div>
    </div>
  )
}
