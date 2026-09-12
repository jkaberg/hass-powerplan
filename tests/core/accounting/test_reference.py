"""D11 §9 24–31 - the reference: savings measure timing, from measured energy (§5.9).

The headline counterfactual is each load's own measured kilowatt-hours, placed
where the uncontrolled device would have drawn them, and booked when the day,
session or run they belong to settles. The prices are the reference house's under
Norgespris and Tensio's energy charge (0.8779 NOK/kWh 06–22, 0.7379 at night, incl.
VAT), because that is where the signal is smallest.
"""

from __future__ import annotations

import random
from datetime import UTC, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.accounting import (
    LoadParams,
    PricedSlot,
    SlotPrice,
    StoreKind,
)
from custom_components.powerplan.core.accounting.ledger import plus
from custom_components.powerplan.core.accounting.reference import (
    OpenBuffer,
    ReferenceKind,
    settle,
)
from custom_components.powerplan.core.loads.base import CycleProfile
from custom_components.powerplan.core.loads.stores import SlabStore
from custom_components.powerplan.core.model import Confidence, Mode
from tests.core.accounting.conftest import (
    NOK,
    ORDINARY,
    closed_slot,
    curve,
    demand,
    local,
    no3,
    shadow_ctx,
    site,
    tensio,
    window,
)

#: Norgespris 0.40 + 25 % VAT, plus Tensio's energy charge incl. VAT (the house).
DAY_PRICE = Decimal("0.8779")
NIGHT_PRICE = Decimal("0.7379")
MAX_W = 11000.0


def norgespris(hour: int) -> Decimal:
    """Return the house's price for a local hour: day 06–22, night otherwise."""
    return DAY_PRICE if 6 <= hour < 22 else NIGHT_PRICE


def _floor(**kwargs: object) -> object:
    """Return a 20 m² bathroom floor whose loss coefficient failed its fit - the house's case."""
    params = LoadParams(
        kind=StoreKind.SLAB,
        nameplate_w=1600.0,
        store=SlabStore(area_m2=20.0, screed_mm=50.0, loss_coeff_w_per_k=None, max_c=27.0),
        loss_coeff_w_per_k=None,
    )
    kwargs.setdefault("mode", Mode.AUTO)
    return shadow_ctx(params=params, target=22.0, level_now=22.0, outdoor_c=5.0, **kwargs)


def _ev() -> object:
    return shadow_ctx(
        params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W),
        demand=demand(wants=False, max_w=MAX_W),
    )


# --------------------------------------------------------------------------- #
# 24 - `day`
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-69")
def test_24_a_floor_heated_at_night_is_booked_at_the_days_end_against_its_even_spread() -> None:
    """6 kWh at night: nothing booked before 23:00 closes, then 6 × (p̄ − 0.7379)."""
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=norgespris))
    under_test.with_load("floor", _floor())
    for hour in range(23):
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC),
                loads={"floor": 1.0 if hour < 6 else 0.0},
            )
        )
        figures = under_test.accounting.status().loads["floor"]
        assert figures.savings.amount == 0, "an open day carries neither side"
        assert figures.cf_kwh == 0.0

    assert under_test.accounting.status().loads["floor"].pending
    under_test.close(closed_slot(local(2026, 12, 3, 23, 0).astimezone(UTC), loads={"floor": 0.0}))

    figures = under_test.accounting.status().loads["floor"]
    assert not figures.pending
    assert figures.cf_kwh == pytest.approx(6.0), "the same energy"
    # cf: 0.25 kWh in each of 24 hours = 0.25 × (16 × 0.8779 + 8 × 0.7379) = 4.9874;
    # actual: 6 × 0.7379 = 4.4274.
    assert figures.cf_cost.amount == Decimal("4.9874")
    assert figures.cost.amount == Decimal("4.4274")
    assert figures.savings.amount == Decimal("0.5600")
    assert figures.kwh_shifted == pytest.approx((6 * 0.75 + 18 * 0.25) / 2)


def test_24b_a_day_whose_last_slot_never_closed_settles_on_the_next_days_first() -> None:
    """HA down over midnight: the day is settled the moment a later day's slot arrives."""
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=norgespris))
    under_test.with_load("floor", _floor())
    for hour in range(6):
        under_test.close(
            closed_slot(local(2026, 12, 3, hour, 0).astimezone(UTC), loads={"floor": 1.0})
        )
    under_test.close(closed_slot(local(2026, 12, 4, 7, 0).astimezone(UTC), loads={"floor": 0.0}))

    figures = under_test.accounting.status().loads["floor"]
    # Six night hours spread evenly over the six hours that were measured: no timing.
    assert figures.cf_kwh == pytest.approx(6.0)
    assert figures.savings.amount == 0
    assert under_test.accounting.state().open["floor"].key == "2026-12-04"


def test_24c_a_legionella_slot_is_its_own_counterfactual() -> None:
    """The protection is due with or without powerplan, so it neither saves nor costs."""
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=norgespris))
    tank = shadow_ctx(
        params=LoadParams(kind=StoreKind.TANK, nameplate_w=3000.0), legionella_active=True
    )
    under_test.with_load("tank", tank)
    under_test.close(closed_slot(local(2026, 12, 3, 3, 0).astimezone(UTC), loads={"tank": 3.0}))

    figures = under_test.accounting.status().loads["tank"]
    assert figures.cf_kwh == pytest.approx(3.0)
    assert figures.savings.amount == 0
    assert not figures.pending


# --------------------------------------------------------------------------- #
# 25 - conservation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reference", [ReferenceKind.DAY, ReferenceKind.SESSION, ReferenceKind.RUN])
def test_25_every_settled_buffer_conserves_its_energy(reference: ReferenceKind) -> None:
    """`Σ cf_kwh == Σ kwh` for any slots, any rate - savings can only be timing."""
    rng = random.Random(25)
    start = local(2026, 12, 3, 0, 0).astimezone(UTC)
    for _ in range(200):
        count = rng.randint(1, 30)
        slots = tuple(
            PricedSlot(
                start_utc=start + timedelta(minutes=15 * index),
                minutes=15,
                kwh=rng.choice((0.0, rng.uniform(0.0, 4.0))),
                cf_kwh=0.0,
                price=SlotPrice(Decimal("0.8"), NOK, Confidence.KNOWN),
                shape_kwh=rng.choice((0.0, rng.uniform(0.0, 1.0))),
            )
            for index in range(count)
        )
        # A rate below what the real load drew leaves a remainder the fill must keep.
        settled = settle(OpenBuffer(reference, "k", slots), rng.choice((None, 2000.0, MAX_W)))
        assert sum(slot.cf_kwh for slot in settled) == pytest.approx(
            sum(slot.kwh for slot in slots), abs=1e-9
        )
        assert all(slot.settled for slot in settled)


# --------------------------------------------------------------------------- #
# 26 - `session`
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-69")
def test_26_an_ev_with_no_soc_is_settled_when_it_stops_wanting_and_nothing_is_deferred() -> None:
    """A reference-house evening: 3.44 kWh at the day rate 19–21, 11.9 kWh at night.

    The shadow held those 3.44 kWh unpriced and never settled them, so the bar read
    −3.11 NOK. The reference needs no state of charge: the session's own energy
    at the charger's full rate from the plug-in.
    """
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=norgespris))
    under_test.with_load("ev", _ev())
    drawn = {(3, 19): 1.72, (3, 20): 1.72, (3, 22): 4.0, (3, 23): 4.0, (4, 0): 3.9}
    for day, hour in ((3, 19), (3, 20), (3, 21), (3, 22), (3, 23), (4, 0)):
        under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=None, max_w=MAX_W))
        under_test.close(
            closed_slot(
                local(2026, 12, day, hour, 0).astimezone(UTC),
                loads={"ev": drawn.get((day, hour), 0.0)},
            )
        )
        assert under_test.accounting.status().loads["ev"].savings.amount == 0, "open, not red"
    under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
    under_test.close(closed_slot(local(2026, 12, 4, 1, 0).astimezone(UTC), loads={"ev": 0.0}))

    figures = under_test.accounting.status().loads["ev"]
    assert not figures.pending
    assert not under_test.accounting.state().open
    assert figures.kwh == pytest.approx(15.34)
    assert figures.cf_kwh == pytest.approx(15.34), "11 kWh at 19:00 and 4.34 at 20:00"
    # The night's 11.9 kWh would have run at the day rate: 11.9 × 0.14 = 1.666 NOK.
    assert figures.savings.amount == Decimal("11.9") * (DAY_PRICE - NIGHT_PRICE)


def test_26b_an_off_stretch_inside_a_session_is_excluded_and_the_session_goes_on() -> None:
    """The household set the charger to off 21:11–22:08: those slots state no savings."""
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=norgespris))
    under_test.with_load("ev", _ev())
    plan = ((19, Mode.AUTO, 0.0), (20, Mode.AUTO, 0.0), (21, Mode.OFF, 2.0), (22, Mode.AUTO, 5.0))
    for hour, mode, kwh in plan:
        under_test.ctx_for(
            "ev", mode=mode, demand=demand(wants=True, required_kwh=None, max_w=MAX_W)
        )
        under_test.close(
            closed_slot(local(2026, 12, 3, hour, 0).astimezone(UTC), loads={"ev": kwh})
        )
    under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
    under_test.close(closed_slot(local(2026, 12, 3, 23, 0).astimezone(UTC), loads={"ev": 0.0}))

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.excluded_slots == 1
    assert rec.cf_kwh == pytest.approx(7.0), "2 kWh as they ran, 5 kWh placed at 19:00"
    # The 5 kWh at 22:00 (night) would have run at 19:00 (day): 5 × 0.14.
    assert rec.savings.amount == Decimal(5) * (DAY_PRICE - NIGHT_PRICE)


# --------------------------------------------------------------------------- #
# 27 - `run`
# --------------------------------------------------------------------------- #

UNIFORM_10 = tuple(1.0 / 10 for _ in range(10))


def _dishwasher(profile: CycleProfile | None) -> object:
    return shadow_ctx(
        params=LoadParams(kind=StoreKind.CYCLE, nameplate_w=2000.0, cycle_profile=profile),
        demand=demand(wants=True, required_kwh=0.9),
    )


def _run_dishwasher(profile: CycleProfile | None):
    under_test = site(import_curve=curve(ORDINARY, days=2))
    at = local(2026, 12, 3, 19, 0).astimezone(UTC)
    under_test.with_load("dishwasher", _dishwasher(profile), at=at)
    for day, hour, kwh in (
        (3, 19, 0.0),
        (3, 20, 0.0),
        (3, 21, 0.0),
        (4, 2, 0.4),
        (4, 3, 0.4),
        (4, 4, 0.4),
    ):
        under_test.close(
            closed_slot(local(2026, 12, day, hour, 0).astimezone(UTC), loads={"dishwasher": kwh})
        )
    under_test.ctx_for("dishwasher", demand=demand(wants=False))
    under_test.close(
        closed_slot(local(2026, 12, 4, 5, 0).astimezone(UTC), loads={"dishwasher": 0.0})
    )
    return under_test.accounting.state().ledger.loads["dishwasher"]


def test_27_a_run_takes_the_programmes_shape_scaled_to_its_own_energy() -> None:
    """The profile says 0.9 kWh, the machine drew 1.2: the counterfactual is 1.2 kWh."""
    rec = _run_dishwasher(CycleProfile(duration_s=3 * 3600.0, energy_kwh=0.9, shape=UNIFORM_10))
    assert rec.cf_kwh == pytest.approx(1.2)
    # 0.4 kWh in each of 19, 20, 21 local against 0.4 in each of 02, 03, 04.
    assert rec.cf_cost.amount == Decimal("0.4") * (no3(19) + no3(20) + no3(21))
    assert rec.savings.amount == Decimal("0.4") * (
        no3(19) + no3(20) + no3(21) - no3(2) - no3(3) - no3(4)
    )


def test_27b_a_run_with_no_shape_is_its_own_counterfactual() -> None:
    """No profile, nothing to scale: the run states no savings rather than guess."""
    rec = _run_dishwasher(None)
    assert rec.cf_kwh == pytest.approx(1.2)
    assert rec.savings.amount == 0


# --------------------------------------------------------------------------- #
# 28 - observe
# --------------------------------------------------------------------------- #


def test_28_observe_saves_exactly_nothing_and_still_calibrates_the_model() -> None:
    """Powerplan did nothing, so it saved nothing; the shadow learns from the day."""
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=norgespris))
    under_test.with_load("floor", _floor(mode=Mode.OBSERVE))
    for hour in range(24):
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC),
                loads={"floor": 1.0 if hour < 6 else 0.0},
            )
        )

    figures = under_test.accounting.status().loads["floor"]
    assert figures.savings.amount == 0
    assert figures.cf_kwh == pytest.approx(6.0)
    assert not figures.pending
    rec = under_test.accounting.state().ledger.loads["floor"]
    assert rec.observe_slots == 24
    assert rec.calib_kwh == pytest.approx(6.0)


# --------------------------------------------------------------------------- #
# 29 - capacity through the settled day
# --------------------------------------------------------------------------- #


def test_29_an_open_session_never_shows_a_capacity_loss_and_settles_to_one_step() -> None:
    """The EV plugged in at 18:00 and charged at 02:00: 8.5 kW moved to a 6 kW night.

    While the session is open the actual book has a new 6 kW peak and the
    counterfactual has nothing yet: billed through the last settled day, the
    capacity savings stay 0 rather than going red. Settled, Dec 3 bills 4.5 kW
    against 8.5 kW - 244 against 416 NOK (the tariff conftest's 2026 table).
    """
    under_test = site(import_curve=curve(ORDINARY, days=3), tariff=tensio())
    under_test.with_load("ev", _ev())

    evening = local(2026, 12, 3, 18, 0).astimezone(UTC)
    under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=None, max_w=MAX_W))
    under_test.tariff.record_window(window(evening, 4.5))
    under_test.close(
        closed_slot(
            evening, loads={"ev": 0.0}, uncontrolled_kwh=4.5, window_closed=window(evening, 4.5)
        )
    )
    night = local(2026, 12, 4, 2, 0).astimezone(UTC)
    under_test.tariff.record_window(window(night, 6.0))
    under_test.close(
        closed_slot(
            night, loads={"ev": 4.0}, uncontrolled_kwh=2.0, window_closed=window(night, 6.0)
        )
    )
    status = under_test.accounting.status()
    assert status.site.capacity_fee.amount == Decimal(416), "the live fee has the new peak"
    assert status.site.capacity_savings.amount == 0, "and nothing unsettled is set against it"
    assert not under_test.tariff.history.counterfactual_days

    under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
    under_test.close(closed_slot(local(2026, 12, 4, 7, 0).astimezone(UTC), loads={"ev": 0.0}))

    status = under_test.accounting.status()
    assert under_test.accounting.state().settled_through == night + timedelta(hours=1)
    # Through Dec 3: actual 4.5 kW (2–5 kW, 244), counterfactual 4.5 + 4 = 8.5 kW (5–10, 416).
    assert status.site.capacity_savings.amount == Decimal(416) - Decimal(244)


# --------------------------------------------------------------------------- #
# 30 - the site identity while buffers are open
# --------------------------------------------------------------------------- #


def test_30_no_load_reads_minus_its_cost_while_its_day_is_open() -> None:
    """Five loads whose shadows drew nothing read exactly −cost.

    Their shadows still draw nothing (no loss coefficient), and the headline no
    longer cares: open, they read 0 and `pending`; settled, a timing figure.
    """
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=norgespris))
    for name in ("bad_1", "inngang", "kjokken"):
        under_test.with_load(name, _floor())
    for hour in range(12):
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC),
                loads={"bad_1": 0.2, "inngang": 0.3, "kjokken": 0.1},
                uncontrolled_kwh=1.0,
            )
        )
    status = under_test.accounting.status()
    for name in ("bad_1", "inngang", "kjokken"):
        figures = status.loads[name]
        assert figures.cost.amount > 0
        assert figures.savings.amount == 0, "never −cost"
        assert figures.pending
        rec = under_test.accounting.state().ledger.loads[name]
        assert rec.model_cf_kwh == 0.0, "the shadow still draws nothing; it is not the headline"
    total = status.site.capacity_savings
    for figures in status.loads.values():
        total = plus(total, figures.savings)
    assert status.site.savings == total


def test_30b_a_load_whose_slots_stop_arriving_does_not_hold_the_windows_for_ever() -> None:
    """A load gone while HA was down: its day settles on the next day's first slot."""
    under_test = site(import_curve=curve(ORDINARY, days=2), tariff=tensio())
    under_test.with_load("floor", _floor())
    under_test.with_load("other", _floor())
    night = local(2026, 12, 3, 2, 0).astimezone(UTC)
    under_test.tariff.record_window(window(night, 3.0))
    under_test.close(
        closed_slot(
            night,
            loads={"floor": 1.0, "other": 0.0},
            uncontrolled_kwh=2.0,
            window_closed=window(night, 3.0),
        )
    )
    assert under_test.accounting.state().pending_windows, "the floor's day is open"

    # From the next day on only "other" is metered; "floor" never comes back.
    later = local(2026, 12, 4, 1, 0).astimezone(UTC)
    under_test.close(closed_slot(later, loads={"other": 0.0}))

    state = under_test.accounting.state()
    assert "floor" not in state.open
    assert not state.pending_windows, "the night's window reached the counterfactual book"
    assert under_test.accounting.status().loads["floor"].cf_kwh == pytest.approx(1.0)


def test_26c_a_session_that_ends_while_off_still_settles() -> None:
    """Switched off mid-session and unplugged while off: the session closes on its edge."""
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=norgespris))
    under_test.with_load("ev", _ev())
    under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=None, max_w=MAX_W))
    under_test.close(closed_slot(local(2026, 12, 3, 19, 0).astimezone(UTC), loads={"ev": 3.0}))
    under_test.ctx_for("ev", mode=Mode.OFF, demand=demand(wants=False, max_w=MAX_W))
    under_test.close(closed_slot(local(2026, 12, 3, 20, 0).astimezone(UTC), loads={"ev": 0.0}))

    assert not under_test.accounting.state().open
    assert under_test.accounting.status().loads["ev"].cf_kwh == pytest.approx(3.0)
