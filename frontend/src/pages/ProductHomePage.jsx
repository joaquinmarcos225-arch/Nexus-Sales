export default function ProductHomePage() {
  return (
    <main className="min-h-dvh bg-slate-50 px-4 py-10 text-slate-800">
      <article className="mx-auto max-w-3xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-10">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-600">
          CostGuard · Nexus Sales
        </p>
        <h1 className="mt-2 text-3xl font-bold text-slate-950">Nexus Sales</h1>
        <p className="mt-4 text-sm leading-relaxed">
          Nexus Sales is a B2B sales outreach product operated by CostGuard. Sales teams use it to
          find prospects, run follow-up sequences, and book meetings — from the seller&apos;s own
          connected accounts.
        </p>
        <p className="mt-3 text-sm leading-relaxed">
          Nexus Sales es el software de outreach comercial de CostGuard. El equipo de ventas busca
          prospectos, envía seguimiento y agenda reuniones desde sus propias cuentas.
        </p>

        <h2 className="mt-8 text-lg font-semibold text-slate-950">What Nexus Sales does</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-relaxed">
          <li>Build and run outreach campaigns for a product and target market.</li>
          <li>Send or draft commercial email through the seller&apos;s Gmail.</li>
          <li>Detect prospect replies so the sequence can pause and the seller can answer.</li>
          <li>Check free times and create meeting events on the seller&apos;s Google Calendar.</li>
          <li>Coordinate the same conversation across email and other sales channels.</li>
        </ul>

        <h2 className="mt-8 text-lg font-semibold text-slate-950">Google access</h2>
        <p className="mt-2 text-sm leading-relaxed">
          Connecting Google is optional and done by each seller in Nexus Sales → Integrations.
          Nexus uses Gmail only to draft or send approved outreach and to read replies from
          contacted prospects. It uses Calendar only to offer free slots and to create or update
          the meeting with that prospect. Nexus does not sell this data or use it for advertising.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <a
            className="inline-flex rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white"
            href="/login"
          >
            Sign in to Nexus Sales
          </a>
          <a
            className="inline-flex rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-900"
            href="/privacidad"
          >
            Privacy policy
          </a>
        </div>
        <p className="mt-6 text-sm">
          Support:{' '}
          <a className="font-semibold text-red-700 underline" href="mailto:joaquin@costguard.com.ar">
            joaquin@costguard.com.ar
          </a>
        </p>
      </article>
    </main>
  )
}
