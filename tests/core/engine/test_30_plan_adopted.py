"""D7 §9 30 - `plan_adopted` on a change the household can see (D7 §5.2, D-0714).

The reference house fired `plan_adopted` 3 277 times in eleven days: a floor's
temperature moved its digest, a charging car its requirement
(`design/reviews/field-audit-2026-10.md` §6).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.powerplan.core.engine import _announced
from custom_components.powerplan.core.model import PlanMode

T0 = datetime(2026, 10, 1, 20, 0, 20, tzinfo=UTC)


def _plan(
    *,
    mode: PlanMode = PlanMode.PRICE,
    covered: bool = True,
    kwh: float = 3.0,
    start: datetime | None = T0 + timedelta(hours=4),
) -> SimpleNamespace:
    return SimpleNamespace(
        mode=mode, covered=covered, planned_kwh=kwh, next_active=lambda _now: start
    )


def test_30_plan_adopted_fires_on_a_change_the_household_can_see() -> None:
    """Twelve re-cuts with the same mode, coverage, start and energy fire once."""
    edges: dict[str, str] = {}
    fired = [
        _announced(
            edges, "floor", _plan(kwh=3.0 + 0.04 * cycle), T0 + timedelta(minutes=15 * cycle)
        )
        for cycle in range(12)
    ]
    assert fired == [True] + [False] * 11

    now = T0 + timedelta(hours=3)
    assert _announced(edges, "floor", _plan(start=T0 + timedelta(hours=4, minutes=15)), now)
    assert _announced(
        edges, "floor", _plan(mode=PlanMode.URGENT, start=T0 + timedelta(hours=4, minutes=15)), now
    )
    assert _announced(
        edges,
        "floor",
        _plan(covered=False, mode=PlanMode.URGENT, start=T0 + timedelta(hours=4, minutes=15)),
        now,
    )


def test_30b_planned_energy_beyond_half_a_kwh_and_a_quarter() -> None:
    """A charging car's requirement falls: fired at a 25 % step, not at every 2 %."""
    edges: dict[str, str] = {}
    assert _announced(edges, "ev", _plan(kwh=40.0), T0)
    assert not _announced(edges, "ev", _plan(kwh=35.0), T0)
    assert _announced(edges, "ev", _plan(kwh=29.0), T0)
    small: dict[str, str] = {}
    assert _announced(small, "tank", _plan(kwh=1.0), T0)
    assert not _announced(small, "tank", _plan(kwh=1.4), T0), "under 0.5 kWh"
    assert _announced(small, "tank", _plan(kwh=1.6), T0)


def test_30c_a_running_plan_starts_now() -> None:
    """A plan in its active slot reads its start as `now`, which doesn't move on its own."""
    edges: dict[str, str] = {}
    assert _announced(edges, "ev", _plan(start=T0), T0)
    later = T0 + timedelta(minutes=15)
    assert not _announced(edges, "ev", _plan(start=later), later)
    assert _announced(edges, "ev", _plan(start=None), later + timedelta(minutes=15))
