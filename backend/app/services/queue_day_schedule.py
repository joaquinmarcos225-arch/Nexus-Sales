"""Distribución de tareas de cola en días según límites diarios por canal."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

_WEEKDAY_ES = (
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
)


def day_label(day_offset: int, *, now: datetime | None = None) -> str:
    """Etiqueta legible para un bucket de cola (0=hoy, 1=mañana, …)."""
    if day_offset <= 0:
        return "Hoy"
    if day_offset == 1:
        return "Mañana"
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    target = now + timedelta(days=day_offset)
    wd = _WEEKDAY_ES[target.weekday()]
    return f"{wd} {target.day}/{target.month}"


def schedule_single_budget(
    items: list[T],
    *,
    daily_limit: int,
    remaining_today: int,
) -> list[tuple[int, list[T]]]:
    """
    Reparte items secuencialmente en días con un solo cupo diario.

    Día 0 arranca con ``remaining_today``; días siguientes usan ``daily_limit`` completo.
    """
    if daily_limit <= 0:
        return [(0, list(items))] if items else []

    buckets: dict[int, list[T]] = defaultdict(list)
    day_budgets: dict[int, int] = {0: max(0, remaining_today)}

    for item in items:
        day = 0
        while True:
            if day not in day_budgets:
                day_budgets[day] = daily_limit
            if day_budgets[day] > 0:
                buckets[day].append(item)
                day_budgets[day] -= 1
                break
            day += 1

    if not buckets:
        return []
    return [(d, buckets[d]) for d in sorted(buckets)]


def schedule_dual_budget(
    items: list[T],
    *,
    classify: Callable[[T], str],
    primary_limit: int,
    primary_remaining_today: int,
    secondary_limit: int,
    secondary_remaining_today: int,
    primary_kinds: frozenset[str],
) -> list[tuple[int, list[T]]]:
    """
    Reparte items con dos cupos diarios (p. ej. LinkedIn invites + DMs).

    ``classify(item)`` devuelve la clave de cupo (p. ej. ``connect`` vs ``message``).
    Items cuya clase está en ``primary_kinds`` consumen el cupo primario; el resto, el secundario.
    """
    if not items:
        return []

    buckets: dict[int, list[T]] = defaultdict(list)
    primary_budgets: dict[int, int] = {0: max(0, primary_remaining_today)}
    secondary_budgets: dict[int, int] = {0: max(0, secondary_remaining_today)}

    def _budget_for(day: int, kind: str) -> int:
        is_primary = kind in primary_kinds
        store = primary_budgets if is_primary else secondary_budgets
        limit = primary_limit if is_primary else secondary_limit
        if day not in store:
            store[day] = limit
        return store[day]

    def _consume(day: int, kind: str) -> None:
        is_primary = kind in primary_kinds
        store = primary_budgets if is_primary else secondary_budgets
        store[day] -= 1

    for item in items:
        kind = classify(item)
        day = 0
        while True:
            if _budget_for(day, kind) > 0:
                buckets[day].append(item)
                _consume(day, kind)
                break
            day += 1

    return [(d, buckets[d]) for d in sorted(buckets)]


def deferred_count(day_buckets: list[tuple[int, list[T]]]) -> int:
    """Items programados para después de hoy."""
    return sum(len(tasks) for day, tasks in day_buckets if day > 0)
