"""Créditos de contacto por plan comercial + economía COGS (CostGuard)."""

from __future__ import annotations

from dataclasses import dataclass

# Alias legacy → plan vigente (filas antiguas en BD).
PLAN_ALIASES: dict[str, str] = {
    "pro": "scaler",
    "enterprise": "elite",
}


@dataclass(frozen=True)
class ContactPlanDef:
    key: str
    label: str
    monthly_contact_credits: int
    description: str
    price_usd: float
    """Precio de lista al cliente (USD / mes). Custom = 0 (se cobra por crédito)."""
    openai_usd: float
    prospeo_usd: float
    brave_usd: float
    """Costo estimado de tools por ciclo (billeteras CostGuard)."""

    @property
    def tools_cogs_usd(self) -> float:
        return round(self.openai_usd + self.prospeo_usd + self.brave_usd, 2)

    @property
    def margin_usd(self) -> float:
        if self.key == "custom":
            return 0.0
        return round(self.price_usd - self.tools_cogs_usd, 2)

    @property
    def sale_per_credit_usd(self) -> float | None:
        if self.key == "custom":
            return CUSTOM_PRICE_PER_CREDIT_USD
        if self.monthly_contact_credits <= 0:
            return None
        return round(self.price_usd / self.monthly_contact_credits, 6)


# Custom: 1 crédito Nexus = 1 secuencia mail+WA+LI.
# COGS ~$0.30 (Prospeo ~11 créditos × $0.0245 + Brave/OpenAI). Venta $0.50.
CUSTOM_PRICE_PER_CREDIT_USD = 0.50
CUSTOM_COGS_PER_CREDIT_USD = 0.30

# Precio de lista fijo; cupo = price / $0.50.
# Starter $300→600 · Growth $500→1.000 · Scaler $700→1.400 · Elite $900→1.800


def _tools_budget(credits: int) -> tuple[float, float, float]:
    """OpenAI ~2% / Prospeo ~90% / Brave ~8% del COGS triple canal."""
    total = round(credits * CUSTOM_COGS_PER_CREDIT_USD, 2)
    openai = round(total * 0.02, 2)
    prospeo = round(total * 0.90, 2)
    brave = round(total - openai - prospeo, 2)
    return openai, prospeo, brave


_STARTER_TOOLS = _tools_budget(600)
_GROWTH_TOOLS = _tools_budget(1_000)
_SCALER_TOOLS = _tools_budget(1_400)
_ELITE_TOOLS = _tools_budget(1_800)


CONTACT_PLANS: dict[str, ContactPlanDef] = {
    "starter": ContactPlanDef(
        key="starter",
        label="Starter",
        monthly_contact_credits=600,
        description="Equipos que arrancan outbound estructurado.",
        price_usd=300.0,
        openai_usd=_STARTER_TOOLS[0],
        prospeo_usd=_STARTER_TOOLS[1],
        brave_usd=_STARTER_TOOLS[2],
    ),
    "growth": ContactPlanDef(
        key="growth",
        label="Growth",
        monthly_contact_credits=1_000,
        description="Equipos en expansión con varios SDRs.",
        price_usd=500.0,
        openai_usd=_GROWTH_TOOLS[0],
        prospeo_usd=_GROWTH_TOOLS[1],
        brave_usd=_GROWTH_TOOLS[2],
    ),
    "scaler": ContactPlanDef(
        key="scaler",
        label="Scaler",
        monthly_contact_credits=1_400,
        description="Operación comercial multicanal a escala.",
        price_usd=700.0,
        openai_usd=_SCALER_TOOLS[0],
        prospeo_usd=_SCALER_TOOLS[1],
        brave_usd=_SCALER_TOOLS[2],
    ),
    "elite": ContactPlanDef(
        key="elite",
        label="Elite",
        monthly_contact_credits=1_800,
        description="Alto volumen y varios managers/equipos.",
        price_usd=900.0,
        openai_usd=_ELITE_TOOLS[0],
        prospeo_usd=_ELITE_TOOLS[1],
        brave_usd=_ELITE_TOOLS[2],
    ),
    "custom": ContactPlanDef(
        key="custom",
        label="Customized",
        monthly_contact_credits=0,
        description="Cupo a medida · USD 0,50 por prospección (mail+WA+LI).",
        price_usd=0.0,
        openai_usd=0.0,
        prospeo_usd=0.0,
        brave_usd=0.0,
    ),
}

DEFAULT_PLAN_KEY = "starter"

TOOL_KEYS = ("openai", "prospeo", "brave")


def normalize_plan_key(raw: str | None) -> str:
    key = (raw or DEFAULT_PLAN_KEY).strip().lower()
    key = PLAN_ALIASES.get(key, key)
    return key if key in CONTACT_PLANS else DEFAULT_PLAN_KEY


def plan_definition(raw: str | None) -> ContactPlanDef:
    return CONTACT_PLANS[normalize_plan_key(raw)]


def credits_for_plan(raw: str | None) -> int:
    return plan_definition(raw).monthly_contact_credits


def list_contact_plans() -> list[ContactPlanDef]:
    return list(CONTACT_PLANS.values())


def custom_tool_costs_for_credits(credits: int) -> dict[str, float]:
    """Reparte COGS custom ~$0.30/crédito (triple canal) en openai/prospeo/brave."""
    n = max(0, int(credits))
    total = round(n * CUSTOM_COGS_PER_CREDIT_USD, 2)
    # ~OpenAI 2% / Prospeo 90% / Brave 8% (móvil = 10 créditos Prospeo)
    openai = round(total * 0.02, 2)
    prospeo = round(total * 0.90, 2)
    brave = round(total - openai - prospeo, 2)
    return {"openai_usd": openai, "prospeo_usd": prospeo, "brave_usd": brave}


def plan_economics_dict(plan: ContactPlanDef, *, custom_credits: int | None = None) -> dict:
    if plan.key == "custom":
        credits = max(0, int(custom_credits or 0))
        tools = custom_tool_costs_for_credits(credits)
        price = round(credits * CUSTOM_PRICE_PER_CREDIT_USD, 2)
        cogs = round(sum(tools.values()), 2)
        return {
            "key": plan.key,
            "label": plan.label,
            "monthly_contact_credits": credits,
            "description": plan.description,
            "price_usd": price,
            "openai_usd": tools["openai_usd"],
            "prospeo_usd": tools["prospeo_usd"],
            "brave_usd": tools["brave_usd"],
            "tools_cogs_usd": cogs,
            "margin_usd": round(price - cogs, 2),
            "sale_per_credit_usd": CUSTOM_PRICE_PER_CREDIT_USD,
            "is_custom": True,
        }
    return {
        "key": plan.key,
        "label": plan.label,
        "monthly_contact_credits": plan.monthly_contact_credits,
        "description": plan.description,
        "price_usd": plan.price_usd,
        "openai_usd": plan.openai_usd,
        "prospeo_usd": plan.prospeo_usd,
        "brave_usd": plan.brave_usd,
        "tools_cogs_usd": plan.tools_cogs_usd,
        "margin_usd": plan.margin_usd,
        "sale_per_credit_usd": plan.sale_per_credit_usd,
        "is_custom": False,
    }
