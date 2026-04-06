"""The AMS meter: fast noisy power, and a register that only moves at the seam.

A Norwegian AMS meter pushes signed active power every ten seconds and reports
the cumulative import register **once per hour, about twelve seconds after the
boundary**.  That gap is the seam D3 §5.4 is built around, and a bug of the
ancestor controller lived in it: close the window on the wall clock and the last twelve
seconds land in the wrong hour.

So this simulator does not expose a register that tracks energy continuously.
`register_import_kwh` changes at boundary + ≈12 s and at no other time, it is
quantised to 0.01 kWh, its latch jitters by a few seconds, and now and then the
meter repeats the previous hour's value - the frame was stale and nobody said
so.

`step`'s command is the site's instantaneous signed power in W (import +,
export −): the meter measures the house, it is not commanded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .base import (
    REGISTER_EXPORT_KWH,
    REGISTER_IMPORT_KWH,
    W_PER_AMP_IT230_3P,
    Env,
    Reads,
    derive_rng,
    kwh,
)

SAMPLE_S = 10.0
NOISE_FRACTION = 0.01
NOISE_FLOOR_W = 5.0
LATCH_S = 12.0
LATCH_JITTER_S = 4.0
REGISTER_STEP_KWH = 0.01
REGISTER_REPEAT_P = 0.02
PHASE_IMBALANCE = 0.08

SOURCES: dict[str, str] = {
    "SAMPLE_S": "D3 §5.3: a fast register/power meter reports every 2–10 s; the HAN list-2 cadence",
    "NOISE_FRACTION": (
        "assumed: 1 % of magnitude. Norwegian AMS meters are class B (EN 50470-3, ±1 % on "
        "active energy); the instantaneous power sample is noisier than the register"
    ),
    "NOISE_FLOOR_W": "assumed: 5 W floor so a quiet house still jitters. Replaced by a captured trace",
    "LATCH_S": (
        "the reference house's AMS reports the hour's register at boundary + ≈12 s, inside "
        "D3 §5.4's seam_after_s = 15 and far inside register_grace_s = 90"
    ),
    "LATCH_JITTER_S": (
        "assumed: ±4 s on the latch, keeping it inside the 15 s seam. Replaced by the observed "
        "spread of the reference house's register timestamps"
    ),
    "REGISTER_STEP_KWH": (
        "assumed: the register is published with 0.01 kWh resolution. Replaced by the captured "
        "AMS entity's precision"
    ),
    "REGISTER_REPEAT_P": (
        "assumed: 2 % of hours repeat the previous value (a stale HAN frame). Replaced by a rate "
        "counted from recorder history — D3 §5.6's latched-mode grace exists for exactly this"
    ),
    "PHASE_IMBALANCE": (
        "assumed: 8 % spread between the three phase currents in a single-family house. Replaced "
        "by the captured L1/L2/L3 entities (D3 §5.1 lists them)"
    ),
    "W_PER_AMP_IT230_3P": "see sim/base.py — D3 §5.1",
}


@dataclass(slots=True)
class MeterSim:
    """An AMS meter over a simulated house."""

    seed: int = 0
    true_import_kwh: float = 0.0
    true_export_kwh: float = 0.0
    reported_import_kwh: float = 0.0
    reported_export_kwh: float = 0.0
    reported_power_w: float = 0.0
    outage_from: datetime | None = None
    outage_to: datetime | None = None
    latches: int = 0
    repeats: int = 0
    _pending: tuple[datetime, float, float] | None = field(default=None)
    _last_sample_index: int = field(default=-1)

    def inject_outage(self, start: datetime, seconds: float) -> None:
        """Make the meter go stale from `start` for `seconds` (D9 §4 `meter_stale`)."""
        self.outage_from = start
        self.outage_to = start + timedelta(seconds=seconds)

    def _stale_at(self, t: datetime) -> bool:
        return (
            self.outage_from is not None
            and self.outage_to is not None
            and self.outage_from <= t < self.outage_to
        )

    def _latch(self, boundary: datetime, import_kwh: float, export_kwh: float) -> None:
        """Queue the register report for `boundary`, with its jitter and its faults."""
        rng = derive_rng(self.seed, "latch", int(boundary.timestamp()))
        delay = LATCH_S + rng.uniform(-LATCH_JITTER_S, LATCH_JITTER_S)
        if rng.random() < REGISTER_REPEAT_P:
            self.repeats += 1
            import_kwh = self.reported_import_kwh
            export_kwh = self.reported_export_kwh
        self._pending = (boundary + timedelta(seconds=delay), import_kwh, export_kwh)

    def step(self, dt_s: float, command: float, env: Env) -> Reads:
        """Integrate `command` (signed W) over the step and report what a meter would."""
        t0, t1 = env.now, env.now + timedelta(seconds=dt_s)
        import_w = max(0.0, command)
        export_w = max(0.0, -command)

        # An hour boundary inside the step latches the register at the boundary itself.
        boundary = t0.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if t0 < boundary <= t1:
            before_s = (boundary - t0).total_seconds()
            self._latch(
                boundary,
                self.true_import_kwh + kwh(import_w, before_s),
                self.true_export_kwh + kwh(export_w, before_s),
            )

        self.true_import_kwh += kwh(import_w, dt_s)
        self.true_export_kwh += kwh(export_w, dt_s)

        if self._pending is not None and t1 >= self._pending[0]:
            _, imp, exp = self._pending
            self.reported_import_kwh = round(imp / REGISTER_STEP_KWH) * REGISTER_STEP_KWH
            self.reported_export_kwh = round(exp / REGISTER_STEP_KWH) * REGISTER_STEP_KWH
            self._pending = None
            self.latches += 1

        index = int(t1.timestamp() // SAMPLE_S)
        if index != self._last_sample_index:
            self._last_sample_index = index
            rng = derive_rng(self.seed, "power", index)
            sigma = NOISE_FRACTION * abs(command) + NOISE_FLOOR_W
            self.reported_power_w = command + rng.gauss(0.0, sigma)

        if self._stale_at(t1):
            return Reads(
                power_w=self.reported_power_w,
                amps=self._amps(self.reported_power_w),
                available=False,
                status="stale",
                values={},
            )

        return Reads(
            power_w=self.reported_power_w,
            amps=self._amps(self.reported_power_w),
            status="ok",
            values={
                REGISTER_IMPORT_KWH: self.reported_import_kwh,
                REGISTER_EXPORT_KWH: self.reported_export_kwh,
            },
        )

    def _amps(self, power_w: float) -> tuple[float, float, float]:
        """Per-phase currents with a seeded imbalance (D3 §5.1 arithmetic)."""
        base = power_w / W_PER_AMP_IT230_3P
        rng = derive_rng(self.seed, "imbalance", self._last_sample_index)
        factors = [1.0 + rng.uniform(-PHASE_IMBALANCE, PHASE_IMBALANCE) for _ in range(3)]
        mean = sum(factors) / 3.0
        return tuple(base * f / mean for f in factors)  # type: ignore[return-value]
