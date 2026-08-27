"""Distribución de colas operativas por día según límites diarios."""

from dataclasses import dataclass

from app.services import queue_day_schedule as qds


@dataclass
class _Item:
    name: str
    kind: str = "dm"


def test_schedule_single_budget_all_fit_today():
    items = [_Item(f"p{i}") for i in range(5)]
    buckets = qds.schedule_single_budget(items, daily_limit=10, remaining_today=10)
    assert len(buckets) == 1
    assert buckets[0][0] == 0
    assert len(buckets[0][1]) == 5


def test_schedule_single_budget_spills_to_next_days():
    items = [_Item(f"p{i}") for i in range(25)]
    buckets = qds.schedule_single_budget(items, daily_limit=10, remaining_today=3)
    assert buckets[0] == (0, items[:3])
    assert buckets[1] == (1, items[3:13])
    assert buckets[2] == (2, items[13:23])
    assert buckets[3] == (3, items[23:25])
    assert qds.deferred_count(buckets) == 22


def test_schedule_dual_budget_linkedin_style():
    items = [
        _Item("c1", "connect"),
        _Item("c2", "connect"),
        _Item("m1", "message"),
        _Item("c3", "connect"),
        _Item("m2", "message"),
    ]
    buckets = qds.schedule_dual_budget(
        items,
        classify=lambda t: t.kind,
        primary_limit=2,
        primary_remaining_today=1,
        secondary_limit=2,
        secondary_remaining_today=2,
        primary_kinds=frozenset({"connect"}),
    )
    assert len(buckets[0][1]) == 3  # c1 + m1 + m2 (1 invite + 2 dms hoy)
    day0_kinds = [t.kind for t in buckets[0][1]]
    assert day0_kinds.count("connect") == 1
    assert day0_kinds.count("message") == 2
    assert len(buckets[1][1]) == 2
    day1_names = [t.name for t in buckets[1][1]]
    assert day1_names == ["c2", "c3"]


def test_day_label():
    assert qds.day_label(0) == "Hoy"
    assert qds.day_label(1) == "Mañana"
