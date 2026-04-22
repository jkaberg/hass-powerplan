"""D11 §9 4, 5, 14 - the counterfactual, against the simulators and the modes.

The savings figure is a model, and it is the number a household will quote (PLAN §6
R10). So the shadows are checked against the `tests/sim` houses, which are
deliberately one node richer than the store models the planner uses (D9 §2): the
slab simulator separates the screed from the room air and loses heat downwards, the
heat pump runs a Carnot mechanism with real defrost cycles. A shadow that only
agreed with itself would prove nothing.

What the shadow honours is the household's intent - the target profile, the
plug-in, a force. What it does not honour is powerplan's plan. That difference is
the savings, and each test below pins one half of it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.accounting import (
    ClosedSlot,
    LoadParams,
    SavingsConfidence,
    ShadowCtx,
    SlotConfidence,
    StoreKind,
    shadow_for,
)
from custom_components.powerplan.core.loads.base import effective_mode
from custom_components.powerplan.core.loads.stores import DEFAULT_COP_CURVES, SlabStore
from custom_components.powerplan.core.model import Mode
from tests.core.accounting.conftest import (
    ORDINARY,
    OSLO,
    closed_slot,
    curve,
    demand,
    local,
    shadow_ctx,
    site,
)
from tests.sim.heatpump import HeatPumpSim
from tests.sim.slab import SlabSim
from tests.sim.weather import WeatherSim

STEP_S = 60.0

#: The bathroom floor of the reference house: 20 m², 80 W/m², 50 mm of screed.
AREA_M2 = 20.0
NAMEPLATE_W = AREA_M2 * 80.0
SWING_K = 1.0
TARGET_C = 22.0

#: D11 §5.3's tolerance against the richer simulator (D11 §9 4).
TOLERANCE = 0.10

#: Two January days: the first is history D10 would fit a loss coefficient from,
#: the second is the day under test. Fitting and testing on one day would make the
#: agreement a tautology.
FIT_DAY = date(2027, 1, 14)
TEST_DAY = date(2027, 1, 15)

#: A mild May day on which the heat-pump simulator does not defrost (its coil ices
#: below +3 °C). Defrost is deliberately not modelled by the shadow (D11 §5.3), and
#: `test_04d` measures the gap it opens rather than hiding it.
MILD_DAY = date(2027, 5, 5)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return the UTC instants of local midnight and the next local midnight."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=OSLO).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=OSLO).astimezone(
        UTC
    )
    return start, end


def hour_slot(start: datetime) -> ClosedSlot:
    """Return the bare `ClosedSlot` a shadow needs: a start and a length."""
    return ClosedSlot(
        start_utc=start,
        minutes=60,
        import_kwh=0.0,
        export_kwh=0.0,
        site_confidence=SlotConfidence.EXACT,
        loads={},
    )


# --------------------------------------------------------------------------- #
# 4 - the thermostat shadows against the simulators
# --------------------------------------------------------------------------- #


def _run_slab(day: date, weather: WeatherSim) -> tuple[float, float]:
    """Run the slab simulator uncontrolled for a local day.

    Returns `(kWh, UA)` where UA is the loss coefficient D10 would fit from this
    day: mean power over mean (screed − outdoor), which is the one-node coefficient
    the planner and the shadow both use.
    """
    sim = SlabSim(area_m2=AREA_M2, setpoint_c=TARGET_C, screed_c=TARGET_C, room_c=TARGET_C - 1.0)
    start, end = day_bounds(day)
    cursor = start
    powers: list[float] = []
    diffs: list[float] = []
    while cursor < end:
        env = weather.env_at(cursor)
        reads = sim.step(STEP_S, None, env)
        powers.append(reads.power_w)
        diffs.append(sim.screed_c - env.outdoor_c)
        cursor += timedelta(seconds=STEP_S)
    return sim.energy_in_kwh, (sum(powers) / len(powers)) / (sum(diffs) / len(diffs))


def _run_slab_shadow(
    day: date,
    weather: WeatherSim,
    ua_w_per_k: float,
    *,
    target_at: dict[int, float] | None = None,
) -> tuple[float, list[float]]:
    """Step the slab shadow through a local day, hour by hour."""
    shadow = shadow_for(StoreKind.SLAB)
    assert shadow is not None
    params = LoadParams(
        kind=StoreKind.SLAB,
        nameplate_w=NAMEPLATE_W,
        store=SlabStore(area_m2=AREA_M2, screed_mm=50.0, loss_coeff_w_per_k=ua_w_per_k, max_c=27.0),
        band_k=SWING_K,
        loss_coeff_w_per_k=ua_w_per_k,
    )
    start, end = day_bounds(day)
    state = shadow.init(TARGET_C, start, ShadowCtx(params=params, target=TARGET_C))
    per_hour: list[float] = []
    cursor = start
    while cursor < end:
        local_hour = cursor.astimezone(OSLO).hour
        target = (target_at or {}).get(local_hour, TARGET_C)
        state, kwh = shadow.step(
            state,
            hour_slot(cursor),
            ShadowCtx(params=params, target=target, outdoor_c=weather.env_at(cursor).outdoor_c),
        )
        per_hour.append(kwh)
        cursor += timedelta(hours=1)
    return sum(per_hour), per_hour


@pytest.mark.inv("INV-69")
def test_04_the_slab_shadow_is_within_ten_percent_of_the_slab_simulator() -> None:
    """The planner's own one-node physics against the two-node house (D9 §2)."""
    weather = WeatherSim(seed=7)
    _, ua = _run_slab(FIT_DAY, weather)
    simulated, _ = _run_slab(TEST_DAY, weather)
    shadowed, _ = _run_slab_shadow(TEST_DAY, weather, ua)

    # The coefficient comes from the day before, so the agreement is a prediction.
    assert 5.0 < ua < 30.0, f"a 20 m² floor's fitted UA should be a few W/K, got {ua:.1f}"
    assert shadowed == pytest.approx(simulated, rel=TOLERANCE)
    assert simulated > 5.0, "a January day on a bathroom floor is kilowatt-hours"


@pytest.mark.inv("INV-63")
def test_04b_the_shadow_reads_the_loads_loss_coefficient_and_never_fits_one() -> None:
    """A wrong coefficient produces a wrong shadow, visibly - it is not corrected."""
    weather = WeatherSim(seed=7)
    _, ua = _run_slab(FIT_DAY, weather)
    right, _ = _run_slab_shadow(TEST_DAY, weather, ua)
    doubled, _ = _run_slab_shadow(TEST_DAY, weather, ua * 2.0)

    assert doubled > right * 1.5, "twice the loss is nearly twice the energy"


def test_04c_a_day_night_target_profile_is_honoured_by_the_shadow() -> None:
    """22 °C by day and 19 °C at night: the profile is the household's, so it holds."""
    weather = WeatherSim(seed=7)
    _, ua = _run_slab(FIT_DAY, weather)
    night = dict.fromkeys((*range(6), 23), 19.0)
    total, per_hour = _run_slab_shadow(TEST_DAY, weather, ua, target_at=night)

    night_kwh = sum(per_hour[hour] for hour in night)
    day_kwh = total - night_kwh
    assert night_kwh < day_kwh / len(night) * 7, "the setback draws less, hour for hour"
    flat_total, _ = _run_slab_shadow(TEST_DAY, weather, ua)
    assert total < flat_total, "a setback the household asked for is not a saving"


def _run_pump(day: date, weather: WeatherSim, area: float = 60.0) -> tuple[float, int]:
    """Run the heat-pump simulator uncontrolled for a local day."""
    sim = HeatPumpSim(area_m2=area, setpoint_c=21.0, room_c=21.0)
    start, end = day_bounds(day)
    cursor = start
    while cursor < end:
        sim.step(STEP_S, None, weather.env_at(cursor))
        cursor += timedelta(seconds=STEP_S)
    return sim.energy_in_kwh, sim.defrost_count


def _run_pump_shadow(day: date, weather: WeatherSim, area: float = 60.0) -> float:
    """Step the heat-pump shadow through a local day at its steady state."""
    shadow = shadow_for(StoreKind.HEAT_PUMP)
    assert shadow is not None
    params = LoadParams(
        kind=StoreKind.HEAT_PUMP,
        nameplate_w=1500.0,
        rated_w=1500.0,
        cop=DEFAULT_COP_CURVES["a2a"],
        # The simulator's envelope: 0.7 W/m²K over the floor area (D4 §6.4).
        loss_coeff_w_per_k=0.7 * area,
    )
    start, end = day_bounds(day)
    state = shadow.init(21.0, start, ShadowCtx(params=params, target=21.0))
    total = 0.0
    cursor = start
    while cursor < end:
        state, kwh = shadow.step(
            state,
            hour_slot(cursor),
            ShadowCtx(params=params, target=21.0, outdoor_c=weather.env_at(cursor).outdoor_c),
        )
        total += kwh
        cursor += timedelta(hours=1)
    return total


@pytest.mark.inv("INV-69")
def test_04d_the_heat_pump_shadow_is_within_ten_percent_when_the_coil_stays_clear() -> None:
    """Heat demand ÷ COP(T_out) against the Carnot mechanism (D11 §5.3)."""
    weather = WeatherSim(seed=7)
    simulated, defrosts = _run_pump(MILD_DAY, weather)

    assert defrosts == 0, "the mild day is the one without the signature"
    assert _run_pump_shadow(MILD_DAY, weather) == pytest.approx(simulated, rel=TOLERANCE)


def test_04e_defrost_is_the_known_gap_and_it_is_in_both_worlds() -> None:
    """The shadow does not model defrost, so a cold day reads low - by that much.

    D11 §5.3 says so outright, and the reason it is allowed is that a coil ices in
    both worlds: the energy is missing from the counterfactual *and* from what the
    actual would have been, so the savings figure is barely moved while the
    absolute counterfactual is. The test states the size rather than hiding it.
    """
    weather = WeatherSim(seed=7)
    simulated, defrosts = _run_pump(TEST_DAY, weather)
    shadowed = _run_pump_shadow(TEST_DAY, weather)

    assert defrosts > 20, "a January day below +3 °C ices the coil every 45 minutes"
    assert 0.6 < shadowed / simulated < 0.8, (
        "the shadow is about a quarter under the simulator on a defrosting day; "
        "modelling defrost is D11 §10's, not this WP's"
    )


# --------------------------------------------------------------------------- #
# 5 - the plug-in shadow
# --------------------------------------------------------------------------- #

PLUG_IN = local(2026, 12, 3, 17, 0)
MAX_W = 11000.0
REQUIRED_KWH = 30.0


def _ev_ctx(mode: Mode = Mode.AUTO, required_kwh: float | None = REQUIRED_KWH) -> ShadowCtx:
    return shadow_ctx(
        params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W),
        mode=mode,
        demand=demand(wants=True, required_kwh=required_kwh, max_w=MAX_W),
    )


def _evening_site(mode: Mode = Mode.AUTO):
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("ev", _ev_ctx(mode))
    return under_test


def test_05_the_ev_shadow_charges_at_the_full_rate_from_plug_in() -> None:
    """30 kWh at 11 kW from 17:00 is 17:00–19:44: two full slots and a remainder."""
    under_test = _evening_site()
    # The requirement falls as the real load charges; the shadow latched at the edge.
    for index, remaining in enumerate((30.0, 21.0, 12.0)):
        under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=remaining, max_w=MAX_W))
        under_test.close(
            closed_slot((PLUG_IN + timedelta(hours=index)).astimezone(UTC), loads={"ev": 0.0})
        )

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.cf_kwh == pytest.approx(30.0)
    # 11 × 1.25 (17) + 11 × 1.40 (18) + 8 × 1.10 (19) = 37.95 NOK, NO3 shape.
    assert rec.cf_cost.amount == Decimal("37.950")
    assert rec.cost.amount == Decimal("0.000"), "powerplan has not charged it yet"


def test_05b_the_savings_are_the_evening_the_plan_moved_to_the_night() -> None:
    """The actual charges at night; the shadow charged at the peak (D11 §5.3)."""
    under_test = _evening_site()
    # Plugged in at 17:00, charged by powerplan at 02:00–04:44 the next morning.
    night = {2: 11.0, 3: 11.0, 4: 8.0}
    for hour in range(17, 24):
        under_test.close(
            closed_slot(local(2026, 12, 3, hour, 0).astimezone(UTC), loads={"ev": 0.0})
        )
    for hour in range(6):
        under_test.close(
            closed_slot(
                local(2026, 12, 4, hour, 0).astimezone(UTC), loads={"ev": night.get(hour, 0.0)}
            )
        )

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.kwh == pytest.approx(30.0)
    assert rec.cf_kwh == pytest.approx(30.0), "the same energy, at a different hour"
    # 11 × 0.17 (02) + 11 × 0.19 (03) + 8 × 0.22 (04) = 5.72 NOK.
    assert rec.cost.amount == Decimal("5.720")
    assert rec.savings.amount == Decimal("37.950") - Decimal("5.720")
    assert rec.kwh_shifted == pytest.approx(30.0), "all of it ran at another time"


def test_05c_an_unknown_requirement_defers_the_session_and_prices_it_once() -> None:
    """No SoC sensor: the slots are held, then priced once with what was drawn."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("ev", _ev_ctx(required_kwh=None))

    # 17:00–21:00 plugged in with no requirement; powerplan charges 28 kWh at 22–00.
    drawn = {22: 11.0, 23: 11.0}
    for hour in range(17, 24):
        under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=None, max_w=MAX_W))
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC), loads={"ev": drawn.get(hour, 0.0)}
            )
        )
    under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=None, max_w=MAX_W))
    under_test.close(closed_slot(local(2026, 12, 4, 0, 0).astimezone(UTC), loads={"ev": 6.0}))

    status = under_test.accounting.status()
    assert status.loads["ev"].pending, "the session is open; the savings are unstated"
    assert status.loads["ev"].cf_kwh == 0.0
    cost_while_pending = status.loads["ev"].cost

    # The car unplugs: the session is stepped from the plug-in and priced once.
    under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
    under_test.close(closed_slot(local(2026, 12, 4, 1, 0).astimezone(UTC), loads={"ev": 0.0}))

    status = under_test.accounting.status()
    assert not status.loads["ev"].pending
    assert status.loads["ev"].cost == cost_while_pending, "no priced slot was restated (INV-69)"
    assert status.loads["ev"].cf_kwh == pytest.approx(28.0), "what the session actually took"
    # 11 × 1.25 (17) + 11 × 1.40 (18) + 6 × 1.10 (19) = 35.75 NOK.
    assert status.loads["ev"].cf_cost.amount == Decimal("35.750")
    assert not under_test.accounting.state().deferred


def test_05d_a_forced_charge_saves_nothing_and_says_so() -> None:
    """The household forced it, so the real load did what the shadow does."""
    under_test = _evening_site(Mode.FORCE)
    for index, kwh in enumerate((11.0, 11.0, 8.0)):
        under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=REQUIRED_KWH, max_w=MAX_W))
        under_test.close(
            closed_slot((PLUG_IN + timedelta(hours=index)).astimezone(UTC), loads={"ev": kwh})
        )

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.cf_kwh == pytest.approx(rec.kwh)
    assert rec.savings.amount == 0
    assert rec.excluded_slots == 0, "a force is accounted, not excluded"


# --------------------------------------------------------------------------- #
# 14 - modes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", [Mode.DELEGATED, Mode.OFF])
def test_14_delegated_and_off_count_cost_and_state_no_savings(mode: Mode) -> None:
    """Powerplan is not steering those slots, so there is nothing to have saved."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("ev", _ev_ctx(mode))
    under_test.close(closed_slot(PLUG_IN.astimezone(UTC), loads={"ev": 7.0}))

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.cost.amount == Decimal("7") * Decimal("1.25")
    assert rec.cf_kwh == rec.kwh, "the counterfactual is the actual"
    assert rec.savings.amount == 0
    assert rec.excluded_slots == 1


def test_14b_observe_slots_calibrate_and_the_day_reanchors_the_shadow() -> None:
    """In observe the real trajectory IS the counterfactual (D11 §5.3 b, §5.5).

    The anchor lands at the end of the local day, not at the end of every slot: a
    bang-bang shadow pulled onto a level its own band does not centre on re-heats
    the offset on every slot, and the error it measures comes out multiplied by the
    slots in a day (`design/DECISIONS.md` D-0178).
    """
    weather = WeatherSim(seed=7)
    _, ua = _run_slab(FIT_DAY, weather)
    params = LoadParams(
        kind=StoreKind.SLAB,
        nameplate_w=NAMEPLATE_W,
        store=SlabStore(area_m2=AREA_M2, screed_mm=50.0, loss_coeff_w_per_k=ua, max_c=27.0),
        band_k=SWING_K,
        loss_coeff_w_per_k=ua,
    )
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load(
        "floor",
        shadow_ctx(
            params=params, mode=Mode.OBSERVE, target=TARGET_C, outdoor_c=-5.0, level_now=TARGET_C
        ),
    )

    start = local(2026, 12, 3, 0, 0).astimezone(UTC)
    for hour in range(4):
        under_test.ctx_for("floor", level_now=19.0)
        under_test.close(closed_slot(start + timedelta(hours=hour), loads={"floor": 0.6}))

    rec = under_test.accounting.state().ledger.loads["floor"]
    assert rec.observe_slots == 4
    assert rec.calib_kwh == pytest.approx(2.4)
    assert under_test.accounting.state().shadows["floor"].level != 19.0, "not yet anchored"
    calib = under_test.accounting.state().calibration["floor"]
    assert calib.observe_days == 1
    assert calib.days[0].slots == 4

    # Through the day's last slot, 23:00–00:00 local, and the anchor lands.
    for hour in range(4, 24):
        under_test.ctx_for("floor", level_now=19.0)
        under_test.close(closed_slot(start + timedelta(hours=hour), loads={"floor": 0.6}))

    assert under_test.accounting.state().shadows["floor"].level == 19.0, "re-anchored"
    assert under_test.accounting.state().calibration["floor"].days[0].slots == 24


def test_14c_an_inactive_site_makes_every_slot_an_observe_slot() -> None:
    """The site switch off is "every load in observe" (PLAN §7 dec. 20)."""
    assert effective_mode(Mode.AUTO, site_active=False) is Mode.OBSERVE
    assert effective_mode(Mode.OFF, site_active=False) is Mode.OFF

    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("ev", _ev_ctx(effective_mode(Mode.AUTO, site_active=False)))
    under_test.close(closed_slot(PLUG_IN.astimezone(UTC), loads={"ev": 11.0}))

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.observe_slots == 1
    assert rec.excluded_slots == 0


def test_14d_a_load_with_no_store_model_states_no_savings() -> None:
    """`kind = none`: the cost is shown and no baseline is claimed (D11 §5.3)."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("loop", shadow_ctx())
    under_test.close(closed_slot(PLUG_IN.astimezone(UTC), loads={"loop": 2.0}))

    figures = under_test.accounting.status().loads["loop"]
    assert figures.cost.amount == Decimal("2") * Decimal("1.25")
    assert figures.savings.amount == 0
    assert figures.savings_confidence is SavingsConfidence.NONE
    assert figures.calibration_error is None
