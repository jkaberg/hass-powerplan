"""D11 §9 4 and 5, the two edges the scenario runner found (D-0269).

A shadow that D7 opened at setup - before the first tick had read a level - must
take the first level it sees rather than draw nothing for ever; and the plug-in
shadow must latch what the car asked for **at the edge**, which at the slot's
close is what it still asks for plus what the slot already delivered.
"""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest

from custom_components.powerplan.core.accounting.shadow.base import LoadParams, StoreKind
from custom_components.powerplan.core.loads.stores import SlabStore
from tests.core.accounting.conftest import (
    ORDINARY,
    closed_slot,
    curve,
    demand,
    local,
    shadow_ctx,
    site,
    tensio,
    window,
)

AREA_M2 = 5.0
CABLE_W = AREA_M2 * 80.0
UA_W_PER_K = 0.7 * AREA_M2
MAX_W = 11_000.0


def _slab() -> LoadParams:
    return LoadParams(
        kind=StoreKind.SLAB,
        nameplate_w=CABLE_W,
        store=SlabStore(area_m2=AREA_M2, screed_mm=50.0, loss_coeff_w_per_k=UA_W_PER_K, max_c=27.0),
        band_k=1.0,
        loss_coeff_w_per_k=UA_W_PER_K,
    )


@pytest.mark.inv("INV-69")
def test_04c_a_shadow_opened_before_the_first_reading_takes_the_first_level_it_sees() -> None:
    """Added with neither level nor target, as D7 does at setup: the first slot anchors it."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("floor", shadow_ctx(params=_slab()))
    assert under_test.accounting.state().shadows["floor"].level is None

    # A cold floor under its target: the thermostat it shadows would be on.
    under_test.ctx_for("floor", target=22.0, level_now=21.0, outdoor_c=-5.0)
    under_test.close(closed_slot(local(2026, 12, 3, 17, 0).astimezone(UTC), loads={"floor": 0.0}))

    shadow = under_test.accounting.state().shadows["floor"]
    assert shadow.level is not None
    rec = under_test.accounting.state().ledger.loads["floor"]
    assert rec.model_cf_kwh > 0.0, "a shadow without a level would have drawn nothing for ever"


def test_04d_without_a_level_the_first_target_stands_in_as_init_would_have() -> None:
    """No sensor yet but a target: the shadow starts at the target, as `init` does (D11 §5.3)."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("floor", shadow_ctx(params=_slab()))
    under_test.ctx_for("floor", target=22.0, level_now=None, outdoor_c=-5.0)
    under_test.close(closed_slot(local(2026, 12, 3, 17, 0).astimezone(UTC), loads={"floor": 0.0}))
    shadow = under_test.accounting.state().shadows["floor"]
    assert shadow.level is not None
    assert 21.5 - 0.1 <= shadow.level <= 22.5 + 0.1, "started at the target, held in its band"
    rec = under_test.accounting.state().ledger.loads["floor"]
    assert rec.model_cf_kwh > 0.0, "at −5 °C outdoors the thermostat it shadows would have run"


@pytest.mark.inv("INV-69")
def test_05e_the_plug_in_requirement_is_what_the_car_asked_for_at_the_edge() -> None:
    """Plugged in mid-slot and charged 3 kWh at once: the shadow still owes the whole 30 kWh."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load(
        "ev", shadow_ctx(params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W))
    )
    # At each slot's close the demand says what is still owed; the slot says what it got.
    for hour, (drawn, remaining) in enumerate(
        ((3.0, 27.0), (11.0, 16.0), (11.0, 5.0), (5.0, 0.0)), start=17
    ):
        under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=remaining, max_w=MAX_W))
        under_test.close(
            closed_slot(local(2026, 12, 3, hour, 0).astimezone(UTC), loads={"ev": drawn})
        )
    # Unplugged at 21:00: the session settles, and the reference places the same 30 kWh.
    under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
    under_test.close(closed_slot(local(2026, 12, 3, 21, 0).astimezone(UTC), loads={"ev": 0.0}))

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.kwh == pytest.approx(30.0)
    assert rec.model_cf_kwh == pytest.approx(30.0), (
        "27 owed at the close plus the 3 the slot delivered"
    )
    # 11 + 11 + 8 + 0: the shadow charges at the full rate from the plug-in slot.
    assert rec.kwh_shifted == pytest.approx((8.0 + 0.0 + 3.0 + 5.0) / 2.0)


@pytest.mark.inv("INV-69")
def test_05f_the_session_tail_is_charged_in_the_slot_the_car_finished_in() -> None:
    """The car reached its limit mid-slot: the shadow's last kilowatt-hours are that slot's, not dropped."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load(
        "ev", shadow_ctx(params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W))
    )
    # 25 kWh from 17:00 at 11 kW: 11, 11 and a 3 kWh tail. The car is full at 19:17,
    # so at the 19:00 slot's close the demand no longer wants.
    for hour, (drawn, owed, wants) in enumerate(
        ((11.0, 14.0, True), (11.0, 3.0, True), (3.0, 0.0, False)), start=17
    ):
        under_test.ctx_for("ev", demand=demand(wants=wants, required_kwh=owed, max_w=MAX_W))
        under_test.close(
            closed_slot(local(2026, 12, 3, hour, 0).astimezone(UTC), loads={"ev": drawn})
        )

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.kwh == pytest.approx(25.0)
    assert rec.cf_kwh == pytest.approx(25.0), "the tail belongs to the slot the session ended in"
    assert rec.savings.amount == 0, "charged at plug-in in both worlds"
    assert under_test.accounting.state().shadows["ev"].pending_kwh == 0.0


@pytest.mark.inv("INV-69")
def test_10b_a_window_handed_over_a_slot_late_takes_exactly_its_own_slots() -> None:
    """D7 may carry a window on the slot after its end; its counterfactual is still its four slots' (D-0267).

    Since D11 v0.3 a window waits until its slots have settled (§5.9.4): the
    session below ends at 18:15, and only then is the 17:00 window recorded.
    """
    under_test = site(tariff=tensio(), import_curve=curve(ORDINARY, days=2))
    under_test.with_load(
        "ev", shadow_ctx(params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W))
    )
    under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=30.0, max_w=MAX_W))
    first = local(2026, 12, 3, 17, 0).astimezone(UTC)
    # Plugged in at 17:00 and idle: the reference charges 11 kW from 17:00, +2.75 a slot.
    for quarter in range(4):
        under_test.close(
            closed_slot(first + timedelta(minutes=15 * quarter), minutes=15, loads={"ev": 0.0})
        )
    # The 17:00 window arrives with the 18:00 slot, whose own +2.75 is not the window's.
    under_test.close(
        closed_slot(
            first + timedelta(hours=1),
            minutes=15,
            loads={"ev": 0.0},
            window_closed=window(first, 3.0, window_min=60),
        )
    )
    day = first.astimezone(under_test.tz).date()
    assert day not in under_test.tariff.history.counterfactual_days, "the session is open"

    # The car takes 30 kWh in the 18:15 slot and is done: the session settles.
    under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
    under_test.close(
        closed_slot(first + timedelta(hours=1, minutes=15), minutes=15, loads={"ev": 30.0})
    )
    recorded = under_test.tariff.history.counterfactual_days[day]
    assert recorded.max_raw_kw == pytest.approx(3.0 + 4 * 2.75)
    remaining = under_test.accounting.state().slot_deltas
    assert list(remaining) == [
        (first + timedelta(hours=1)).isoformat(),
        (first + timedelta(hours=1, minutes=15)).isoformat(),
    ]


@pytest.mark.inv("INV-69")
def test_05g_a_link_that_drops_and_comes_back_is_not_a_new_plug_in() -> None:
    """The EV type wants nothing while offline (D4 §6.2); the shadow must not charge the session again."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load(
        "ev", shadow_ctx(params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W))
    )
    first = local(2026, 12, 3, 17, 0).astimezone(UTC)
    # 17:00 plug-in wanting 30 kWh; powerplan charges 11 kWh at 22 and 23; the BLE
    # link drops during the 00:00 slot and is back at 01:00 with 8 kWh still owed.
    script = [
        (True, 30.0, 0.0),
        (True, 30.0, 0.0),
        (True, 30.0, 0.0),
        (True, 30.0, 0.0),
        (True, 30.0, 0.0),
        (True, 19.0, 11.0),
        (True, 8.0, 11.0),
        (False, None, 0.0),
        (True, 8.0, 0.0),
        (True, 8.0, 0.0),
    ]
    for index, (wants, owed, drawn) in enumerate(script):
        under_test.ctx_for("ev", demand=demand(wants=wants, required_kwh=owed, max_w=MAX_W))
        under_test.close(closed_slot(first + timedelta(hours=index), loads={"ev": drawn}))

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.model_cf_kwh == pytest.approx(30.0), "one session, charged once at plug-in"
    assert rec.kwh == pytest.approx(22.0)


@pytest.mark.inv("INV-69")
def test_05h_a_car_back_from_a_drive_re_latches_the_drive_not_the_backlog() -> None:
    """Never topped up by powerplan over a weekend, the real car asks for more than the shadow's car spent."""
    under_test = site(import_curve=curve(ORDINARY, days=3))
    under_test.with_load(
        "ev", shadow_ctx(params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W))
    )
    first = local(2026, 12, 3, 17, 0).astimezone(UTC)
    # Plug-in wanting 30; powerplan delivers only 8 before the car leaves (unplugged,
    # required 0); it drives 6 kWh worth and returns asking 28 (= 30 − 8 + 6).
    script = [
        (True, 30.0, 0.0),
        (True, 30.0, 0.0),
        (True, 30.0, 0.0),
        (True, 22.0, 8.0),
        (False, 0.0, 0.0),
        (False, 0.0, 0.0),
        (True, 28.0, 0.0),
        (True, 28.0, 0.0),
    ]
    for index, (wants, owed, drawn) in enumerate(script):
        under_test.ctx_for("ev", demand=demand(wants=wants, required_kwh=owed, max_w=MAX_W))
        under_test.close(closed_slot(first + timedelta(hours=index), loads={"ev": drawn}))

    rec = under_test.accounting.state().ledger.loads["ev"]
    # 30 at the first plug-in, then only the 6 kWh drive: the shadow's car left full.
    assert rec.model_cf_kwh == pytest.approx(36.0)
