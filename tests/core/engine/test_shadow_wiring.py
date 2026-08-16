"""WP5.6's wiring: what the tank's and the schedule's shadows read reaches them.

D11 §4 gave `ShadowCtx` two tank fields - the household's draw-off for the slot
and whether the real legionella cycle is running - and nothing populated either
(`design/PLAN.md` WP3.3's row). The engine now says whether the cycle ran
(`SlotLoad.legionella_active`, from the tank's own latch), the adapter turns the
tank's D4 draw-off profile into the slot's kWh, and `params_of` hands each shadow
the load's own numbers - its dial, its standby, its daily hours (INV-69).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from custom_components.powerplan.core.accounting.close import AccountingConfig
from custom_components.powerplan.core.accounting.shadow.base import StoreKind
from custom_components.powerplan.core.accounting_hook import AccountingAdapter, params_of
from custom_components.powerplan.core.engine import Engine, EngineState, SlotClose, SlotLoad
from custom_components.powerplan.core.loads import LoadState
from custom_components.powerplan.core.loads.stores.thermal import DrawOffProfile
from custom_components.powerplan.core.model import Carrier, Mode
from tests.builders.curves import OSLO
from tests.core.accounting.conftest import load_slot
from tests.core.engine.conftest import (
    START,
    RecordingHook,
    curves,
    evaluator,
    inputs_at,
    site,
    window_meter,
)
from tests.core.loads.conftest import load_from


def _tank():
    return load_from(
        "water_heater",
        {
            "litres": "300",
            "element_kw": "3",
            "persons": 3.0,
            "ready_by": "06:30",
            "ready_temp_c": 75.0,
            "comfort_min_c": 45.0,
            "control": "thermostat",
            "legionella": "powerplan",
        },
        load_id="tank",
        qctx={"capabilities": frozenset({"setpoint", "water_heater"})},
    )


def test_params_of_hands_the_tank_its_dial_its_standby_and_its_household() -> None:
    """The dial is the tank's own `anchor_c` (D-0203), the draw-off its D4 §5.7 profile."""
    tank = _tank()
    params = params_of(tank)
    assert params.kind is StoreKind.TANK
    assert params.charge_setpoint == tank.config.params["anchor_c"] == 75.0
    assert params.standby_loss_w == tank.config.params["standby_loss_w"]
    assert params.draw_off == DrawOffProfile(persons=3)
    assert params.charge_eff == 0.98, "the element's η (D4 §4.3), not an EV charger's 0.9"
    assert params.nameplate_w == 3000.0


def test_params_of_hands_a_pool_pump_its_daily_hours() -> None:
    """`generic_switch` with a daily quota is the `schedule` shadow (D11 §5.3)."""
    pool = load_from("generic_switch", {"appliance": "pool_pump"}, load_id="pool")
    params = params_of(pool)
    assert params.kind is StoreKind.SCHEDULE
    assert params.hours_per_day == pool.config.params["hours_per_day"] == 8.0


class _Spy:
    """Record the `CloseCtx` the adapter builds, then let the real ledger have it."""

    def __init__(self, real: object) -> None:
        self.real = real
        self.ctxs: list[object] = []

    def close_slot(self, slot: object, ctx: object) -> object:
        self.ctxs.append(ctx)
        return self.real.close_slot(slot, ctx)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self.real, name)


def test_the_adapter_hands_the_tank_its_draw_off_and_the_cycle() -> None:
    """A morning quarter: the profile's kWh for exactly that quarter, and the engine's cycle flag."""
    tank = _tank()
    tariff = evaluator()
    adapter = AccountingAdapter(
        AccountingConfig(currency="NOK", tz=OSLO), [tank], tariff, tariff.history, now=START
    )
    spy = _Spy(adapter.accounting)
    adapter.accounting = spy  # type: ignore[assignment]
    start = (START + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=15)

    for running in (True, False):
        adapter.close_slot(
            SlotClose(
                start=start,
                end=end,
                now=end + timedelta(seconds=20),
                curves=curves(START - timedelta(hours=2)),
                site_import_kwh=1.0,
                site_export_kwh=0.0,
                site_confidence="exact",
                outdoor_c=-3.0,
                loads={
                    "tank": SlotLoad(
                        load_id="tank",
                        mode=Mode.AUTO,
                        demand=None,
                        level_now=60.0,
                        slot=load_slot("tank", start, 0.75, minutes=15),
                        legionella_active=running,
                    )
                },
            )
        )
        ctx = spy.ctxs[-1].loads["tank"]  # type: ignore[attr-defined]
        assert ctx.legionella_active is running
        assert ctx.draw_off_kwh > 0.0, "07:00 is inside the morning's draw window"
        assert ctx.draw_off_kwh == DrawOffProfile(persons=3).kwh_between(start, end, OSLO)
        start, end = end, end + timedelta(minutes=15)


def test_the_engine_says_whether_the_tanks_cycle_is_running_at_the_close() -> None:
    """`SlotLoad.legionella_active` is the tank's own latch (D4 §5.12), read at the slot's close."""
    cfg = site()
    tank = _tank()
    engine = Engine(cfg, window_meter(cfg), evaluator(), (tank,), accounting=RecordingHook())
    at = START + timedelta(minutes=20)
    inputs = inputs_at(cfg, at, grid_w=1000.0, curves_=curves(START - timedelta(hours=2)))
    assert inputs.curves is not None
    slot = next(
        slot
        for slot in inputs.curves.import_[Carrier.ELECTRICITY].slots
        if slot.end <= at and slot.end > at - timedelta(minutes=15)
    )

    idle = EngineState(loads={"tank": LoadState()})
    close = engine._slot_close(idle, inputs, slot, {}, None)
    assert close.loads["tank"].legionella_active is False

    running = replace(idle, loads={"tank": LoadState(legionella_in_progress_since=START)})
    close = engine._slot_close(running, inputs, slot, {}, None)
    assert close.loads["tank"].legionella_active is True
