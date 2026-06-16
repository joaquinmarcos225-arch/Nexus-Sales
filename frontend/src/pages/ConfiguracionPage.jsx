import { ComingSoonCard } from '../components/ComingSoonCard'
import { PageHeader } from '../layout/PageHeader'

export default function ConfiguracionPage() {
  return (
    <>
      <PageHeader
        title="Configuración"
        description="Preferencias de cuenta, integraciones y seguridad."
      />
      <ComingSoonCard />
    </>
  )
}
