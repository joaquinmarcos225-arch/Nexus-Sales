import math


def marginal_total_cost_usd(meetings: int) -> int:
    """
    Precio simulado acumulado para N reuniones (USD enteros):

    Primeras 20 @ 10 USD, siguientes 30 @ 6, siguientes 50 @ 4, resto @ 2.5.
    """
    if meetings <= 0:
        return 0

    remaining = meetings
    cost = 0.0

    take = min(remaining, 20)
    cost += take * 10.0
    remaining -= take
    if remaining <= 0:
        return int(round(cost))

    take = min(remaining, 30)
    cost += take * 6.0
    remaining -= take
    if remaining <= 0:
        return int(round(cost))

    take = min(remaining, 50)
    cost += take * 4.0
    remaining -= take
    if remaining <= 0:
        return int(round(cost))

    cost += remaining * 2.5
    return int(round(cost))


def meetings_range_from_prospects(prospect_count: int) -> tuple[int, int]:
    if prospect_count < 1:
        raise ValueError("La cantidad de prospectos debe ser al menos 1.")
    meetings_min = max(1, (prospect_count * 5) // 100)
    meetings_max = max(meetings_min, math.ceil(prospect_count * 12 / 100))
    return meetings_min, meetings_max


def estimate_campaign_metrics(prospect_count: int) -> dict[str, int | float]:
    meetings_min, meetings_max = meetings_range_from_prospects(prospect_count)
    cost_min = marginal_total_cost_usd(meetings_min)
    cost_max = marginal_total_cost_usd(meetings_max)

    meetings_mid = max(1, int(round((meetings_min + meetings_max) / 2)))
    mid_cost = marginal_total_cost_usd(meetings_mid)
    avg_unit = mid_cost / meetings_mid if meetings_mid else 0.0

    return {
        "estimated_meetings_min": meetings_min,
        "estimated_meetings_max": meetings_max,
        "estimated_cost_min": min(cost_min, cost_max),
        "estimated_cost_max": max(cost_min, cost_max),
        "estimated_avg_cost_per_meeting": round(avg_unit, 2),
    }
