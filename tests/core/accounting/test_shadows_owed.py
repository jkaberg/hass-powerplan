"""D11 §9 6, 8, 9 - the tank, the schedule and the idle battery.

The three shadows WP3.3, WP4.1 and WP5.4 named and never built. Until they
existed `shadow_for` answered `None` for all three, so a water heater, a pool
pump and a battery showed what they cost and never what they saved.

* The **tank** is a plain cylinder on its own thermostat: it holds its dial,
  reheats the moment the household's draw-off takes it under the band, and runs
  the legionella cycle when the real one runs - the protection is owed with or
  without powerplan, so those slots net to zero (D11 §5.3).
* The **schedule** is a relay load's daily hours spread evenly over the day, so
  the counterfactual pays the day's mean price (D11 §5.3, §11).
* The **battery** without a controller idles: it draws nothing, so its savings
  are minus its cost - revenue when it sold high what it bought low (INV-19).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.accounting import (
    ClosedSlot,
    LoadParams,
    ShadowCtx,
    SlotConfidence,
    StoreKind,
    shadow_for,
)
from custom_components.powerplan.core.accounting.shadow import kinds
from custom_components.powerplan.core.loads.stores import TankStore
from custom_components.powerplan.core.loads.stores.thermal import DrawOffProfile
from tests.core.accounting.conftest import (
    ORDINARY,
    OSLO,
    closed_slot,
    curve,
    local,
    no3,
    shadow_ctx,
    site,
)
from tests.sim.base import Env
from tests.sim.tank import ETA, TankSim

# --------------------------------------------------------------------------- #
# The reference house's tank (D9 §5.9): 300 L, 3 kW, its dial at 75 °C
# --------------------------------------------------------------------------- #

LITRES = 300.0
ELEMENT_W = 3000.0
STANDBY_W = 60.0
SETPOINT_C = 75.0
HYSTERESIS_K = 2.0
TANK = TankStore(litres=LITRES, standby_loss_w=STANDBY_W, max_c=80.0, min_c=45.0)
PERSONS = 3

#: D11 §9 6's tolerance against the stratified simulator.
TOLERANCE = 0.10

QUARTER = timedelta(minutes=15)


def _tank_params() -> LoadParams:
    return LoadParams(
        kind=StoreKind.TANK,
        nameplate_w=ELEMENT_W,
        store=TANK,
        hysteresis_k=HYSTERESIS_K,
        charge_eff=TANK.eta,
        charge_setpoint=SETPOINT_C,
        standby_loss_w=STANDBY_W,
        draw_off=DrawOffProfile(persons=PERSONS),
    )


def _slot(start: datetime, minutes: int = 15) -> ClosedSlot:
    """Return the bare `ClosedSlot` a shadow needs: a start and a length."""
    return ClosedSlot(
        start_utc=start,
        minutes=minutes,
        import_kwh=0.0,
        export_kwh=0.0,
        site_confidence=SlotConfidence.EXACT,
        loads={},
    )


def _day(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=OSLO).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=OSLO).astimezone(
        UTC
    )
    return start, end


def test_every_store_kind_but_none_has_a_shadow() -> None:
    """The registry is complete: `none` is the one kind with no counterfactual (D11 §5.3)."""
    assert set(kinds()) == set(StoreKind) - {StoreKind.NONE}
    assert shadow_for(StoreKind.NONE) is None


# --------------------------------------------------------------------------- #
# 6 - the tank
# --------------------------------------------------------------------------- #


def _step_day(
    draws: dict[datetime, float] | None = None, *, profile: DrawOffProfile | None = None
) -> tuple[list[tuple[datetime, float, float]], float]:
    """Step the tank shadow through `ORDINARY` in quarters; return `(start, kWh, level)` rows."""
    shadow = shadow_for(StoreKind.TANK)
    assert shadow is not None
    params = _tank_params()
    start, end = _day(ORDINARY)
    state = shadow.init(SETPOINT_C, start, ShadowCtx(params=params))
    rows: list[tuple[datetime, float, float]] = []
    cursor = start
    while cursor < end:
        draw = (
            profile.kwh_between(cursor, cursor + QUARTER, OSLO)
            if profile is not None
            else (draws or {}).get(cursor, 0.0)
        )
        state, kwh = shadow.step(state, _slot(cursor), ShadowCtx(params=params, draw_off_kwh=draw))
        assert state.level is not None
        rows.append((cursor, kwh, state.level))
        cursor += QUARTER
    assert state.level is not None
    return rows, state.level


def test_06_a_draw_reheats_at_once_at_nameplate() -> None:
    """A 3 kWh morning shower block: the element comes on in the draw's own quarter and stays on."""
    shower = local(2026, 12, 3, 7, 0).astimezone(UTC)
    rows, _ = _step_day({shower: 3.0})
    by_start = {at: kwh for at, kwh, _level in rows}
    full = ELEMENT_W / 1000.0 * 0.25

    assert by_start[shower] > 0.0, "the thermostat calls inside the quarter the draw happened"
    for index in (1, 2, 3):
        assert by_start[shower + index * QUARTER] == pytest.approx(full), index
    recovered = shower + 5 * QUARTER
    assert by_start[recovered] == 0.0, "back at the dial, the element is off"
    # What the reheat took is what the shower and the standby took, and no more.
    reheat = sum(kwh for at, kwh, _level in rows if shower <= at < recovered)
    standby = STANDBY_W / 1000.0 * 5 * 0.25
    assert reheat == pytest.approx(3.0 + standby, abs=HYSTERESIS_K * TANK.capacity_kwh_per_unit())


def test_06b_the_d4_draw_off_profile_is_reheated_as_it_is_drawn() -> None:
    """A day of the household's own draw-off (D4 §5.7): no waiting for a cheap hour, no lag."""
    profile = DrawOffProfile(persons=PERSONS)
    rows, level_end = _step_day(profile=profile)
    full = ELEMENT_W / 1000.0 * 0.25
    capacity = TANK.capacity_kwh_per_unit()

    assert all(kwh <= full + 1e-9 for _at, kwh, _level in rows), "never above nameplate"
    # A thermostat that reheats at once never lets the tank fall far under its band.
    assert min(level for _at, _kwh, level in rows) >= SETPOINT_C - HYSTERESIS_K - 0.1
    # The morning's draw is reheated in the morning, not at the night's cheap hours.
    morning = sum(
        kwh
        for at, kwh, _level in rows
        if local(2026, 12, 3, 6, 0) <= at.astimezone(OSLO) < local(2026, 12, 3, 9, 30)
    )
    morning_draw = profile.kwh_between(
        local(2026, 12, 3, 6, 0).astimezone(UTC), local(2026, 12, 3, 9, 0).astimezone(UTC), OSLO
    )
    assert morning >= morning_draw - HYSTERESIS_K * capacity
    # The day's energy is the draw-off plus the standby through the element's η,
    # less what is left in the tank against its dial.
    drawn = sum(kwh for _at, kwh, _level in rows)
    taken = profile.kwh_per_day + STANDBY_W / 1000.0 * 24.0
    expected = taken / TANK.eta + (level_end - SETPOINT_C) * capacity
    assert drawn == pytest.approx(expected, abs=1e-6)


def test_06c_a_legionella_cycle_runs_in_both_worlds_and_nets_zero() -> None:
    """The real cycle at 03:00 is the shadow's cycle too: those slots save nothing (D11 §5.3)."""
    under_test = site(import_curve=curve(ORDINARY, days=1))
    under_test.with_load("tank", shadow_ctx(params=_tank_params(), level_now=SETPOINT_C))
    for hour in range(3):
        under_test.close(
            closed_slot(local(2026, 12, 3, hour, 0).astimezone(UTC), loads={"tank": 0.0})
        )
    rec = under_test.accounting.state().ledger.loads["tank"]
    before = (rec.kwh, rec.cf_kwh, rec.cost.amount, rec.cf_cost.amount)

    under_test.ctx_for("tank", legionella_active=True)
    for hour, kwh in ((3, 3.0), (4, 3.0), (5, 1.2)):
        under_test.close(
            closed_slot(local(2026, 12, 3, hour, 0).astimezone(UTC), loads={"tank": kwh})
        )

    rec = under_test.accounting.state().ledger.loads["tank"]
    assert rec.kwh - before[0] == pytest.approx(7.2)
    assert rec.cf_kwh - before[1] == pytest.approx(7.2), "the shadow ran the same cycle"
    assert rec.cf_cost.amount - before[3] == rec.cost.amount - before[2]
    expected = Decimal("3") * no3(3) + Decimal("3") * no3(4) + Decimal("1.2") * no3(5)
    assert rec.cost.amount - before[2] == expected

    # The shadow leaves the cycle where a plain tank would be - on its dial (D-0382).
    under_test.ctx_for("tank", legionella_active=False)
    under_test.close(closed_slot(local(2026, 12, 3, 6, 0).astimezone(UTC), loads={"tank": 0.0}))
    assert under_test.accounting.state().ledger.loads["tank"].cf_kwh == pytest.approx(rec.cf_kwh)


@pytest.mark.inv("INV-69")
def test_06d_standby_over_an_idle_day_is_within_ten_percent_of_the_tank_simulator() -> None:
    """The lumped 60 W against the stratified tank's `UA·(T − ambient)`, over a day with no draw.

    Each side is corrected for its own change in stored heat, so where each
    thermostat's cycle happens to be at midnight does not decide the answer.
    """
    start, end = _day(ORDINARY)
    sim = TankSim(
        litres=LITRES,
        element_w=ELEMENT_W,
        setpoint_c=SETPOINT_C,
        hysteresis_k=HYSTERESIS_K,
        draw=None,
        top_c=SETPOINT_C,
        bottom_c=SETPOINT_C,
    )
    stored_before = sim.stored_kwh
    cursor = start
    while cursor < end:
        sim.step(60.0, None, Env(now=cursor, outdoor_c=0.0))
        cursor += timedelta(seconds=60)
    simulated = sim.energy_in_kwh - (sim.stored_kwh - stored_before) / ETA

    rows, level_end = _step_day()
    shadowed = (
        sum(kwh for _at, kwh, _level in rows)
        - (level_end - SETPOINT_C) * TANK.capacity_kwh_per_unit()
    )

    assert simulated > 1.0, "a day of standby is more than a kilowatt-hour"
    assert shadowed == pytest.approx(simulated, rel=TOLERANCE)


def test_06e_vacation_does_not_lower_the_shadows_dial() -> None:
    """A plain tank does not know the household is away: the shadow holds its dial (D11 §5.3)."""
    shadow = shadow_for(StoreKind.TANK)
    assert shadow is not None
    params = _tank_params()
    start = local(2026, 12, 3, 12, 0).astimezone(UTC)
    state = shadow.init(SETPOINT_C - 5.0, start, ShadowCtx(params=params))
    # The household's own target (the comfort floor on vacation) is not the dial.
    _, kwh = shadow.step(state, _slot(start), ShadowCtx(params=params, target=45.0))
    assert kwh == pytest.approx(ELEMENT_W / 1000.0 * 0.25)


# --------------------------------------------------------------------------- #
# 8 - the schedule
# --------------------------------------------------------------------------- #

POOL_W = 1000.0
HOURS_PER_DAY = 6.0


def _pool_params() -> LoadParams:
    return LoadParams(kind=StoreKind.SCHEDULE, nameplate_w=POOL_W, hours_per_day=HOURS_PER_DAY)


def test_08_the_schedule_shadow_spreads_its_hours_evenly_over_the_day() -> None:
    """A 6 h/day pool pump: the same kWh in every quarter, 6 kWh over the day."""
    shadow = shadow_for(StoreKind.SCHEDULE)
    assert shadow is not None
    params = _pool_params()
    start, end = _day(ORDINARY)
    state = shadow.init(None, start, ShadowCtx(params=params))
    per_slot: list[float] = []
    cursor = start
    while cursor < end:
        state, kwh = shadow.step(state, _slot(cursor), ShadowCtx(params=params))
        per_slot.append(kwh)
        cursor += QUARTER
    assert len(per_slot) == 96
    assert all(
        kwh == pytest.approx(POOL_W / 1000.0 * HOURS_PER_DAY / 24.0 * 0.25) for kwh in per_slot
    )
    assert sum(per_slot) == pytest.approx(POOL_W / 1000.0 * HOURS_PER_DAY)


@pytest.mark.inv("INV-69")
def test_08b_the_counterfactual_pays_the_days_mean_price() -> None:
    """Powerplan ran it in the six cheapest hours; without it the pump pays the mean."""
    under_test = site(import_curve=curve(ORDINARY, days=1))
    under_test.with_load("pool", shadow_ctx(params=_pool_params()))
    prices = {hour: no3(hour) for hour in range(24)}
    cheapest = sorted(prices, key=lambda hour: prices[hour])[: int(HOURS_PER_DAY)]
    for hour in range(24):
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC),
                loads={"pool": POOL_W / 1000.0 if hour in cheapest else 0.0},
            )
        )

    rec = under_test.accounting.state().ledger.loads["pool"]
    mean = sum(prices.values(), Decimal(0)) / 24
    assert rec.cf_kwh == pytest.approx(HOURS_PER_DAY * POOL_W / 1000.0)
    assert rec.cf_cost.amount == mean * Decimal(str(HOURS_PER_DAY))
    assert rec.cost.amount == sum((prices[hour] for hour in cheapest), Decimal(0))
    assert rec.savings.amount == rec.cf_cost.amount - rec.cost.amount
    assert rec.savings.amount > 0


# --------------------------------------------------------------------------- #
# 9 - the battery
# --------------------------------------------------------------------------- #


def _battery_site(trace: dict[int, float]):
    under_test = site(import_curve=curve(ORDINARY, days=1))
    under_test.with_load(
        "battery",
        shadow_ctx(params=LoadParams(kind=StoreKind.BATTERY, nameplate_w=5000.0), level_now=0.5),
    )
    for hour in range(24):
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC),
                loads={"battery": trace.get(hour, 0.0)},
                uncontrolled_kwh=10.0,
            )
        )
    return under_test


@pytest.mark.inv("INV-19")
def test_09_an_idle_battery_makes_the_savings_minus_the_cost() -> None:
    """Bought at 02–03, sold at 17–18: the revenue is the savings (D11 §5.3, signed by INV-19)."""
    under_test = _battery_site({2: 5.0, 3: 5.0, 17: -4.5, 18: -4.5})

    rec = under_test.accounting.state().ledger.loads["battery"]
    assert rec.cf_kwh == 0.0, "a battery without a controller neither charges nor discharges"
    assert rec.cf_cost.amount == 0
    # 5 × 0.17 + 5 × 0.19 − 4.5 × 1.25 − 4.5 × 1.40 = −10.125 NOK: it earned money.
    assert rec.cost.amount == Decimal("-10.125")
    assert rec.savings.amount == -rec.cost.amount
    assert rec.savings.amount > 0
    figures = under_test.accounting.status().loads["battery"]
    assert figures.savings == rec.savings


def test_09b_arbitrage_the_wrong_way_round_is_shown_negative() -> None:
    """Bought at the peak, sold at night: the loss is stated, never clamped (INV-69)."""
    under_test = _battery_site({17: 4.5, 18: 4.5, 2: -4.0, 3: -4.0})

    rec = under_test.accounting.state().ledger.loads["battery"]
    assert rec.savings.amount == -rec.cost.amount
    assert rec.savings.amount < 0
