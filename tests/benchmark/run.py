"""Run a house × year at a tier and fold the months into a `BenchmarkResult` (D9 §5.9, §5.11).

A tier is a set of spans; each span is one `Scenario` through the runner with
the year's faults inside it. The months are the runner's month rows plus the
capacity bill the tariff evaluator inside the house prices at the end; the
total sums them. Timings (`PerfMetrics`) ride beside the metrics and never
enter the digest.
"""

from __future__ import annotations

import json
import os
import time as _time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from importlib import import_module
from multiprocessing import get_context
from typing import Any

from custom_components.powerplan.core.tariffs.evaluator import Period
from tests.benchmark.year import SyntheticYear, y2026_27
from tests.scenarios.runner import Scenario, ScenarioResult, run_scenario

#: Tick and plan budgets the `abs` tolerances hold the build to (D9 §5.1).
TICK_P95_BUDGET_MS = 50.0
PLAN_P95_BUDGET_MS = 500.0

#: D9 §5.11: the spans of each tier, as local dates in the year's zone.
TIERS: dict[str, tuple[tuple[date, int], ...]] = {
    "smoke": ((date(2026, 10, 5), 7), (date(2027, 1, 11), 7)),
    "month": ((date(2027, 1, 1), 31),),
    "full": ((date(2026, 7, 1), 365),),
}

#: The first tick of a span: 00:00:17 local, never a round minute (INV-43).
SPAN_OFFSET = timedelta(seconds=17)


@dataclass(frozen=True)
class MonthRow:
    """D9 §4 `BacktestMetrics` for one month, the columns phase 0 can fill."""

    windows: int
    over_target: int
    max_window_kwh: float
    kwh: float
    comfort_violation_min: float
    deadline_misses: int
    writes: int
    fee: str | None
    level: str | None
    metric_kw: float | None
    cost_energy: str | None = None
    cost_counterfactual: str | None = None
    savings: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the row as JSON-able data, rounded the way the baseline stores it."""
        return {
            "windows": self.windows,
            "over_target": self.over_target,
            "max_window_kwh": round(self.max_window_kwh, 3),
            "kwh": round(self.kwh, 3),
            "comfort_violation_min": round(self.comfort_violation_min, 2),
            "deadline_misses": self.deadline_misses,
            "writes": self.writes,
            "fee": self.fee,
            "level": self.level,
            "metric_kw": None if self.metric_kw is None else round(self.metric_kw, 3),
            "cost_energy": self.cost_energy,
            "cost_counterfactual": self.cost_counterfactual,
            "savings": self.savings,
        }


@dataclass
class BenchmarkResult:
    """What one run of a house × year at a tier measured (D9 §4)."""

    house: str
    year: str
    tier: str
    build: str
    seed: int
    months: dict[str, MonthRow]
    total: dict[str, Any]
    spans: list[dict[str, Any]] = field(default_factory=list)
    perf: dict[str, Any] = field(default_factory=dict)
    controlled_share: float = 1.0
    wall_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Return the comparable result - the baseline stores this and the digest hashes it."""
        return {
            "house": self.house,
            "year": self.year,
            "tier": self.tier,
            "build": self.build,
            "seed": self.seed,
            "controlled_share": round(self.controlled_share, 4),
            "months": {key: row.as_dict() for key, row in sorted(self.months.items())},
            "total": dict(sorted(self.total.items())),
            "spans": self.spans,
        }

    @property
    def digest(self) -> str:
        """Return the byte-identity string: metrics, never timings."""
        return json.dumps(self.as_dict(), sort_keys=True)


def house_module(name: str) -> Any:
    """Return `tests.benchmark.houses.<name>`."""
    return import_module(f"tests.benchmark.houses.{name}")


def span_processes(spans: int) -> int:
    """How many processes run a tier's spans at once (D9 §5.13, T.1a).

    Each span starts from its own fresh house and year, so the spans are
    independent and the fold below is in span order either way: the result is
    byte-identical to running them one after another. `POWERPLAN_BENCH_PROCESSES=1`
    runs them in this process, which is what a profiler wants.
    """
    raw = os.environ.get("POWERPLAN_BENCH_PROCESSES")
    if raw:
        return max(1, int(raw))
    return max(1, min(spans, os.cpu_count() or 1))


@dataclass(frozen=True)
class SpanSpec:
    """One span of a tier: everything a `spawn`ed process needs to run it."""

    house: str
    year: SyntheticYear
    tier: str
    first_day: date
    days: int
    seed: int
    target_kw: float

    def scenario(self) -> Scenario:
        """Return the scenario this span runs."""
        module = house_module(self.house)
        start = datetime.combine(self.first_day, datetime.min.time(), tzinfo=self.year.tz)
        start += SPAN_OFFSET
        end = start + timedelta(days=self.days)
        return Scenario(
            name=f"{self.house}:{self.tier}:{self.first_day.isoformat()}",
            house=lambda: module.build(self.year, self.first_day, seed=self.seed),
            start=start,
            days=float(self.days),
            target_kw=self.target_kw,
            faults=self.year.faults_between(start, end),
        )


def _run_span(spec: SpanSpec) -> ScenarioResult:
    """Run one span. Top-level, so a `spawn` process can be handed it by reference."""
    return run_scenario(spec.scenario())


def _run_spans(specs: list[SpanSpec]) -> list[ScenarioResult]:
    """Run every span, in parallel processes when there is more than one.

    `spawn`, not `fork` or `forkserver`, for the reasons D-0322 found: `fork`
    from a multi-threaded xdist worker can deadlock, and `pytest-socket` blocks
    the `forkserver`'s control socket.
    """
    workers = span_processes(len(specs))
    if workers <= 1:
        return [_run_span(spec) for spec in specs]
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(_run_span, spec) for spec in specs]
        return [future.result() for future in futures]


def run_benchmark(
    house: str = "nordic_detached",
    year: SyntheticYear | None = None,
    *,
    tier: str = "smoke",
    seed: int | None = None,
    build: str = "",
    target_kw: float = 10.0,
) -> BenchmarkResult:
    """Run every span of `tier` and fold the months (D9 §5.11)."""
    year = year or y2026_27()
    seed = year.seed if seed is None else seed
    started = _time.perf_counter()
    months: dict[str, dict[str, Any]] = {}
    bills: dict[str, tuple[str | None, str | None, float | None]] = {}
    money: dict[str, dict[str, Any]] = {}
    spans: list[dict[str, Any]] = []
    tick_ms: list[float] = []
    plan_ms: list[float] = []
    ticks = 0
    controlled_share = 1.0
    specs = [
        SpanSpec(house, year, tier, first_day, days, seed, target_kw)
        for first_day, days in TIERS[tier]
    ]
    for spec, result in zip(specs, _run_spans(specs), strict=True):
        scenario = spec.scenario()
        _fold_months(months, result)
        bills.update(_price_months(result, year))
        money.update(result.accounting_months)
        spans.append(_span_summary(scenario, result))
        tick_ms.extend(result.tick_ms)
        plan_ms.extend(result.plan_ms)
        ticks += result.ticks
        controlled_share = result.controlled_share
    rows = {
        key: MonthRow(
            windows=int(row["windows"]),
            over_target=int(row["over_target"]),
            max_window_kwh=float(row["max_window_kwh"]),
            kwh=float(row["kwh"]),
            comfort_violation_min=float(row["comfort_violation_min"]),
            deadline_misses=int(row["deadline_misses"]),
            writes=int(row["writes"]),
            fee=bills.get(key, (None, None, None))[0],
            level=bills.get(key, (None, None, None))[1],
            metric_kw=bills.get(key, (None, None, None))[2],
            cost_energy=money.get(key, {}).get("energy_cost"),
            cost_counterfactual=money.get(key, {}).get("cf_cost"),
            savings=money.get(key, {}).get("savings"),
        )
        for key, row in months.items()
    }
    wall = _time.perf_counter() - started
    return BenchmarkResult(
        house=house,
        year=year.name,
        tier=tier,
        build=build,
        seed=seed,
        months=rows,
        total=_total(rows, spans),
        spans=spans,
        perf={
            "ticks": ticks,
            "tick_p95_ms": round(_p95(tick_ms), 3),
            "plan_p95_ms": round(_p95(plan_ms), 3),
            "ticks_per_s": round(ticks / wall) if wall > 0 else None,
            "wall_s": round(wall, 1),
        },
        controlled_share=controlled_share,
        wall_s=wall,
    )


def _fold_months(into: dict[str, dict[str, Any]], result: ScenarioResult) -> None:
    for key, row in result.months.items():
        target = into.setdefault(key, dict.fromkeys(row, 0.0))
        for name, value in row.items():
            if name == "max_window_kwh":
                target[name] = max(float(target[name]), float(value))
            else:
                target[name] = target[name] + value


def _price_months(
    result: ScenarioResult, year: SyntheticYear
) -> dict[str, tuple[str | None, str | None, float | None]]:
    """Price each month the run touched off the tariff evaluator inside the house."""
    house = result.house
    if house is None:
        return {}
    out: dict[str, tuple[str | None, str | None, float | None]] = {}
    for key in result.months:
        year_no, month_no = (int(part) for part in key.split("-"))
        start = datetime(year_no, month_no, 1, tzinfo=year.tz)
        end = datetime(year_no + (month_no == 12), month_no % 12 + 1, 1, tzinfo=year.tz)
        bill = house.tariff.bill(Period(start=start, end=end, key=key))
        out[key] = (
            f"{bill.capacity_fee.amount:.2f} {bill.capacity_fee.currency}",
            bill.level.name,
            bill.metric_kw,
        )
    return out


def _span_summary(scenario: Scenario, result: ScenarioResult) -> dict[str, Any]:
    picked = result.as_dict()
    return {
        "name": scenario.name,
        "days": scenario.days,
        "ticks": result.ticks,
        "windows": picked["windows"],
        "over_target": picked["over_target"],
        "comfort_violation_min": picked["comfort_violation_min"],
        "deadline_misses": picked["deadline_misses"],
        "sessions_dropped": picked["sessions_dropped"],
        "ev_stops": picked["ev_stops"],
        "zero_amp_writes": picked["zero_amp_writes"],
        "commitment_breaks": picked["commitment_breaks"],
        "plan_gaps": picked["plan_gaps"],
        "engine_failures": picked["engine_failures"],
        "frozen_ticks": picked["frozen_ticks"],
        "writes": picked["writes"],
        "max_writes_per_10min": picked["max_writes_per_10min"],
    }


def _total(rows: dict[str, MonthRow], spans: list[dict[str, Any]]) -> dict[str, Any]:
    fee = 0.0
    currency = None
    for row in rows.values():
        if row.fee is not None:
            amount, currency = row.fee.split(" ")
            fee += float(amount)
    cost_energy = _sum_money(row.cost_energy for row in rows.values())
    cf_cost = _sum_money(row.cost_counterfactual for row in rows.values())
    savings = _sum_money(row.savings for row in rows.values())
    writes_by_load: dict[str, int] = {}
    for span in spans:
        for load_id, count in span["writes"].items():
            writes_by_load[load_id] = writes_by_load.get(load_id, 0) + count
    return {
        "windows": sum(row.windows for row in rows.values()),
        "over_target": sum(row.over_target for row in rows.values()),
        "max_window_kwh": round(max((row.max_window_kwh for row in rows.values()), default=0.0), 3),
        "kwh": round(sum(row.kwh for row in rows.values()), 3),
        "comfort_violation_min": round(sum(row.comfort_violation_min for row in rows.values()), 2),
        "deadline_misses": sum(row.deadline_misses for row in rows.values()),
        "sessions_dropped": sum(span["sessions_dropped"] for span in spans),
        "zero_amp_writes": sum(span["zero_amp_writes"] for span in spans),
        "commitment_breaks": sum(sum(span["commitment_breaks"].values()) for span in spans),
        "plan_gaps": sum(span["plan_gaps"] for span in spans),
        "engine_failures": sum(span["engine_failures"] for span in spans),
        "writes": sum(writes_by_load.values()),
        "writes_by_load": dict(sorted(writes_by_load.items())),
        "fee": None if currency is None else f"{fee:.2f} {currency}",
        "cost_energy": cost_energy,
        "cost_counterfactual": cf_cost,
        "savings": savings,
    }


def _sum_money(values: Any) -> str | None:
    """Sum `"<amount> <currency>"` strings; `None` when there were none."""
    total = 0.0
    currency = None
    for value in values:
        if value is None:
            continue
        amount, currency = value.split(" ")
        total += float(amount)
    return None if currency is None else f"{total:.2f} {currency}"


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
