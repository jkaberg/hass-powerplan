"""Run a strategy as the runtime does, and judge it against the best plan (D9 §5.17).

`plan_all` every 15 minutes with the previous plan, the store moved by its own
model in between, one executed slot at a time. A run is an `Outcome` - what we
paid, what doing nothing special paid, what the exact optimum pays - and
`check()` holds it to the three promises of D-0770: never dearer than hands-off,
inside the band, and a captured share at or above the ratchet in `baseline.json`.

The ratchet moves up in the PR that earns it and down only with a DECISIONS
entry. `POWERPLAN_OPTIMALITY_RECORD=<dir>` makes `check()` write what it measured
instead of judging it, and `python -m tests.optimality.harness merge <dir>`
turns those files into a new `baseline.json`.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest

from custom_components.powerplan.core.loads import ConstantSchedule, TargetProfile
from custom_components.powerplan.core.loads.stores import EnergyStore, RoomStore, SlabStore
from custom_components.powerplan.core.model import Plan, PlanSlot, Urgency
from custom_components.powerplan.core.strategies import Headroom, plan_all
from tests.core.strategies.conftest import (
    Weather,
    battery_store,
    battery_view,
    curves_of,
    demand,
    floor_view,
    site_ctx,
)
from tests.optimality.days import DAYS, START, H, N, curve_of, hour_of
from tests.optimality.oracle import BatteryModel, RcModel, rc_optimum, rc_step

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "BATTERIES",
    "DAY_NAMES",
    "THERMAL_CASES",
    "BatteryRun",
    "Outcome",
    "ThermalCase",
    "ThermalRun",
    "battery_hands_off",
    "battery_loop",
    "battery_model",
    "check",
    "deficits_17_19",
    "share",
    "thermal_loop",
    "thermal_outcome",
]

BASELINE: Final = Path(__file__).with_name("baseline.json")
RECORD_ENV: Final = "POWERPLAN_OPTIMALITY_RECORD"
DAY_NAMES: Final = tuple(DAYS)

#: An optimum that saves less than this share of the hands-off cost (or this much
#: money, where hands-off costs nothing) asserts only "never dearer" (D9 §5.17).
_MIN_SAVING_SHARE: Final = 0.01
_MIN_SAVING_MONEY: Final = 0.01
#: Money noise below which "dearer" is not dearer: a tenth of a percent of the bill.
_DEARER_SHARE: Final = 1e-3
_DEARER_MONEY: Final = 1e-3


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a strategy paid on a day, what hands-off paid, and what was possible."""

    ours: float
    hands_off: float
    optimum: float

    @property
    def possible(self) -> float:
        """Return the money the optimum saves."""
        return self.hands_off - self.optimum

    @property
    def worthwhile(self) -> bool:
        """Return whether the optimum saves enough for a share to mean anything."""
        return self.possible >= max(_MIN_SAVING_SHARE * abs(self.hands_off), _MIN_SAVING_MONEY)

    @property
    def dearer(self) -> bool:
        """Return whether we paid more than hands-off."""
        return self.ours > self.hands_off + max(_DEARER_SHARE * abs(self.hands_off), _DEARER_MONEY)


def share(outcome: Outcome) -> float | None:
    """Return the captured share `(hands-off - ours) / (hands-off - optimum)`, or `None`."""
    if not outcome.worthwhile:
        return None
    return (outcome.hands_off - outcome.ours) / outcome.possible


def _baseline() -> dict[str, float | None]:
    return json.loads(BASELINE.read_text())  # type: ignore[no-any-return]


def check(
    key: str,
    outcome: Outcome,
    *,
    violation: str | None = None,
    known: Mapping[str, str] | None = None,
    exact: float | None = None,
) -> None:
    """Hold one strategy x day to D-0770's three promises.

    `violation` is a description of a band or deadline breach, `None` when there
    was none. `exact` is the share an exact strategy must reach (D-0770: it equals
    the optimum). `known` maps `"dearer"`, `"violation"` and `"exact"` to the work
    package that removes the failure: that failure is then an `xfail` naming it,
    and the share is still held to the ratchet before it.
    """
    measured = share(outcome)
    directory = os.environ.get(RECORD_ENV)
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / (key.replace("/", "__") + ".json")).write_text(
            json.dumps(
                {
                    "key": key,
                    "share": measured,
                    "dearer": outcome.dearer,
                    "violation": violation,
                    **_fields(outcome),
                }
            )
        )
        return
    floor = _baseline()[key]
    if measured is None:
        assert floor is None, f"{key}: the optimum saves too little now; re-record the baseline"
    else:
        assert floor is not None, f"{key}: no baseline; record one"
        assert measured >= floor - 1e-6, (
            f"{key}: captured {measured:.4f}, the ratchet is {floor:.4f} "
            f"(ours {outcome.ours:.4f}, hands-off {outcome.hands_off:.4f}, "
            f"optimum {outcome.optimum:.4f})"
        )
    failures = {}
    if violation is not None:
        failures["violation"] = f"{key}: {violation}"
    if outcome.dearer:
        failures["dearer"] = (
            f"{key}: ours {outcome.ours:.4f} is dearer than hands-off {outcome.hands_off:.4f}"
        )
    if exact is not None and measured is not None and measured < exact:
        failures["exact"] = (
            f"{key}: captured {measured:.4f} of the optimum, an exact strategy reaches {exact}"
        )
    for name, message in failures.items():
        if known and name in known:
            pytest.xfail(f"{known[name]}: {message}")
        pytest.fail(message)


def _fields(outcome: Outcome) -> dict[str, float]:
    return {"ours": outcome.ours, "hands_off": outcome.hands_off, "optimum": outcome.optimum}


def merge(directory: str) -> None:
    """Write `baseline.json` from the files `check()` recorded in `directory`."""
    rows = [json.loads(path.read_text()) for path in sorted(Path(directory).glob("*.json"))]
    baseline: dict[str, float | None] = {}
    for row in sorted(rows, key=lambda r: r["key"]):
        value = row["share"]
        baseline[row["key"]] = None if value is None else math.floor(value * 1e4) / 1e4
    BASELINE.write_text(json.dumps(baseline, indent=1) + "\n")
    sys.stdout.write(f"{len(baseline)} entries -> {BASELINE}\n")


# --------------------------------------------------------------------------- #
# Thermal stores: heat_capacitor and best_save
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ThermalCase:
    """A store, its element and its comfort band, built as the floor and room types do."""

    name: str
    store: SlabStore | RoomStore
    tau_h: float
    max_w: float
    target: float
    floor: float
    ceiling: float


def _slab(area: float, w_per_m2: float = 80.0) -> SlabStore:
    c = area * 0.040 * 2200.0 * 0.9 / 3600.0
    return SlabStore(
        area_m2=area,
        screed_mm=40.0,
        loss_coeff_w_per_k=c * 1000.0 / 60.0,
        max_c=27.0,
        coast_tau_h=60.0,
    )


def _room() -> RoomStore:
    return RoomStore.from_volume(
        volume_m3=40.0, max_c=25.0, min_c=5.0, heat_loss_w_per_k=100.0, coast_tau_h=12.0
    )


THERMAL_CASES: Final = {
    "slab12": ThermalCase("slab12", _slab(12.0), 60.0, 960.0, 24.0, 21.0, 27.0),
    "slab57": ThermalCase("slab57", _slab(57.5), 60.0, 4600.0, 24.0, 21.0, 27.0),
    "room40": ThermalCase("room40", _room(), 12.0, 4000.0, 21.0, 18.0, 25.0),
}


def rc_of(case: ThermalCase, t_out: float) -> RcModel:
    """Return the one-node model of `case`, which is the strategy's own coast model."""
    return RcModel(case.store.capacity_kwh_per_unit(), case.tau_h, case.max_w, t_out)


@dataclass(frozen=True, slots=True)
class ThermalRun:
    """A closed-loop day of a thermal store."""

    cost: float
    cost_hands_off: float
    temps: tuple[float, ...]
    end_value: float


def _thermal_view(case: ThermalCase, strategy: str, params: Mapping[str, Any], level: float) -> Any:
    profile = TargetProfile(
        schedule=ConstantSchedule(case.target),
        comfort_default=case.target,
        floor=case.floor,
        ceiling=case.ceiling,
    )
    return floor_view(
        strategy=strategy,
        kind="setpoint",
        store=case.store,
        target=profile,
        level_now=level,
        nameplate_w=case.max_w,
        demand=demand(
            required_kwh=0.5,
            deadline=None,
            min_w=0.0,
            max_w=case.max_w,
            urgency=Urgency.NORMAL,
            reason="comfort",
        ),
        params=dict(params),
    )


def _execute(rc: RcModel, target: float, t: float, slot: PlanSlot | None) -> tuple[float, float]:
    """Return `(T after, W drawn)` for one slot of the ideal thermostat under a plan slot.

    The thermostat draws what reaches the setpoint (target plus the slot's desired
    delta) by the slot's end, never more than the slot's envelope or the element.
    """
    cap = rc.max_w
    setpoint = target
    if slot is not None:
        state = slot.desired_state
        if isinstance(state, float):
            setpoint = target + state
        if slot.envelope_w is not None:
            cap = min(cap, max(0.0, slot.envelope_w))
    a = math.exp(-H / rc.tau_h)
    need = rc.ua_w * (setpoint - rc.t_out - (t - rc.t_out) * a) / (1.0 - a)
    power = min(max(need, 0.0), cap)
    return rc_step(rc, t, power, H), power


def thermal_loop(
    case: ThermalCase,
    strategy: str,
    params: Mapping[str, Any],
    prices: Sequence[float],
    t_out: float,
) -> ThermalRun:
    """Run `strategy` for 48 h from the target, re-planned every quarter hour, and hands-off.

    The stored heat left at the end is valued at the day's mean price (a kelvin is
    `C` kWh), for ours and for hands-off alike, so ending cool is not a saving.
    """
    rc = rc_of(case, t_out)
    curve = curve_of(prices)
    site = site_ctx(forecasts=Weather(outdoor=t_out))
    room = Headroom(by_slot={s.start: 1e5 for s in curve.slots})
    mean = sum(prices) / len(prices)
    t = case.target
    previous: Plan | None = None
    cost = 0.0
    temps: list[float] = []
    for k in range(N):
        now = START + timedelta(minutes=15 * k)
        view = _thermal_view(case, strategy, params, t)
        planned = plan_all(
            [view],
            curves_of(curve),
            site,
            now,
            headroom=room,
            previous=None if previous is None else {view.load_id: previous},
        )
        previous = planned.plans[view.load_id]
        t, power = _execute(rc, case.target, t, previous.slot_at(now))
        temps.append(t)
        cost += prices[k] * power * H / 1000.0
    end_value = (case.target - t) * rc.c_kwh_per_k * mean
    base = case.target
    base_cost = 0.0
    for k in range(N):
        base, power = _execute(rc, case.target, base, None)
        base_cost += prices[k] * power * H / 1000.0
    return ThermalRun(
        cost=cost + end_value,
        cost_hands_off=base_cost + (case.target - base) * rc.c_kwh_per_k * mean,
        temps=tuple(temps),
        end_value=end_value,
    )


def thermal_outcome(
    case: ThermalCase,
    strategy: str,
    params: Mapping[str, Any],
    day: str,
    t_out: float,
    *,
    lo: float,
    hi: float,
) -> tuple[Outcome, str | None]:
    """Run `strategy` on `day` and return its `Outcome` and any breach of `[lo, hi]`.

    The optimum is the LP over the same band, from the target to the target. The
    band is the strategy's own: `heat_capacitor` banks `bank_k` above and coasts
    `coast_k` below (never past the floor or the ceiling); `best_save` stays at or
    under the target.
    """
    prices = DAYS[day]
    run = thermal_loop(case, strategy, params, prices, t_out)
    optimum = rc_optimum(
        prices, rc_of(case, t_out), t0=case.target, lo=lo, hi=hi, terminal=case.target
    )
    assert optimum is not None
    tol = 0.1
    worst_low = min(run.temps) - lo
    worst_high = max(run.temps) - hi
    breach = None
    if worst_low < -tol or worst_high > tol:
        breach = f"left [{lo:.1f}, {hi:.1f}] C: {min(run.temps):.2f} .. {max(run.temps):.2f}"
    return Outcome(run.cost, run.cost_hands_off, optimum), breach


# --------------------------------------------------------------------------- #
# Batteries: arbitrage and peak_shave
# --------------------------------------------------------------------------- #

RESERVE_PCT: Final = 20.0
MAX_PCT: Final = 100.0
SOC0: Final = 50.0
USABLE: Final = 0.95

#: name -> (capacity kWh, max W, efficiency each way).
BATTERIES: Final = {"eff95": (10.0, 5000.0, 0.95), "eff80": (10.0, 5000.0, 0.80)}


def battery_model(name: str) -> BatteryModel:
    """Return the oracle's view of the battery `name`."""
    capacity, watts, eff = BATTERIES[name]
    return BatteryModel(capacity * USABLE / 100.0, watts, eff, eff, RESERVE_PCT, MAX_PCT)


@dataclass(frozen=True, slots=True)
class BatteryRun:
    """A closed-loop battery day: grid cost net of the stored energy kept, and what was missed."""

    cost: float
    unmet_kwh: float


def _apply(model: BatteryModel, soc: float, envelope_w: float) -> tuple[float, float]:
    """Return `(soc after, W actually exchanged)` for a quarter hour at `envelope_w`."""
    if envelope_w > 0.0:
        w = min(envelope_w, (MAX_PCT - soc) * model.kwh_per_pct / model.eta_c / H * 1000.0)
        return soc + w * H / 1000.0 * model.eta_c / model.kwh_per_pct, w
    if envelope_w < 0.0:
        room = max(0.0, (soc - RESERVE_PCT) * model.kwh_per_pct * model.eta_d / H * 1000.0)
        w = -min(-envelope_w, room)
        return soc + w * H / 1000.0 / model.eta_d / model.kwh_per_pct, w
    return soc, 0.0


def battery_loop(
    name: str,
    strategy: str,
    prices: Sequence[float],
    *,
    deficits_w: Sequence[float] | None = None,
    params: Mapping[str, Any] | None = None,
) -> BatteryRun:
    """Run a battery strategy for 48 h, re-planned every quarter hour.

    `deficits_w[k]` is how far the site is over its ceiling in slot `k` without the
    battery: the headroom is that much negative there, and `unmet_kwh` is what the
    battery's discharge failed to cover. The energy left in the store, against the
    start, is valued at the mean price times the discharge efficiency.
    """
    capacity, watts, eff = BATTERIES[name]
    model = battery_model(name)
    store: EnergyStore = battery_store(
        capacity_kwh=capacity,
        max_charge_w=watts,
        max_discharge_w=watts,
        charge_eff=eff,
        discharge_eff=eff,
        reserve_soc=RESERVE_PCT,
        min_soc=RESERVE_PCT + 1.0,
        max_soc=MAX_PCT,
    )
    curve = curve_of(prices)
    room = Headroom(
        by_slot={
            s.start: -deficits_w[k] if deficits_w and deficits_w[k] > 0.0 else 1e5
            for k, s in enumerate(curve.slots)
        }
    )
    value = sum(prices) / len(prices) * eff
    soc = SOC0
    cost = 0.0
    unmet = 0.0
    previous: Plan | None = None
    for k in range(N):
        now = START + timedelta(minutes=15 * k)
        view = battery_view(
            strategy=strategy,
            store=store,
            level_now=soc,
            nameplate_w=watts,
            params=dict(params or {}),
        )
        planned = plan_all(
            [view],
            curves_of(curve),
            site_ctx(),
            now,
            headroom=room,
            previous=None if previous is None else {view.load_id: previous},
        )
        previous = planned.plans[view.load_id]
        slot = previous.slot_at(now)
        envelope = 0.0 if slot is None or slot.envelope_w is None else slot.envelope_w
        soc, w = _apply(model, soc, envelope)
        cost += prices[k] * w * H / 1000.0
        if deficits_w:
            unmet += max(0.0, deficits_w[k] - max(0.0, -w)) * H / 1000.0
    return BatteryRun(cost - (soc - SOC0) * model.kwh_per_pct * value, unmet)


def battery_hands_off(
    name: str, prices: Sequence[float], deficits_w: Sequence[float] | None
) -> float:
    """Return the cost, net of the energy kept, of a battery that does the plain thing.

    Without a deficit it sits idle. With one it charges to full at once and covers
    the deficit whenever it comes: the peak-shaving battery nobody tuned.
    """
    model = battery_model(name)
    if deficits_w is None:
        return 0.0
    value = sum(prices) / len(prices) * model.eta_d
    soc = SOC0
    cost = 0.0
    for k in range(N):
        want = -deficits_w[k] if deficits_w[k] > 0.0 else model.max_w
        soc, w = _apply(model, soc, want)
        cost += prices[k] * w * H / 1000.0
    return cost - (soc - SOC0) * model.kwh_per_pct * value


def deficits_17_19() -> list[float]:
    """Return a 2 kW deficit 17:00-19:00 on both days: 8 kWh to cover."""
    return [2000.0 if 17 <= hour_of(k) < 19 else 0.0 for k in range(N)]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "merge":
        merge(sys.argv[2])
    else:
        sys.exit("usage: python -m tests.optimality.harness merge <record dir>")
