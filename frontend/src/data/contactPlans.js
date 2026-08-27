/** Prospecciones y precios por plan comercial. */
export const CONTACT_PLAN_CREDITS = {
  starter: 3_500,
  growth: 10_000,
  scaler: 15_000,
  elite: 20_000,
  custom: 0,
}

export const CONTACT_PLAN_PRICE_USD = {
  starter: 300,
  growth: 500,
  scaler: 700,
  elite: 900,
  custom: 0,
}

export const CUSTOM_PRICE_PER_CREDIT_USD = 0.03

export function planContactCredits(planKey, apiValue) {
  const fromApi = Number(apiValue)
  if (Number.isFinite(fromApi) && fromApi > 0) {
    return fromApi
  }
  const key = String(planKey || 'starter').trim().toLowerCase()
  return CONTACT_PLAN_CREDITS[key] ?? CONTACT_PLAN_CREDITS.starter
}
