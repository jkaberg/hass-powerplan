"""Builders for the engine tests: a site, its loads, and one tick's inputs (D7 §3).

Everything here goes through the public constructors the runtime will use -
`WindowMeter`, `Evaluator` over a shipped preset, loads built through the type
registry - so a test that passes here is a tick the runtime can also run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.engine import (
    AccountingClose,
    Engine,
    EngineState,
    Inputs,
    Knobs,
    LoadReads,
    SiteConfig,
    SlotClose,
)
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.metering import (
    ElectricalProfile,
    MeterSample,
    Reading,
    VoltageSystem,
    WindowMeter,
    WindowMeterConfig,
)
from custom_components.powerplan.core.model import Carrier, Confidence, Direction, PriceCurve, Slot
from custom_components.powerplan.core.pricing.holidays import NO_HOLIDAYS
from custom_components.powerplan.core.strategies import Curves
from custom_components.powerplan.core.tariffs import Evaluator
from tests.builders.houses import fixture_preset
from tests.core.loads.conftest import ev_load, floor_load, reads

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from custom_components.powerplan.core.loads import Load

OSLO = ZoneInfo("Europe/Oslo")

#: A Tuesday evening in the heating season, 17 s past the quarter (HLD §7.1).
START = datetime(2026, 1, 13, 17, 15, 17, tzinfo=OSLO)

#: The reference house: 230 V IT, three phases, a 63 A main fuse (D9 §5.9).
PROFILE = ElectricalProfile(system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0)

TICK_S = 10.0


def site(**overrides: Any) -> SiteConfig:
    """Return the site as the runtime would materialise it."""
    options: dict[str, Any] = {
        "site_id": "site_test",
        "tz": OSLO,
        "electrical": PROFILE,
        "name": "Test",
    }
    options.update(overrides)
    return SiteConfig(**options)


def evaluator() -> Evaluator:
    """Return Tensio TS's evaluator with the synthetic 2027 version (INV-52)."""
    return Evaluator(fixture_preset("no/tensio-ts-2027"), tz=OSLO, calendar=NO_HOLIDAYS)


def window_meter(cfg: SiteConfig) -> WindowMeter:
    """Return a fresh window meter for the site."""
    return WindowMeter(
        WindowMeterConfig(profile=cfg.electrical, window_min=cfg.window_min, tz=cfg.tz), None
    )


def engine_for(
    loads: Sequence[Load], *, cfg: SiteConfig | None = None, accounting: Any = None
) -> Engine:
    """Wire an engine over `loads` the way `runtime.py` will."""
    cfg = cfg or site()
    return Engine(cfg, window_meter(cfg), evaluator(), loads, accounting=accounting)


def flat_curve(start: datetime, hours: int = 48, price: Decimal = Decimal("0.50")) -> PriceCurve:
    """Return a flat Norgespris-shaped 15-minute curve from the start of `start`'s hour."""
    origin = start.replace(minute=0, second=0, microsecond=0)
    slots = tuple(
        Slot(
            start=origin + timedelta(minutes=15 * index),
            end=origin + timedelta(minutes=15 * (index + 1)),
            total=price,
            components={"spot": price},
            confidence=Confidence.KNOWN,
        )
        for index in range(hours * 4)
    )
    return PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency="NOK",
        slots=slots,
        built_at=start,
        sources=("test",),
    )


def curves(start: datetime = START, **kwargs: Any) -> Curves:
    """Return the site's curves: one flat electricity import curve."""
    return Curves(import_={Carrier.ELECTRICITY: flat_curve(start, **kwargs)})


def meter_sample(
    at: datetime, grid_w: float, *, register_kwh: float | None = None, age_s: float = 0.0
) -> MeterSample:
    """Return one meter sample; `age_s` back-dates the readings to make them stale."""
    stamp = at - timedelta(seconds=age_s)
    return MeterSample(
        grid_w=Reading(value=grid_w, at=stamp, source="test"),
        import_kwh=None
        if register_kwh is None
        else Reading(value=register_kwh, at=stamp, source="test"),
    )


def ev_reads(
    at: datetime, *, amps: float = 0.0, status: str = "awaiting_start", soc: float = 40.0
) -> LoadReads:
    """Return what the charger's entities say."""
    return LoadReads(
        reads=reads(
            at,
            numbers={
                Role.POWER: amps * 230.0 * 3.0,
                Role.CURRENT_SET: amps,
                Role.SOC: soc,
                Role.CURRENT_MAX: 32.0,
            },
            texts={Role.STATUS: status, Role.ENABLE: "on" if amps > 0 else "off"},
        )
    )


def floor_reads(
    at: datetime, *, temp_c: float = 22.0, setpoint_c: float = 23.0, power_w: float = 0.0
) -> LoadReads:
    """Return what a floor thermostat's entities say."""
    return LoadReads(
        reads=reads(
            at,
            numbers={Role.TEMP: temp_c, Role.SETPOINT: setpoint_c, Role.POWER: power_w},
            texts={Role.MODE_SELECT: "Heating mode"},
            options={Role.MODE_SELECT: ("Off", "Heating mode", "Energy saving heating mode")},
        )
    )


def inputs_at(
    cfg: SiteConfig,
    at: datetime,
    *,
    grid_w: float,
    register_kwh: float | None = None,
    loads: Mapping[str, LoadReads] | None = None,
    knobs: Knobs | None = None,
    curves_: Curves | None = None,
    age_s: float = 0.0,
    trigger: str = "tick",
) -> Inputs:
    """Assemble one tick's inputs."""
    return Inputs(
        now=at,
        site=cfg,
        meter=meter_sample(at, grid_w, register_kwh=register_kwh, age_s=age_s),
        loads=dict(loads or {}),
        knobs=knobs or Knobs(),
        curves=curves_,
        outdoor_c=-3.0,
        trigger=trigger,
    )


def reference_loads() -> tuple[Load, Load]:
    """Return the two loads WP0.5 shipped: the EV and one floor loop."""
    return (
        ev_load(strategy="deadline_fill"),
        floor_load(strategy="always"),
    )


@dataclass
class RecordingHook:
    """An `AccountingHook` that only remembers what it was asked (D7 §9 16)."""

    calls: list[tuple[datetime, datetime, datetime]] = field(default_factory=list)
    windows: list[Any] = field(default_factory=list)

    def close_slot(self, close: SlotClose) -> AccountingClose:
        """Record the call and answer with an empty close."""
        self.calls.append((close.start, close.end, close.now))
        self.windows.append(close.window_closed)
        return AccountingClose(slot_start=close.start, slot_end=close.end)


def run(
    engine: Engine,
    state: EngineState,
    cfg: SiteConfig,
    *,
    start: datetime,
    ticks: int,
    grid_w: float | Any,
    loads: Any = None,
    knobs: Knobs | None = None,
    curves_: Curves | None = None,
) -> tuple[EngineState, list[Any], list[Any]]:
    """Tick `ticks` times at `TICK_S`; `grid_w` and `loads` may be callables of the instant."""
    snapshots: list[Any] = []
    effects: list[Any] = []
    for index in range(ticks):
        at = start + timedelta(seconds=TICK_S * index)
        power = grid_w(at) if callable(grid_w) else grid_w
        reads_now = loads(at) if callable(loads) else (loads or {})
        state, snapshot, effect = engine.tick(
            state, inputs_at(cfg, at, grid_w=power, loads=reads_now, knobs=knobs, curves_=curves_)
        )
        snapshots.append(snapshot)
        effects.append(effect)
    return state, snapshots, effects
