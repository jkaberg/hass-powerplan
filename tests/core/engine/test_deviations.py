"""D7 §9 26, 29 - the month's deviations, counted in the tick (D7 §5.10).

Comfort time and episodes, deadlines met and missed, and windows over their
ceiling, per local calendar month. The counters are observations: nothing but
`_count_deviations` writes `RuntimeState.deviations`.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.engine import DeviationsState, _count_deviations
from custom_components.powerplan.core.model import Demand, Urgency

OSLO = ZoneInfo("Europe/Oslo")
T0 = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)


def _demand(
    deadline: datetime | None = None,
    required_kwh: float | None = None,
    *,
    wants: bool | None = None,
) -> Demand:
    return Demand(
        wants=deadline is not None if wants is None else wants,
        required_kwh=required_kwh,
        deadline=deadline,
        min_w=0.0,
        max_w=11000.0,
        urgency=Urgency.NORMAL,
        comfort=None,
        price_sensitive=True,
        reason="test",
    )


def _tick(
    prev: DeviationsState,
    now: datetime,
    since: datetime | None,
    *,
    comfort: frozenset[str] = frozenset(),
    demands: dict[str, Demand] | None = None,
    closed_over: tuple[bool, ...] = (),
) -> DeviationsState:
    return _count_deviations(
        prev,
        now=now,
        tz=OSLO,
        since=since,
        comfort=comfort,
        demands=demands or {},
        closed_over=closed_over,
    )


def test_26a_comfort_counts_seconds_and_one_episode_per_edge() -> None:
    """Three ticks 10 s apart below the floor: 20 s and one episode; a 20-min gap counts 5 min."""
    below = frozenset({"floor"})
    state = _tick(DeviationsState(), T0, None, comfort=below)
    state = _tick(state, T0 + timedelta(seconds=10), T0, comfort=below)
    state = _tick(state, T0 + timedelta(seconds=20), T0 + timedelta(seconds=10), comfort=below)
    assert state.comfort_s["floor"] == 20.0
    assert state.comfort_n["floor"] == 1

    later = T0 + timedelta(minutes=20, seconds=20)
    state = _tick(state, later, T0 + timedelta(seconds=20), comfort=below)
    assert state.comfort_s["floor"] == 20.0 + 300.0, "a gap in the ticks is not counted"
    state = _tick(state, later + timedelta(seconds=10), later)
    state = _tick(
        state, later + timedelta(seconds=20), later + timedelta(seconds=10), comfort=below
    )
    assert state.comfort_n["floor"] == 2, "a new edge into it is a new episode"
    assert state.total == 2


def test_26b_a_deadline_is_met_or_missed_by_what_was_left_before_it() -> None:
    """0.05 kWh left is met, 2 kWh is missed, and one withdrawn before it is not judged."""
    deadline = T0 + timedelta(hours=1)
    state = _tick(
        DeviationsState(),
        T0,
        None,
        demands={
            "ev": _demand(deadline, 0.05),
            "tank": _demand(deadline, 2.0),
            "gone": _demand(deadline, 5.0),
        },
    )
    # The car is unplugged before its deadline: the demand no longer carries one.
    state = _tick(
        state,
        T0 + timedelta(minutes=30),
        T0,
        demands={"ev": _demand(deadline, 0.05), "tank": _demand(deadline, 2.0), "gone": _demand()},
    )
    state = _tick(
        state, deadline, T0 + timedelta(minutes=30), demands={"ev": _demand(), "tank": _demand()}
    )
    assert state.deadline_met == {"ev": 1}
    assert state.deadline_missed == {"tank": 1}
    assert "gone" not in state.deadline_met
    assert "gone" not in state.deadline_missed
    assert state.deadline_at == {}, "a judged deadline is not judged again"
    assert state.total == 1


def test_26c_windows_over_their_ceiling_and_the_new_month() -> None:
    """Windows count as the tick hands them over; the first tick of October starts from zero."""
    state = _tick(DeviationsState(), T0, None, closed_over=(True,))
    state = _tick(state, T0 + timedelta(hours=1), T0, closed_over=(False,))
    assert (state.over_windows, state.windows, state.month) == (1, 2, "2026-09")

    october = datetime(2026, 9, 30, 22, 0, tzinfo=UTC)  # 00:00 on 1 October in Oslo
    state = _tick(state, october, october - timedelta(seconds=10))
    assert (state.month, state.over_windows, state.windows, state.total) == ("2026-10", 0, 0, 0)


def test_26d_nothing_but_count_deviations_writes_them() -> None:
    """The counters are observations (INV-68's rule): one writer in the engine."""
    source = Path("custom_components/powerplan/core/engine.py").read_text(encoding="utf-8")
    writers = [
        ast.unparse(keyword.value.func)
        for call in ast.walk(ast.parse(source))
        if isinstance(call, ast.Call) and ast.unparse(call.func) == "replace"
        for keyword in call.keywords
        if keyword.arg == "deviations" and isinstance(keyword.value, ast.Call)
    ]
    assert writers == ["_count_deviations"]
    readers = [
        path
        for path in Path("custom_components/powerplan/core").rglob("*.py")
        if path.name != "engine.py" and "runtime.deviations" in path.read_text(encoding="utf-8")
    ]
    assert readers == []


def test_29_a_deadline_is_judged_only_while_the_load_asks_for_it() -> None:
    """D-0709: a car not connected, or a session the car ended, has withdrawn its deadline.

    The reference house counted five "misses" on nights the charger read
    `disconnected` at 06:00, and one for a car that stopped itself at 98 %
    (`design/reviews/field-audit-2026-10.md` §2). A demand that no longer wants
    energy while its requirement is unmet isn't judged; one whose requirement is
    met is, and counts met. A tank's `wants` is `level < target - 1 K`.
    """
    deadline = T0 + timedelta(hours=8)
    before = deadline - timedelta(seconds=10)
    demands = {
        # No car: the departure and the 40 kWh stay on the demand, `wants` is off.
        "no_car": _demand(deadline, 40.0, wants=False),
        "short": _demand(deadline, 2.0),
        "full": _demand(deadline, 0.0, wants=False),
        # The car ended its session at 98 % of a 100 % target.
        "car_stopped": _demand(deadline, 1.9, wants=False),
        # A tank inside its 1 K band (74.3 against 75 °C) is at its target.
        "tank_in_band": _demand(deadline, 0.0, wants=False),
        # A tank below it (73.9 °C) still owes 0.38 kWh and wants it.
        "tank_below": _demand(deadline, 0.38),
    }
    state = _tick(DeviationsState(), T0, None, demands=demands)
    state = _tick(state, before, T0, demands=demands)
    state = _tick(state, deadline, before, demands={})
    assert state.deadline_met == {"full": 1, "tank_in_band": 1}
    assert state.deadline_missed == {"short": 1, "tank_below": 1}
    assert state.deadline_at == {}


def test_29b_a_car_unplugged_before_its_deadline_withdraws_it() -> None:
    """Plugged in and owing at 22:00, unplugged at 05:30: the 06:00 deadline isn't judged."""
    deadline = T0 + timedelta(hours=8)
    state = _tick(DeviationsState(), T0, None, demands={"ev": _demand(deadline, 20.0)})
    unplugged = deadline - timedelta(minutes=30)
    state = _tick(state, unplugged, T0, demands={"ev": _demand(deadline, 6.0, wants=False)})
    state = _tick(state, deadline, unplugged, demands={"ev": _demand(deadline, 6.0, wants=False)})
    assert state.deadline_met == {}
    assert state.deadline_missed == {}
