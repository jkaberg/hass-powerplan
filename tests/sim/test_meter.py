"""The AMS meter: a register that only moves at the seam, and a signed power sample."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.sim.base import (
    REGISTER_EXPORT_KWH,
    REGISTER_IMPORT_KWH,
    W_PER_AMP_IT230_3P,
    Env,
)
from tests.sim.meter import (
    LATCH_JITTER_S,
    LATCH_S,
    REGISTER_STEP_KWH,
    MeterSim,
)

STEP_S = 10.0


def env(t: datetime) -> Env:
    """Build the ambient conditions of a winter hour."""
    return Env(now=t, outdoor_c=-5.0)


def trace(
    sim: MeterSim, start: datetime, steps: int, house_w: float
) -> list[tuple[datetime, float]]:
    """Step the meter and record (step start, reported import register)."""
    out: list[tuple[datetime, float]] = []
    t = start
    for _ in range(steps):
        reads = sim.step(STEP_S, house_w, env(t))
        out.append((t, reads.values.get(REGISTER_IMPORT_KWH, -1.0)))
        t += timedelta(seconds=STEP_S)
    return out


def test_the_register_only_moves_at_the_boundary_plus_twelve_seconds() -> None:
    """D3 §5.4's seam, and the ancestor controller's bug that lived in it."""
    sim = MeterSim(seed=3)
    start = datetime(2027, 1, 13, 10, 30, tzinfo=UTC)
    rows = trace(sim, start, steps=int(2.5 * 3600 / STEP_S), house_w=3000.0)

    changes = [t for (t, v), (_, prev) in zip(rows[1:], rows, strict=False) if v != prev]
    assert len(changes) == 2  # 11:00 and 12:00
    for t in changes:
        # The change is reported in the step whose window covers boundary + ~12 s.
        boundary = t.replace(minute=0, second=0, microsecond=0)
        offset = (t - boundary).total_seconds()
        assert 0.0 <= offset <= LATCH_S + LATCH_JITTER_S + STEP_S


def test_the_latched_value_is_the_energy_at_the_boundary_not_at_the_report() -> None:
    """Twelve seconds of a 3 kW house is 10 Wh: it belongs to the hour that ended."""
    sim = MeterSim(seed=3)
    start = datetime(2027, 1, 13, 10, 0, tzinfo=UTC)
    rows = trace(sim, start, steps=int(1.2 * 3600 / STEP_S), house_w=3600.0)
    latched = rows[-1][1]
    # 3.6 kW for exactly one hour = 3.6 kWh, quantised to 0.01.
    assert latched == pytest.approx(3.6, abs=REGISTER_STEP_KWH)
    assert latched == pytest.approx(round(latched / REGISTER_STEP_KWH) * REGISTER_STEP_KWH)


def test_the_export_register_moves_when_the_house_exports() -> None:
    """Power is signed: import +, export −, and the two registers are separate."""
    sim = MeterSim(seed=3)
    start = datetime(2027, 1, 13, 10, 0, tzinfo=UTC)
    rows: list[float] = []
    t = start
    for _ in range(int(1.1 * 3600 / STEP_S)):
        reads = sim.step(STEP_S, -2000.0, env(t))
        rows.append(reads.values.get(REGISTER_EXPORT_KWH, 0.0))
        t += timedelta(seconds=STEP_S)
    assert rows[-1] == pytest.approx(2.0, abs=REGISTER_STEP_KWH)
    assert sim.reported_import_kwh == 0.0


def test_the_power_sample_is_noisy_but_not_wild() -> None:
    """A 1 % + 5 W sample noise, resampled every ten seconds."""
    sim = MeterSim(seed=3)
    t = datetime(2027, 1, 13, 10, 0, tzinfo=UTC)
    samples: list[float] = []
    for _ in range(360):
        samples.append(sim.step(STEP_S, 4000.0, env(t)).power_w)
        t += timedelta(seconds=STEP_S)
    assert len(set(samples)) > 300
    assert max(abs(s - 4000.0) for s in samples) < 250.0
    assert sum(samples) / len(samples) == pytest.approx(4000.0, abs=20.0)


def test_per_phase_amps_follow_the_it_net_arithmetic_with_imbalance() -> None:
    """P = √3 × 230 × I on a balanced house; the phases differ by a few percent."""
    sim = MeterSim(seed=3)
    reads = sim.step(STEP_S, 6000.0, env(datetime(2027, 1, 13, 10, 0, tzinfo=UTC)))
    assert sum(reads.amps) / 3.0 == pytest.approx(reads.power_w / W_PER_AMP_IT230_3P, rel=1e-6)
    assert max(reads.amps) > min(reads.amps)


def test_an_outage_marks_the_meter_unavailable() -> None:
    """Blindness freezes the tick; it never opens a gate (INV-15, INV-17)."""
    sim = MeterSim(seed=3)
    start = datetime(2027, 1, 13, 10, 0, tzinfo=UTC)
    sim.inject_outage(start + timedelta(minutes=5), seconds=1800.0)
    rows: list[bool] = []
    t = start
    for _ in range(int(3600 / STEP_S)):
        rows.append(sim.step(STEP_S, 3000.0, env(t)).available)
        t += timedelta(seconds=STEP_S)
    assert rows.count(False) == pytest.approx(1800 / STEP_S, abs=1)
    assert rows[0] is True
    assert rows[-1] is True


def test_the_meter_sometimes_repeats_a_stale_frame() -> None:
    """A repeated register is what latched mode's grace exists for (D3 §5.6)."""
    sim = MeterSim(seed=3)
    t = datetime(2027, 1, 1, 0, 0, tzinfo=UTC)
    for _ in range(int(400 * 3600 / 60.0)):
        sim.step(60.0, 2000.0, env(t))
        t += timedelta(seconds=60.0)
    assert sim.latches > 350
    assert sim.repeats >= 1
