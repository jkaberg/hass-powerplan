"""D4 §9 11 - the legionella cycle completes whatever the price (INV-54).

INV-54 is the one invariant in D4 that a *price* may not argue with: a tank held
down at 45 °C to save peaks is a tank kept in the temperature band where
*Legionella pneumophila* multiplies, so the cycle to 65 °C is placed by price but
its deadline is absolute.

The adversarial case is therefore not "an expensive hour" but **a plan that says
stand still, every slot, for a week**: `Desired.SHED` on every tick, a grant of
zero watts and no shed in sight (INV-25). Under that plan the only thing that can
still bring the tank to 65 °C is the type refusing to give the plan a vote once
the cycle is due, which is exactly what §5.12 asks for and what this file pins.

The tank is `tests/sim/tank.py` - two stratified layers, a mechanical thermostat
with a 5 K differential, and real showers at seeded times. A static mock would
have passed a controller that never noticed the differential drops the water out
of the pasteurisation band between reheats (which is why the cycle is driven at
the charge setpoint, `design/DECISIONS.md` D-0203).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import Desired, LoadState, PresenceMode, Urgency
from custom_components.powerplan.core.loads.types.water_heater import (
    LEGIONELLA_BAND_K,
    LEGIONELLA_URGENT_H,
)
from tests.core.loads.conftest import (
    OSLO,
    grant,
    load_ctx,
    load_from,
    sim_command,
    sim_env,
    tank_reads,
)
from tests.sim.tank import DrawProfile, TankSim

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Command, Load

#: A Tuesday in February at 01:07 local - never `HH:00:00` (HLD §7.1).
START = datetime(2026, 2, 3, 1, 7, 13, tzinfo=OSLO)

#: Five minutes is fine for a tank: the element is 3 kW and the fastest clock in
#: play is the 600 s min-on. 8 days of it is 2 304 ticks and runs in about a
#: second, which keeps `tests/core` fast.
TICK_S = 300.0


def tank_sim(**kwargs: Any) -> TankSim:
    """Return the reference house's 300 L tank, drawn on by two people."""
    options: dict[str, Any] = {
        "top_c": 47.0,
        "bottom_c": 45.0,
        "setpoint_c": 45.0,
        "draw": DrawProfile(persons=2, seed=11),
    }
    options.update(kwargs)
    return TankSim(**options)


def week(
    load: Load,
    state: LoadState,
    sim: TankSim,
    *,
    days: float = 8.0,
    desired: Desired | None = Desired.SHED,
    presence: PresenceMode = PresenceMode.HOME,
) -> tuple[LoadState, list[tuple[datetime, float]]]:
    """Run the tank under a plan that never says charge, and log the setpoints.

    The grant is zero watts and **not** a shed: an always-expensive curve is a
    plan statement, not a capacity one (INV-25). Nothing here decides anything
    the allocator would - a `SETPOINT` load's lever is the number it is told, so
    what the tank does with a week of "stand still" is entirely the type's.
    """
    command: Command | None = None
    written: list[tuple[datetime, float]] = []
    ticks = int(days * 86400 / TICK_S)
    for index in range(ticks):
        at = START + timedelta(seconds=TICK_S * index)
        step = sim.step(TICK_S, sim_command(command), sim_env(at, outdoor_c=-4.0))
        ctx = load_ctx(
            now=at,
            reads=tank_reads(sim, step, at),
            presence=presence,
            desired=desired,
            zone=OSLO,
        )
        state, _ = load.observe(state, ctx)
        state, result = load.apply(grant(0.0), state, ctx)
        command = result.command
        if command is not None:
            written.append((at, float(command.value)))
    return state, written


@pytest.mark.inv("INV-54")
def test_11_the_cycle_completes_under_a_plan_that_never_says_charge() -> None:
    """INV-54: placed by price, but its deadline is absolute (§5.12)."""
    load = load_from("water_heater", {"legionella": "powerplan"})
    sim = tank_sim()
    state = LoadState()

    state, written = week(load, state, sim)

    params = load.config.params
    interval = timedelta(days=float(params["legionella_interval_days"]))
    completed = state.legionella_last_completed
    assert completed is not None
    assert completed > START, "the cycle ran: the adoption stamp has moved on"
    assert completed <= START + interval, "and it ran before the interval ran out (INV-54)"
    assert sim.legionella_cycles >= 1, "the water itself reached the band, not just our model"
    assert state.legionella_in_progress_since is None, "a completed cycle is not still running"

    drive = float(params["legionella_drive_c"])
    assert max(value for _, value in written) == pytest.approx(drive)
    assert drive >= float(params["legionella_temp_c"]) + LEGIONELLA_BAND_K, (
        "the cycle is driven above the pasteurisation temperature, because a "
        "mechanical thermostat's own differential would otherwise drop the water "
        "out of the band between reheats"
    )


@pytest.mark.inv("INV-54")
def test_11b_the_plan_loses_its_vote_six_hours_before_the_deadline() -> None:
    """Price-sensitive through the lead window, not a minute past `due_at − 6 h`."""
    load = load_from("water_heater")
    state = LoadState(legionella_last_completed=START)
    params = load.config.params
    due = START + timedelta(days=float(params["legionella_interval_days"]))
    lead_h = float(params["legionella_lead_h"])

    def at(moment: datetime) -> Any:
        ctx = load_ctx(now=moment, reads=tank_reads(tank_sim(), _cold_step(), moment), zone=OSLO)
        fresh, observation = load.observe(state, ctx)
        deadlines = [when for when, _ in load.device_type.deadlines(load, fresh, ctx)]
        return load.device_type.legionella(load, fresh, ctx), observation.demand, deadlines

    early_leg, early_demand, early_deadlines = at(due - timedelta(hours=lead_h + 1.0))
    assert not early_leg.active, "outside the lead window the cycle is not asking yet"
    assert early_demand.urgency is not Urgency.LEGIONELLA
    assert due not in early_deadlines

    lead_leg, lead_demand, lead_deadlines = at(due - timedelta(hours=lead_h - 1.0))
    assert lead_leg.active
    assert not lead_leg.mandatory
    assert lead_demand.urgency is Urgency.LEGIONELLA
    assert lead_demand.price_sensitive, "inside the lead window the price still chooses the slot"
    assert due in lead_deadlines, "the cycle's own deadline is one of the tank's (§5.12)"

    late_leg, late_demand, _ = at(due - timedelta(hours=LEGIONELLA_URGENT_H - 0.5))
    assert late_leg.mandatory
    assert not late_demand.price_sensitive, "past due − 6 h the plan has no vote (§5.12)"


@pytest.mark.inv("INV-54")
def test_11c_a_heater_with_its_own_programme_is_skipped() -> None:
    """A heater with its own programme: powerplan never runs one (§5.12)."""
    load = load_from("water_heater", {"legionella": "built_in"})
    sim = tank_sim()

    state, written = week(load, LoadState(), sim, days=9.0)

    assert load.config.params["legionella"] is False
    assert state.legionella_in_progress_since is None
    assert state.legionella_last_completed is None, "no clock is started for a cycle we do not run"
    comfort = float(load.config.params["comfort_min_c"])
    assert max((value for _, value in written), default=comfort) <= comfort + 0.01, (
        "a skipped cycle never drives the tank above its comfort setpoint"
    )
    assert sim.setpoint_c == pytest.approx(comfort), "and the tank is left holding that"

    ctx = load_ctx(now=START, reads=tank_reads(sim, _cold_step(), START), zone=OSLO)
    status = load.device_type.legionella(load, state, ctx)
    assert status.skipped
    assert status.due_at is None


def test_11d_dont_know_means_powerplan_does_it() -> None:
    """The third option is the default in disguise (§6.3)."""
    assert (
        load_from("water_heater", {"legionella": "dont_know"}).config.params["legionella"] is True
    )


@pytest.mark.inv("INV-54")
def test_11e_a_cycle_that_cannot_finish_in_time_says_so() -> None:
    """An element too small, or a week of sheds, is a warning and never a silence."""
    load = load_from("water_heater", {"element_kw": "1.5", "litres": "400"})
    params = load.config.params
    due = START + timedelta(days=float(params["legionella_interval_days"]))
    state = LoadState(legionella_last_completed=START)
    moment = due - timedelta(minutes=30)
    ctx = load_ctx(now=moment, reads=tank_reads(tank_sim(), _cold_step(), moment), zone=OSLO)

    state, _ = load.observe(state, ctx)
    status = load.device_type.legionella(load, state, ctx)

    assert status.in_progress, "it is trying"
    assert status.at_risk, "and it cannot make it: 400 L to 70 °C on 1.5 kW in half an hour"


@pytest.mark.inv("INV-54")
def test_11f_the_clock_starts_at_adoption_and_never_slides() -> None:
    """A tank with no history is presumed pasteurised the day we take it over.

    Before powerplan the tank sat at its own dial, well above 60 °C, so the
    honest anchor is "now" - and it is written down once, so the deadline does
    not slide away with every tick (`design/DECISIONS.md` D-0203).
    """
    load = load_from("water_heater")
    sim = tank_sim()
    first = load_ctx(now=START, reads=tank_reads(sim, _cold_step(), START), zone=OSLO)
    state, _ = load.observe(LoadState(), first)
    assert state.legionella_last_completed == START

    later = START + timedelta(hours=6)
    second = load_ctx(now=later, reads=tank_reads(sim, _cold_step(), later), zone=OSLO)
    state, _ = load.observe(state, second)
    assert state.legionella_last_completed == START, "the anchor is written once, not every tick"


@pytest.mark.inv("INV-64")
def test_11g_the_state_a_shed_tank_is_left_in_is_one_a_household_can_live_in() -> None:
    """INV-64: a shed tank sits at its comfort floor, not at a pulled relay."""
    load = load_from("water_heater")
    state = LoadState(legionella_last_completed=START)
    charging = tank_sim(setpoint_c=75.0)
    step = charging.step(0.0, None, sim_env(START, outdoor_c=-4.0))
    ctx = load_ctx(now=START, reads=tank_reads(charging, step, START), zone=OSLO)

    _, result = load.apply(grant(0.0, shed=True, shed_reason="stage 2", stage=2), state, ctx)

    floor = float(load.config.params["comfort_min_c"])
    assert result.command is not None
    assert float(result.command.value) == pytest.approx(floor)
    assert floor >= 45.0, "45 °C is the floor below which storage favours legionella (§5.12)"


def _cold_step() -> Any:
    """Return one simulator report of a tank sitting at its floor, element off."""
    sim = tank_sim()
    return sim.step(0.0, None, sim_env(START, outdoor_c=-4.0))


@pytest.mark.inv("INV-54")
def test_11h_the_hold_accumulates_only_while_the_water_is_in_the_band() -> None:
    """Sixty minutes **at** temperature, and the clock survives a restart.

    The accumulator is in the persisted state, so a reload mid-cycle does not
    start the hour again; and a tank that falls out of the band loses it, because
    pasteurisation is a sustained temperature and not an average one.
    """
    load = load_from("water_heater")
    state = LoadState(legionella_last_completed=START - timedelta(days=7))
    sim = tank_sim(top_c=72.0, bottom_c=71.0, setpoint_c=70.0)
    for index in range(4):
        at = START + timedelta(seconds=TICK_S * index)
        step = sim.step(TICK_S, None, sim_env(at, outdoor_c=-4.0))
        ctx = load_ctx(now=at, reads=tank_reads(sim, step, at), zone=OSLO)
        state, _ = load.observe(state, ctx)

    assert state.legionella_in_progress_since == START
    held = state.learned["legionella_hold_s"].value
    assert held == pytest.approx(TICK_S * 2, abs=TICK_S)

    cold = tank_sim(top_c=50.0, bottom_c=48.0, setpoint_c=70.0)
    at = START + timedelta(seconds=TICK_S * 5)
    step = cold.step(TICK_S, None, sim_env(at, outdoor_c=-4.0))
    state, _ = load.observe(state, load_ctx(now=at, reads=tank_reads(cold, step, at), zone=OSLO))
    assert "legionella_hold_s" not in state.learned, "out of the band, the hour starts again"
