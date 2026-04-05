"""What a `MeterSource` hands the core, and how old it is (D3 §4).

`Quality` is the shared vocabulary from `core/model.py` and is re-exported here
so a provider or a consumer of this package imports one name from one place.
Nothing in this module knows where a reading came from: age is measured against
the receipt time the provider stamped, never against a device's own clock
(D3 §8, "clock skew between meter and HA").
"""

from dataclasses import dataclass
from datetime import datetime

from ..model import Quality

__all__ = ["MeterSample", "Quality", "Reading", "age", "is_fresh"]


@dataclass(frozen=True, slots=True)
class Reading:
    """One number from the outside world, with its provenance (D3 §4)."""

    value: float
    at: datetime
    source: str
    quality: Quality = Quality.OK


@dataclass(frozen=True, slots=True)
class MeterSample:
    """What a `MeterSource` yields per tick (D3 §4).

    Every role is optional: a site may have power only, a register only, or
    neither (INV-53 - the capacity axis then switches off, explicitly). Power is
    signed: import +, export − (INV-19).
    """

    grid_w: Reading | None = None
    import_kwh: Reading | None = None
    export_kwh: Reading | None = None
    production_w: Reading | None = None
    meter_window_kwh: Reading | None = None
    meter_window_start: datetime | None = None
    phase_a: tuple[Reading, ...] | None = None
    battery_charge_w: Reading | None = None


def age(reading: Reading | None, now: datetime) -> float | None:
    """Seconds since `reading` was taken, or `None` when there is no reading."""
    if reading is None:
        return None
    return (now - reading.at).total_seconds()


def is_fresh(reading: Reading | None, now: datetime, max_age_s: float) -> bool:
    """Say whether `reading` is present, OK and no older than `max_age_s`."""
    if reading is None or reading.quality is not Quality.OK:
        return False
    return (now - reading.at).total_seconds() <= max_age_s
