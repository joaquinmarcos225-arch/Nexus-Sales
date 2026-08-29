"""Paquetes ad-hoc de créditos (top-up fuera del ciclo mensual)."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.credit_plans import CUSTOM_PRICE_PER_CREDIT_USD


@dataclass(frozen=True)
class CreditPackDef:
    key: str
    label: str
    credits: int
    description: str

    @property
    def price_usd(self) -> float:
        return round(self.credits * CUSTOM_PRICE_PER_CREDIT_USD, 2)

    @property
    def price_per_credit_usd(self) -> float:
        return CUSTOM_PRICE_PER_CREDIT_USD

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "credits": self.credits,
            "price_usd": self.price_usd,
            "price_per_credit_usd": self.price_per_credit_usd,
            "description": self.description,
        }


CREDIT_PACKS: dict[str, CreditPackDef] = {
    "s": CreditPackDef(
        key="s",
        label="Pequeño",
        credits=100,
        description="~100 secuencias completas (mail + WA + LinkedIn).",
    ),
    "m": CreditPackDef(
        key="m",
        label="Mediano",
        credits=500,
        description="Ideal para rellenar el mes o una campaña grande.",
    ),
    "l": CreditPackDef(
        key="l",
        label="Grande",
        credits=1000,
        description="Alto volumen sin cambiar de plan mensual.",
    ),
}

LOW_CREDIT_THRESHOLD = 10


def list_credit_packs() -> list[dict]:
    return [p.to_dict() for p in CREDIT_PACKS.values()]


def get_credit_pack(key: str) -> CreditPackDef | None:
    return CREDIT_PACKS.get((key or "").strip().lower())


def payment_instructions_text() -> str:
    import os

    raw = (os.getenv("NEXUS_PAYMENT_INSTRUCTIONS") or "").strip()
    if raw:
        return raw
    return (
        "Te contactamos por Soporte con el link o datos de transferencia. "
        "Los créditos se acreditan en el pool de la empresa una vez confirmado el pago."
    )
