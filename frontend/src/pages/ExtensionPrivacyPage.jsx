export default function ExtensionPrivacyPage() {
  return (
    <main className="min-h-dvh bg-slate-50 px-4 py-10 text-slate-800">
      <article className="mx-auto max-w-3xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-10">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-red-600">
          CostGuard · Nexus Sales
        </p>
        <h1 className="mt-2 text-3xl font-bold text-slate-950">
          Política de privacidad de la extensión
        </h1>
        <p className="mt-2 text-sm text-slate-500">Última actualización: 14 de agosto de 2026</p>

        <div className="mt-8 space-y-7 text-sm leading-relaxed">
          <section>
            <h2 className="text-lg font-semibold text-slate-950">Finalidad</h2>
            <p className="mt-2">
              Nexus Sales — Outreach Assist conecta la aplicación Nexus con WhatsApp Web para que
              un usuario autenticado abra conversaciones, copie mensajes preparados y confirme
              manualmente las acciones realizadas. LinkedIn se opera mediante apertura de perfiles
              y confirmación humana; la extensión no obtiene grados de conexión ni lee
              automáticamente el inbox.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Datos tratados</h2>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>Identificador de empresa y sesión de Nexus, almacenados localmente en Chrome.</li>
              <li>Identificadores de prospectos y números necesarios para abrir WhatsApp Web.</li>
              <li>
                Estado de tareas asistidas y mensajes que el usuario decide registrar en Nexus.
              </li>
              <li>Telemetría técnica mínima para diagnosticar errores de la integración.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Uso y transferencia</h2>
            <p className="mt-2">
              Los datos se envían únicamente al API de Nexus Sales para prestar el servicio a la
              empresa del usuario. CostGuard no vende datos personales, no los utiliza para
              publicidad y no los transfiere a terceros ajenos a la infraestructura necesaria del
              producto.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Permisos del navegador</h2>
            <p className="mt-2">
              La extensión solicita acceso a Nexus Sales, su API y WhatsApp Web para el flujo
              asistido iniciado por el usuario. No realiza envíos silenciosos ni instala software
              adicional. LinkedIn se abre desde la aplicación Nexus con un clic humano; este
              paquete de Chrome no lee el inbox ni el grado de conexión.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Conservación y seguridad</h2>
            <p className="mt-2">
              Las credenciales de sesión se guardan en el almacenamiento protegido de la extensión
              y pueden eliminarse cerrando sesión o desinstalándola. Los registros del servicio se
              conservan según la relación contractual de la empresa con CostGuard.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-slate-950">Contacto</h2>
            <p className="mt-2">
              La política general de Nexus Sales está en{' '}
              <a className="font-semibold text-red-700 underline" href="/privacidad">
                /privacidad
              </a>
              . Para consultas, acceso o eliminación de datos:{' '}
              <a className="font-semibold text-red-700 underline" href="mailto:joaquin@costguard.com.ar">
                joaquin@costguard.com.ar
              </a>
              .
            </p>
          </section>
        </div>
      </article>
    </main>
  )
}
