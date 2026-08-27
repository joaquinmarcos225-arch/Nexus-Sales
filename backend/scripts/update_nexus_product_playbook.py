"""Actualiza producto Plataforma Nexus con copy completo + playbook en notas."""
from sqlalchemy import select

from app.database.session import SessionLocal, init_db
from app.models.campaign import Campaign
from app.models.product import Product

PRODUCT_NAME = "Plataforma Nexus"

VALUE_PROPOSITION = (
    "Automatiza entre un 60% y un 90% de las tareas manuales de prospección outbound, "
    "orquestando email, LinkedIn y WhatsApp en un solo flujo con IA — el SDR solo interviene "
    "cuando el prospecto muestra interés real."
)

DESCRIPTION = """
Plataforma Nexus es un software B2B de ventas outbound diseñado para equipos comerciales que hoy
pierden horas en tareas operativas en lugar de conversar con prospectos calificados.

Qué hace Nexus:
- Orquesta secuencias multicanal de 7 toques (email, LinkedIn asistido y WhatsApp) con timing
  automático y pausa inteligente cuando el prospecto responde.
- Genera borradores de mensajes con IA entrenada en el playbook del equipo (primer contacto
  impactante, seguimientos humanos, break-up y cierre).
- Crea borradores en Gmail para que el SDR revise y envíe; asiste LinkedIn (abre chat, pega
  mensaje, el humano aprieta Enviar) para evitar bloqueos de la red.
- Detecta respuestas inbound por email y LinkedIn, clasifica interés/objeciones y sugiere réplicas
  consultivas antes de empujar reunión.
- Centraliza prospectos, campañas, cola operativa del SDR y visibilidad para managers en un solo lugar.

Diferenciación frente a CRM + planillas + herramientas sueltas:
- No es solo un CRM: automatiza la ejecución del outbound, no solo el registro.
- No es solo automatización de email: integra LinkedIn manual asistido y WhatsApp en la misma secuencia.
- La IA no reemplaza al vendedor: reduce carga manual (60–90%) y deja al equipo en conversaciones
  con leads que ya mostraron señal.

Resultado típico para el equipo comercial:
- Menos tiempo cargando datos, copiando mensajes y persiguiendo follow-ups a mano.
- Más conversaciones reales con prospectos que respondieron.
- Managers con visibilidad de pipeline outbound y actividad del SDR sin perseguir reportes.
""".strip()

TARGET_NOTES = """
Problemas que resuelve:
- El SDR pierde la mayor parte del día en tareas operativas de prospección (cargar datos, copiar
  mensajes, recordar follow-ups) en lugar de vender.
- Canales dispersos: email en Gmail, LinkedIn en otra pestaña, WhatsApp aparte, sin secuencia única.
- Respuestas inbound que se pierden o tardan en contestarse; secuencia que no se pausa sola.
- Managers sin visibilidad clara de qué hizo cada SDR y en qué estado está cada prospecto.
- Primer contacto genérico que no destaca en bandejas saturadas.

Beneficios principales:
- Automatización del 60% al 90% de tareas manuales de prospección outbound.
- Secuencias multicanal coordinadas (email + LinkedIn + WhatsApp) desde un solo flujo.
- Borradores IA alineados al playbook del equipo; humano en control del envío final.
- Cola operativa: qué enviar hoy, qué respondió el prospecto, qué reunión priorizar.
- Detección y clasificación de inbound; réplicas sugeridas que responden antes de pedir demo.
- Consolidación de prospectos, campañas y reporting para el manager.

Mensaje estrella para Día 1 (impacto):
"Soy de Plataforma Nexus y te escribo porque ayudamos a empresas a automatizar entre un 60% y un 90%
de sus prospecciones outbound, para que el equipo solo intervenga cuando hay interés real."

Playbook secuencia 7 toques (días 1, 4, 7, 10, 13, 16, 19):
D1 Email impacto · D4 LinkedIn coordinar llamada · D7 WhatsApp seguimiento breve ·
D10 Email insight prospección manual · D13 LinkedIn retomar · D16 WhatsApp break-up · D19 Email cierre.
""".strip()


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        campaign = db.get(Campaign, 4)
        if not campaign:
            print("campaign 4 not found")
            return
        product = db.get(Product, campaign.product_id) if campaign.product_id else None
        if product is None:
            products = db.scalars(
                select(Product).where(
                    Product.company_id == campaign.company_id,
                    Product.name == PRODUCT_NAME,
                )
            ).all()
            product = products[0] if products else None
        if product is None:
            product = Product(
                company_id=campaign.company_id,
                name=PRODUCT_NAME,
                description=DESCRIPTION,
                value_proposition=VALUE_PROPOSITION,
                target_notes=TARGET_NOTES,
                is_active=True,
            )
            db.add(product)
            db.flush()
            campaign.product_id = product.id
            print("created product", product.id)
        else:
            product.name = PRODUCT_NAME
            product.description = DESCRIPTION
            product.value_proposition = VALUE_PROPOSITION
            product.target_notes = TARGET_NOTES
            product.is_active = True
            print("updated product", product.id)
        db.commit()
        print("OK — producto Nexus actualizado para company", campaign.company_id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
