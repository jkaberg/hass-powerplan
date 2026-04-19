"""D6 §9 10 - the trim removes the deficit, not the house (INV-37, INV-38).

**On the ancestor controller.** Stage 4 meant "everything off except comfort violators
and heat pumps", and that removed roughly ten kilowatts to correct a 1.4 kW excursion,
ten times in one night - each removal costing a charging session and the car's
ten-minute retry timer on top.

A deficit is a number. Cover it and stop: walk the granted loads in **ascending**
keep-priority, take back what each one **actually frees** (its reservation, not its
grant), stop the moment the deficit is covered. The EV is trimmed, never stopped:
30 → 22 → 14 → 6 A, and no lower - below 6 A there is no valid PWM duty cycle, so a
smaller number is not a smaller charge, it is a dropped session (INV-28).

INV-38's trim half: the deficit is **instantaneous**. It measures a real excess,
where the ladder's projection is smoothed.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    ShedReason,
    TrimCfg,
    allocate,
    proportional_trim,
)
from custom_components.powerplan.core.model import Grant
from tests.core.allocation.conftest import (
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    loop_view,
    meter,
    pump_view,
    tank_view,
)

CFG = TrimCfg()


def _grant(w: float) -> Grant:
    return Grant(
        w=w, shed=False, shed_reason=None, stop_ok=False, stage=3, blunt=False, capped_by=()
    )


@pytest.mark.inv("INV-37")
def test_10_a_fourteen_hundred_watt_deficit_does_not_remove_ten_kilowatts() -> None:
    """The charger comes down 8 A and the walk stops; nothing else is touched."""
    loads = [pump_view(), loop_view(), tank_view(), ev_view()]
    grants = {
        "pump": _grant(3000.0),
        "loop_bath": _grant(960.0),
        "tank": _grant(3000.0),
        "ev": _grant(32.0 * W_PER_AMP),
    }
    views = {
        "pump": controlled("pump", measured_w=2500.0),
        "loop_bath": controlled("loop_bath", measured_w=960.0),
        "tank": controlled("tank", measured_w=3000.0),
        "ev": controlled("ev", measured_w=7360.0),
    }

    after, freed = proportional_trim(loads, grants, 1400.0, frozenset(), CFG, views=views)

    assert 1650.0 <= freed <= 2000.0
    assert after["ev"].w < grants["ev"].w
    assert after["tank"].w == pytest.approx(3000.0)
    assert after["loop_bath"].w == pytest.approx(960.0)
    assert after["pump"].w == pytest.approx(3000.0)
    assert sum(g.w for g in grants.values()) - sum(g.w for g in after.values()) < 2000.0


@pytest.mark.inv("INV-37")
def test_10_the_charger_walks_down_thirty_twenty_two_fourteen_six() -> None:
    """A 1 590 W deficit plus the 250 W margin is 8 A of charger, three times over.

    30 A → 22 → 14 → 6, and the fourth trim finds nothing left to take: 6 A is the
    floor and a stop is a separate, deliberate decision (INV-28, INV-39).
    """
    ev = ev_view()
    amps = [30.0]
    grants = {"ev": _grant(30.0 * W_PER_AMP)}

    for _ in range(4):
        grants, freed = proportional_trim(
            [ev],
            grants,
            1590.0,
            frozenset(),
            CFG,
            views={"ev": controlled("ev", measured_w=grants["ev"].w)},
        )
        amps.append(round(grants["ev"].w / W_PER_AMP, 3))
        if amps[-1] == amps[-2]:
            assert freed == 0.0

    assert amps == [30.0, 22.0, 14.0, 6.0, 6.0]
    assert grants["ev"].shed is False


@pytest.mark.inv("INV-37", "INV-39")
def test_10_the_floor_is_only_left_when_a_stop_is_authorised() -> None:
    """With `stop_ok` the charger goes to 0 and lands in the shed set as "trim"."""
    ev = ev_view()
    grants = {"ev": _grant(6.0 * W_PER_AMP)}
    views = {"ev": controlled("ev", measured_w=1380.0)}

    held, held_freed = proportional_trim([ev], grants, 3000.0, frozenset(), CFG, views=views)
    stopped, stopped_freed = proportional_trim(
        [ev], grants, 3000.0, frozenset(), CFG, views=views, stop_ok={"ev": True}
    )

    assert held["ev"].w == pytest.approx(1380.0)
    assert held_freed == 0.0
    assert stopped["ev"].w == 0.0
    assert stopped["ev"].shed is True
    assert stopped["ev"].shed_reason == ShedReason.TRIM
    assert stopped_freed == pytest.approx(1380.0)


@pytest.mark.inv("INV-37")
def test_10_a_relay_frees_its_reservation_not_its_grant() -> None:
    """Taking back a 348 W paced grant frees 3 kW of real headroom (§5.2)."""
    tank = tank_view()
    grants = {"tank": _grant(348.3)}
    views = {"tank": controlled("tank", measured_w=2940.0)}

    after, freed = proportional_trim([tank], grants, 1000.0, frozenset(), CFG, views=views)

    assert after["tank"].w == 0.0
    assert after["tank"].shed_reason == ShedReason.TRIM
    assert freed == pytest.approx(3000.0)


@pytest.mark.inv("INV-37")
def test_10_a_load_inside_its_settle_window_is_skipped_unless_blunt() -> None:
    """The deficit may be our own write: wait a tick, then act."""
    tank = tank_view()
    grants = {"tank": _grant(3000.0)}
    views = {"tank": controlled("tank", measured_w=3000.0, commanded_w=3000.0, settling=True)}

    skipped, skipped_freed = proportional_trim(
        [tank], grants, 1000.0, frozenset(), CFG, views=views
    )
    forced, forced_freed = proportional_trim(
        [tank], grants, 1000.0, frozenset(), CFG, views=views, blunt=True
    )

    assert skipped["tank"].w == pytest.approx(3000.0)
    assert skipped_freed == 0.0
    assert forced["tank"].w == 0.0
    assert forced_freed == pytest.approx(3000.0)


@pytest.mark.inv("INV-37")
def test_10_protected_loads_are_not_candidates() -> None:
    """Comfort violators and heat pumps below stage 4 are off the table (§5.5)."""
    loads = [pump_view(), tank_view()]
    grants = {"pump": _grant(3000.0), "tank": _grant(3000.0)}
    views = {
        "pump": controlled("pump", measured_w=2800.0),
        "tank": controlled("tank", measured_w=3000.0),
    }

    after, freed = proportional_trim(loads, grants, 5000.0, frozenset({"tank"}), CFG, views=views)

    assert after["tank"].w == pytest.approx(3000.0)
    assert after["pump"].w == pytest.approx(3000.0)
    assert freed == 0.0


@pytest.mark.inv("INV-37")
def test_10_no_deficit_is_no_trim() -> None:
    """A negative deficit is headroom and has its own name (`headroom_w`)."""
    ev = ev_view()
    grants = {"ev": _grant(7360.0)}

    after, freed = proportional_trim([ev], grants, -2000.0, frozenset(), CFG)

    assert after == grants
    assert freed == 0.0


@pytest.mark.inv("INV-38")
def test_10_the_deficit_is_the_instantaneous_reading_not_the_smoothed_one() -> None:
    """8 000 W measured against a 6 650 W allowance is a 1 350 W deficit.

    The ladder would read 4 000 W of two-minute average and see no risk at all;
    the trim is the fast, tactical layer and it uses the meter (INV-38).
    """
    ev = ev_view()
    ctx = alloc_ctx(
        [ev],
        budget=budget_of(6650.0),
        meter_snapshot=meter(grid_w=8000.0, grid_smooth_w=4000.0),
        previous={"ev": _grant(7360.0)},
        views={"ev": controlled("ev", measured_w=7360.0)},
    )

    _grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert report.deficit_w == pytest.approx(8000.0 - 6650.0)
    assert report.headroom_w == 0.0
