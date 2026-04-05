"""D3 §9 14 - per-phase headroom, in amps.

Per-phase power is ill-defined on an IT system and every per-phase limit is an
ampere rating, so D3 keeps phases in amps (D3 §2). A load whose phase is unknown
is constrained by the minimum headroom across phases - conservative and correct.
"""

import pytest

from custom_components.powerplan.core.metering import (
    MeterSample,
    Phase,
    PhaseReadings,
    Quality,
    Reading,
    WindowMeter,
    headroom_a,
)
from tests.core.metering.conftest import config, local

AT = local(2026, 1, 15, 18, 30)


def test_14_per_phase_headroom_in_amps() -> None:
    """Headroom = limit − I, per phase, in amps (D3 §2)."""
    readings = PhaseReadings(amps=(20.0, 30.0, 40.0), at=AT, limit_a=63.0)
    assert readings.headroom_a() == pytest.approx((43.0, 33.0, 23.0))
    assert readings.min_headroom_a() == pytest.approx(23.0)


def test_14b_an_unknown_phase_takes_the_minimum_headroom() -> None:
    """`None` phases ⇒ the tightest phase bounds the load (D3 §2)."""
    readings = PhaseReadings(amps=(20.0, 30.0, 40.0), at=AT, limit_a=63.0)
    assert headroom_a(readings, None) == pytest.approx(23.0)
    assert headroom_a(readings, frozenset({Phase.L1})) == pytest.approx(43.0)
    assert headroom_a(readings, frozenset({Phase.L2, Phase.L3})) == pytest.approx(23.0)
    assert headroom_a(readings, frozenset()) == pytest.approx(23.0)


def test_14c_an_overloaded_phase_has_negative_headroom() -> None:
    """Nothing clamps headroom: a breach is a number D6 can act on (D3 §2)."""
    readings = PhaseReadings(amps=(70.0, 10.0, 10.0), at=AT, limit_a=63.0)
    assert readings.min_headroom_a() == pytest.approx(-7.0)


def test_14d_the_snapshot_carries_the_phase_limit_from_the_profile() -> None:
    """`limit_a` is `per_phase_limit_a` when set, else the main fuse (D3 §5.4 step 10)."""
    meter = WindowMeter(config(), None)
    now = local(2026, 1, 15, 18, 30, 30)
    snap = meter.sample(
        now,
        MeterSample(
            grid_w=Reading(9000.0, at=now, source="test"),
            phase_a=(
                Reading(13.0, at=now, source="l1"),
                Reading(14.0, at=now, source="l2"),
                Reading(12.0, at=now, source="l3"),
            ),
        ),
        (),
    )
    assert snap.phases is not None
    assert snap.phases.limit_a == 63.0
    assert snap.phases.amps == pytest.approx((13.0, 14.0, 12.0))
    assert snap.phases.min_headroom_a() == pytest.approx(49.0)


def test_14f_one_unavailable_current_sensor_drops_the_whole_reading() -> None:
    """A phase that reads 0 A because its sensor died is worse than no reading (INV-17)."""
    meter = WindowMeter(config(), None)
    now = local(2026, 1, 15, 18, 30, 30)
    snap = meter.sample(
        now,
        MeterSample(
            grid_w=Reading(9000.0, at=now, source="test"),
            phase_a=(
                Reading(13.0, at=now, source="l1"),
                Reading(0.0, at=now, source="l2", quality=Quality.UNAVAILABLE),
                Reading(12.0, at=now, source="l3"),
            ),
        ),
        (),
    )
    assert snap.phases is None


def test_14e_no_phase_entities_means_no_phase_readings() -> None:
    """A site without current sensors degrades explicitly, not to zeros (INV-53)."""
    meter = WindowMeter(config(), None)
    now = local(2026, 1, 15, 18, 30, 30)
    snap = meter.sample(now, MeterSample(grid_w=Reading(9000.0, at=now, source="test")), ())
    assert snap.phases is None
