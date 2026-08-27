from pydantic import BaseModel, Field


class LinkedInMockConnectBody(BaseModel):
    """Datos del asistente de vínculo LinkedIn (sin OAuth / sin contraseña)."""

    linkedin_profile_url: str | None = Field(
        default=None,
        max_length=512,
        description="URL pública del perfil LinkedIn del SDR",
    )
    display_name: str | None = Field(
        default=None,
        max_length=120,
        description="Nombre visible del perfil LinkedIn",
    )
