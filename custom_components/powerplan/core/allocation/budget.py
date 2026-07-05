"""The budget chain: ceiling → reserve → allowance, and the PI trim (D6 §5.1).

    ceiling  ← D2.ceiling_kwh(now, target, risk, ε)      ε in kWh, scaled by window
    reserve  ← clamp(max(σ_uc, σ_floor) × k × t_rem + r_trim, min, max)
    E_budget ← ceiling − used − reserve                  signed
    P_allow  ← max(0, E_budget / t_rem × 1000), capped by P_hard

**The guard band is in kWh, never in watts** (INV-34). Subtract a 0.3 kW power
margin from the allowance and the protection is `0.3 × t_rem` kWh - 0.275 kWh at
:05 and 0.025 kWh at:55 - so it evaporates exactly when nothing can be corrected
any more. ε arrives already subtracted, inside D2's `Ceiling`; there is no
watt-margin parameter in this module and there never will be.

**The reserve shrinks with the window** (INV-35). At:05 the oven can still
surprise the house for 55 minutes; at:58 it cannot. σ is measured on
**uncontrolled** power (D3 §5.8, INV-16) - feed it the total and our own shedding
inflates σ, which inflates the reserve, which triggers more shedding.

**The σ floor holds under any forecast** (INV-62). A baseline improves the
*projection*; the reserve still covers deviation from it, and never below
`sigma_floor_w`. A forecast is never an authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ..tariffs import eps_for_window

if TYPE_CHECKING:
    from datetime import datetime

    from ..metering import MeterSnapshot
    from ..tariffs import Ceiling

__all__ = [
    "BASELINE_CONFIDENCE",
    "DEGRADED_BUMP_KWH",
    "SIGMA_FLOOR_W",
    "Baseline",
    "Budget",
    "BudgetCfg",
    "PiState",
    "allowance_w",
    "budget",
    "frozen_for",
    "is_outlier",
    "pi_close",
    "pi_update",
    "projection_kwh",
    "reserve_kwh",
]

#: The floor under σ, in watts (D6 §2, INV-62). A house is never perfectly
#: predictable, and a baseline that says it is has over-fitted.
SIGMA_FLOOR_W = 300.0

#: D6 §2's own gate on the baseline: below this the projection stays `smooth`
#: and the reserve stays on the live EMA σ. Same number as D10's own
#: `OFFER_CONFIDENCE` (`core/forecasts/model.py`) but owned separately here -
#: `core/allocation` never imports `core/forecasts` (D-0313, D-0316).
BASELINE_CONFIDENCE = 0.6

#: What a degraded anchor adds to the reserve, in kWh (D6 §5.1): `used` is an
#: estimate, so hold more back rather than trust it.
DEGRADED_BUMP_KWH = 0.2

#: With less than a second left the answer is always zero: nothing can happen in
#: the time remaining, and `0.05 kWh / 0.0001 h` is 500 kW of nonsense.
_SECOND_H = 1.0 / 3600.0


class Baseline(Protocol):
    """D10's forecast of the uncontrolled load, as D6 §2 asks it (D-0319).

    `core/forecasts/model.py::BudgetForecast` satisfies this structurally, built
    fresh each tick by `runtime.py` around `HourOfWeekBaseline` and handed in
    through `Inputs.forecast_baseline` - `core/allocation` never imports
    `core/forecasts` (the one-way direction D-0313 and D-0316 already keep).
    """

    @property
    def confidence(self) -> float:
        """How much the fit is worth trusting, 0–1; D6 §2 wants ≥ `BASELINE_CONFIDENCE`."""
        ...

    def energy_kwh(self, start: datetime, hours: float) -> float:
        """Return the energy the uncontrolled load is forecast to take."""
        ...

    def residual_sigma_w(self, t: datetime) -> float | None:
        """Return the bin's residual σ in W, or `None` with fewer than two samples.

        `None` is never treated as zero (INV-62): `reserve_kwh` floors whatever it
        is handed, so a `None` here falls back to the live EMA `σ_uc`, never to
        "no deviation".
        """
        ...


@dataclass(frozen=True, slots=True)
class BudgetCfg:
    """The site knobs of the budget chain (D6 §6, defaults from effektstyring)."""

    #: ε per 60 minutes, in kWh. Scaled by the window, never by the target.
    eps_base_kwh: float = 0.30
    k: float = 1.5
    min_kwh: float = 0.10
    max_kwh: float = 2.00
    sigma_floor_w: float = SIGMA_FLOOR_W
    degraded_bump_kwh: float = DEGRADED_BUMP_KWH
    ki: float = 0.05
    clamp_kwh: tuple[float, float] = (-0.5, 1.5)
    target_utilisation: float = 0.95
    #: What one unit of utilisation error is worth in kWh of reserve.
    pi_scale_kwh: float = 10.0
    #: μ + k·σ on uncontrolled power: above it, the window is a Sunday roast.
    outlier_k: float = 3.0


@dataclass(frozen=True, slots=True)
class PiState:
    """The PI integrator and what the window in progress has seen (D6 §5.1, §7).

    `binding` is recorded from the **budget** stage only: a stage raised because we
    were blind is not "the controller constrained something", and without that
    distinction a run of restarts drives `r_trim` to its clamp floor on windows
    where nothing was ever held back (seen on the reference house).
    """

    r_trim_kwh: float = 0.0
    binding: bool = False
    degraded: bool = False
    outlier: bool = False

    def saw_binding(self) -> PiState:
        """Return the state with this window marked binding, unless it is degraded."""
        if self.degraded:
            return self
        return replace(self, binding=True)

    def saw_degraded(self) -> PiState:
        """Return the state with this window degraded, which suppresses `binding`."""
        return replace(self, degraded=True, binding=False)

    def saw_outlier(self) -> PiState:
        """Return the state with an uncontrolled outlier recorded this window."""
        return replace(self, outlier=True)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-able form D7 persists (D6 §7)."""
        return {
            "r_trim_kwh": self.r_trim_kwh,
            "binding": self.binding,
            "degraded": self.degraded,
            "outlier": self.outlier,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiState:
        """Return the state `as_dict()` wrote."""
        return cls(
            r_trim_kwh=float(data["r_trim_kwh"]),
            binding=bool(data["binding"]),
            degraded=bool(data["degraded"]),
            outlier=bool(data["outlier"]),
        )


@dataclass(frozen=True, slots=True)
class Budget:
    """What this window may still spend, and how fast (D6 §4)."""

    ceiling_kwh: float
    eps_kwh: float
    used_kwh: float
    t_rem_h: float
    reserve_kwh: float
    sigma_w: float
    r_trim_kwh: float
    #: `(ceiling − used − reserve) / t_rem`, floored at 0 and capped by `p_hard_w`.
    p_allow_w: float
    #: The tightest site hard limit now - fuse, contracted power, external limit.
    p_hard_w: float
    #: The allowance less the uncontrolled baseline: free power before any load
    #: asks. `AllocReport.p_free_w` is the residual after the walk (D6 §4).
    p_free_w: float
    projected_kwh: float
    projection_source: Literal["smooth", "baseline"]
    eligible: bool
    free_ride: bool
    #: Signed: the ladder has to be able to see that the window is already over.
    e_budget_kwh: float


def _clamp(low: float, value: float, high: float) -> float:
    """Return `value` bounded to `[low, high]`."""
    return max(low, min(high, value))


def reserve_kwh(
    t_rem_h: float,
    sigma_w: float | None,
    r_trim_kwh: float,
    cfg: BudgetCfg,
    *,
    degraded: bool = False,
) -> float:
    """Return the reserve held against uncontrolled load, in kWh (INV-35, INV-62).

    `sigma_w` is the standard deviation of **uncontrolled** power; `None` - no
    samples yet - is not "no deviation", so the floor applies to it too.
    """
    sigma = cfg.sigma_floor_w if sigma_w is None else max(sigma_w, cfg.sigma_floor_w)
    energy = sigma / 1000.0 * cfg.k * max(0.0, t_rem_h) + r_trim_kwh
    if degraded:
        energy += cfg.degraded_bump_kwh
    return _clamp(cfg.min_kwh, energy, cfg.max_kwh)


def allowance_w(
    ceiling_kwh: float,
    used_kwh: float,
    reserve: float,
    t_rem_h: float,
    p_hard_w: float,
) -> tuple[float, float]:
    """Return `(P_allow, E_budget)` for the window (D6 §5.1).

    `E_budget` is deliberately **signed** - the ladder has to be able to see that
    the ceiling is already spent, and a floor of zero would hide it. `P_allow` is
    floored at zero, because "you may draw −400 W" is not an instruction any
    actuator can follow.
    """
    e_budget = ceiling_kwh - used_kwh - reserve
    if t_rem_h <= _SECOND_H:
        return 0.0, e_budget
    return min(max(0.0, e_budget / t_rem_h * 1000.0), p_hard_w), e_budget


def projection_kwh(used_kwh: float, power_w: float, t_rem_h: float) -> float:
    """Return where the window lands if everything continues as it is (INV-38).

    `power_w` is the **smoothed** total for the ladder. Fed the instantaneous
    reading it is at its most pessimistic exactly when the window is emptiest.
    """
    return used_kwh + power_w / 1000.0 * max(0.0, t_rem_h)


def is_outlier(
    peak_uncontrolled_w: float, mean_w: float, sigma_w: float | None, k: float = 3.0
) -> bool:
    """Return whether the window's uncontrolled peak was μ + k·σ (D6 §5.1).

    A perfectly flat window (σ = 0) can never produce one: without variance there
    is nothing to call unusual.
    """
    if sigma_w is None or sigma_w <= 0.0:
        return False
    return peak_uncontrolled_w > mean_w + k * sigma_w


def pi_update(pi: PiState, utilisation: float, cfg: BudgetCfg) -> PiState:
    """Return `pi` with the integrator moved by this window's utilisation (INV-35).

    The sign is the point: a binding window that **over**-used its ceiling grows
    the reserve, one that under-used it - we held loads back for nothing - shrinks
    it. Inverted, this is positive feedback.
    """
    if pi.outlier or not pi.binding:
        return pi
    low, high = cfg.clamp_kwh
    moved = pi.r_trim_kwh + cfg.ki * (utilisation - cfg.target_utilisation) * cfg.pi_scale_kwh
    return replace(pi, r_trim_kwh=_clamp(low, moved, high))


def pi_close(pi: PiState, closed_kwh: float, ceiling_kwh: float, cfg: BudgetCfg) -> PiState:
    """Return the state for the next window: integrate, then reset the flags (§5.1).

    Called once per **closed** window (D3 hands them over on `MeterSnapshot.closed`).
    A window with no ceiling to measure against cannot say anything about the
    reserve.
    """
    updated = pi if ceiling_kwh <= 0.0 else pi_update(pi, closed_kwh / ceiling_kwh, cfg)
    return replace(updated, binding=False, degraded=False, outlier=False)


def frozen_for(meter: MeterSnapshot) -> bool:
    """Return whether this tick is blind and must hold still (INV-15, INV-17).

    A stale meter or a window seam. D3 has already decided and named it; D6 only
    refuses to act on it - blindness never opens a gate.
    """
    return meter.frozen_reason is not None


def budget(  # noqa: PLR0917 - D6 §3's signature, positional as the LLD writes it
    ceiling: Ceiling,
    meter: MeterSnapshot,
    hard_limit_w: float,
    pi: PiState,
    cfg: BudgetCfg,
    baseline: Baseline | None,
    controlled_planned_kwh: float = 0.0,
) -> Budget:
    """Return the whole chain's answer for this tick (D6 §3, §5.1, §2).

    An **ineligible** window - D2 answers `+inf` because the tariff does not
    measure it - has no ceiling at all: only the hard limits bind (item 1 of the
    precedence).

    `baseline`, when its confidence clears `BASELINE_CONFIDENCE`, sharpens the
    projection to `used + controlled_planned_kwh + ∫baseline` instead of the
    smoothed-power extrapolation, and may replace the reserve's σ with D10's own
    residual - `controlled_planned_kwh` is the caller's own Σ over the loads'
    plans for `[now, now + t_rem)` (`Plan.kwh_between`, D-0319). Below the
    gate, or with no baseline at all, both stay exactly as they were (D6 §2).
    """
    eps_kwh = eps_for_window(cfg.eps_base_kwh, meter.window_min)

    sigma_w = meter.sigma_uncontrolled_w
    projected_kwh: float | None = None
    projection_source: Literal["smooth", "baseline"] = "smooth"
    if baseline is not None and baseline.confidence >= BASELINE_CONFIDENCE:
        resid = baseline.residual_sigma_w(meter.now)
        if resid is not None:
            sigma_w = resid
        projected_kwh = (
            meter.used_kwh + controlled_planned_kwh + baseline.energy_kwh(meter.now, meter.t_rem_h)
        )
        projection_source = "baseline"

    reserve = reserve_kwh(
        meter.t_rem_h, sigma_w, pi.r_trim_kwh, cfg, degraded=meter.health.degraded
    )
    if ceiling.eligible:
        p_allow, e_budget = allowance_w(
            ceiling.kwh, meter.used_kwh, reserve, meter.t_rem_h, hard_limit_w
        )
    else:
        p_allow = hard_limit_w
        e_budget = ceiling.kwh - meter.used_kwh - reserve

    if projected_kwh is None:
        smooth_w = meter.grid_smooth_w if meter.grid_smooth_w is not None else (meter.grid_w or 0.0)
        projected_kwh = projection_kwh(meter.used_kwh, smooth_w, meter.t_rem_h)
    reported_sigma_w = cfg.sigma_floor_w if sigma_w is None else max(sigma_w, cfg.sigma_floor_w)
    return Budget(
        ceiling_kwh=ceiling.kwh,
        eps_kwh=eps_kwh,
        used_kwh=meter.used_kwh,
        t_rem_h=meter.t_rem_h,
        reserve_kwh=reserve,
        sigma_w=reported_sigma_w,
        r_trim_kwh=pi.r_trim_kwh,
        p_allow_w=p_allow,
        p_hard_w=hard_limit_w,
        p_free_w=max(0.0, p_allow - (meter.uncontrolled_w or 0.0)),
        projected_kwh=projected_kwh,
        projection_source=projection_source,
        eligible=ceiling.eligible,
        free_ride=ceiling.free_ride,
        e_budget_kwh=e_budget,
    )
