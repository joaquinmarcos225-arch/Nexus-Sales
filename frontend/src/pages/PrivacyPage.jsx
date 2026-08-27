export default function PrivacyPage() {
  return (
    <main className="min-h-dvh bg-slate-50 px-4 py-10 text-slate-800">
      <article className="mx-auto max-w-3xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-10">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-600">
          CostGuard · Nexus Sales
        </p>
        <h1 className="mt-2 text-3xl font-bold text-slate-950">Política de privacidad</h1>
        <p className="mt-2 text-sm text-slate-500">Última actualización: 17 de agosto de 2026</p>

        <div className="mt-8 space-y-7 text-sm leading-relaxed">
          <section>
            <h2 className="text-lg font-semibold text-slate-950">Quiénes somos</h2>
            <p className="mt-2">
              CostGuard opera Nexus Sales, un software de outreach comercial para equipos de venta.
              Sitio:{' '}
              <a className="font-semibold text-red-700 underline" href="https://nexus.costguard.com.ar/inicio">
                https://nexus.costguard.com.ar/inicio
              </a>
              .
              Contacto de privacidad:{' '}
              <a className="font-semibold text-red-700 underline" href="mailto:joaquin@costguard.com.ar">
                joaquin@costguard.com.ar
              </a>
              .
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Datos que tratamos</h2>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>Cuenta Nexus: nombre, email, rol y empresa.</li>
              <li>Prospectos y campañas que carga o genera el equipo de la empresa cliente.</li>
              <li>Mensajes de outreach y respuestas asociadas a esos prospectos.</li>
              <li>Registros técnicos de uso, errores y facturación operativa.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Google (Gmail y Calendar)</h2>
            <p className="mt-2">
              Si el usuario conecta Google, Nexus solicita acceso para enviar y crear borradores de
              correo comercial, leer respuestas de los prospectos contactados, consultar
              disponibilidad y crear o actualizar reuniones. No usamos Gmail ni Calendar para
              publicidad, no vendemos esos datos y no los usamos para entrenar modelos de IA de
              terceros de forma independiente al servicio prestado a esa empresa.
            </p>
            <p className="mt-2">
              Cumplimos la{' '}
              <a
                className="font-semibold text-red-700 underline"
                href="https://developers.google.com/terms/api-services-user-data-policy"
              >
                Google API Services User Data Policy
              </a>
              , incluida la política Limited Use: los datos de Gmail se usan solo para las
              funciones visibles de outreach y seguimiento de reuniones, no para servir anuncios.
            </p>
            <p className="mt-2">
              El usuario puede desconectar Google en Configuración → Integraciones. Eso elimina los
              tokens de Nexus y pide a Google que los revoque. Para borrar el workspace, escribir a
              joaquin@costguard.com.ar.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Subprocesadores</h2>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>Google — autenticación OAuth, Gmail y Calendar.</li>
              <li>Railway y Vercel — hosting de API y aplicación.</li>
              <li>OpenAI — redacción y clasificación de mensajes, cuando la empresa usa esas funciones.</li>
              <li>Prospeo y Brave Search — enriquecimiento y búsqueda de empresas/contactos.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Conservación</h2>
            <p className="mt-2">
              Conservamos los datos mientras la empresa tenga un workspace activo y el tiempo
              adicional necesario para soporte, seguridad y obligaciones legales. Al terminar el
              contrato, la empresa puede pedir la eliminación de su workspace.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Extensión Chrome</h2>
            <p className="mt-2">
              El tratamiento específico de la extensión de outreach está en{' '}
              <a className="font-semibold text-red-700 underline" href="/privacidad-extension">
                /privacidad-extension
              </a>
              .
            </p>
          </section>
        </div>
      </article>
    </main>
  )
}
