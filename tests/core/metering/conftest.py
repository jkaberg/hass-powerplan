"""Shared fixtures for the D3 metering tests (D9 §3).

Nothing here starts Home Assistant: `tests/core/` is the pure half of the suite
(D9 §3). The driver below is the only thing in the D3 tests that
knows how a provider assembles a `MeterSample`, so a test reads as a trace, a
register series and an assertion.
"""

import json
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.metering import (
    ClosedWindow,
    ControlledView,
    ElectricalProfile,
    MeterSample,
    MeterSnapshot,
    PendingClose,
    Reading,
    VoltageSystem,
    WindowMeter,
    WindowMeterConfig,
    WindowState,
)
from tests.builders import histories

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

OSLO = ZoneInfo("Europe/Oslo")

# The reference house (D3 §5.1, §6): 63 A, three-phase 230 V IT, ≈ 25.1 kW.
REFERENCE_PROFILE = ElectricalProfile(
    system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0, per_phase_limit_a=63.0
)


@pytest.fixture
def profile() -> ElectricalProfile:
    """Return the reference house's electrical profile."""
    return REFERENCE_PROFILE


def config(**kwargs: Any) -> WindowMeterConfig:
    """Build a `WindowMeterConfig` for the reference house (`window_min` 60)."""
    kwargs.setdefault("profile", REFERENCE_PROFILE)
    kwargs.setdefault("window_min", 60)
    kwargs.setdefault("tz", OSLO)
    return WindowMeterConfig(**kwargs)


@dataclass
class Run:
    """What driving a meter along a trace produced."""

    meter: WindowMeter
    snapshots: list[MeterSnapshot] = field(default_factory=list)
    closed: list[ClosedWindow] = field(default_factory=list)

    @property
    def last(self) -> MeterSnapshot:
        """Return the most recent snapshot."""
        return self.snapshots[-1]

    def at(self, moment: datetime) -> MeterSnapshot:
        """Return the snapshot taken at `moment`."""
        for snap in self.snapshots:
            if snap.now == moment:
                return snap
        raise AssertionError(f"no snapshot at {moment}")


def drive(
    meter: WindowMeter,
    trace: histories.Trace,
    reports: Sequence[tuple[datetime, float]] = (),
    *,
    times: Sequence[datetime] | None = None,
    controlled: Sequence[ControlledView] | Callable[[datetime], Sequence[ControlledView]] = (),
    production_w: float | None = None,
    battery_w: float | None = None,
    meter_window: Sequence[tuple[datetime, datetime, float]] = (),
    run: Run | None = None,
) -> Run:
    """Sample `meter` along `trace`, handing it the newest register report each tick.

    `meter_window` rows are `(at, window_start, kwh)` - a meter that computes
    the running window itself (D3 §2, anchor kind `meter_window`).
    """
    out = run if run is not None else Run(meter=meter)
    for now in times if times is not None else trace.times:
        report = histories.newest(reports, now)
        mw = [row for row in meter_window if row[0] <= now]
        sample = MeterSample(
            grid_w=Reading(trace.power_at(now), at=now, source="test"),
            import_kwh=(
                Reading(report[1], at=report[0], source="test") if report is not None else None
            ),
            production_w=(
                Reading(production_w, at=now, source="test") if production_w is not None else None
            ),
            meter_window_kwh=(Reading(mw[-1][2], at=mw[-1][0], source="test") if mw else None),
            meter_window_start=mw[-1][1] if mw else None,
            battery_charge_w=(
                Reading(battery_w, at=now, source="test") if battery_w is not None else None
            ),
        )
        views = controlled(now) if callable(controlled) else controlled
        snap = meter.sample(now, sample, views)
        out.snapshots.append(snap)
        out.closed.extend(snap.closed)
    return out


# --------------------------------------------------------------------------- #
# WindowState round-trip (D3 §7)
# --------------------------------------------------------------------------- #
# D7 owns the store; this is the shape it has to be able to write. Everything
# in `WindowState` is a primitive, an ISO-8601 string, a `StrEnum` or a tuple of
# those, and the decoder below is deliberately explicit so a divergence shows up
# as a test failure rather than as a silent `str()`.

_DATETIME_FIELDS = {
    "window_start_utc",
    "last_sample_at",
    "last_register_at",
    "start_utc",
    "deadline_utc",
}


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ClosedWindow | PendingClose):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def _decode(cls: type, raw: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        value = raw[f.name]
        if f.name in _DATETIME_FIELDS and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif f.name == "pending_closed":
            value = tuple(_decode(ClosedWindow, item) for item in value)
        elif f.name == "closing" and value is not None:
            value = _decode(PendingClose, value)
        elif f.name == "cadence_samples":
            value = tuple(value)
        kwargs[f.name] = value
    return cls(**kwargs)


def roundtrip(state: WindowState) -> WindowState:
    """`state` through JSON and back, as D7's store will take it (D3 §7)."""
    encoded = {f.name: _encode(getattr(state, f.name)) for f in fields(state)}
    revived: dict[str, Any] = json.loads(json.dumps(encoded))
    return _decode(WindowState, revived)


def restart(meter: WindowMeter, cfg: WindowMeterConfig | None = None) -> WindowMeter:
    """Restore a new meter from `meter`'s persisted state, as after a restart."""
    return WindowMeter(cfg if cfg is not None else meter.config, roundtrip(meter.state()))


def local(*args: int, tz: Any = OSLO) -> datetime:
    """Build a local wall-clock instant in the site's zone, as the meter sees it."""
    return datetime(*args, tzinfo=tz)
