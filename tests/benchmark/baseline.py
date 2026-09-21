"""Baselines and tolerances (D9 §5.11): load, compare, render, update.

A baseline is one JSON file per house holding, per tier, the `BenchmarkResult`
that set it, the build that produced it and the WP that committed it. Every
metric the comparison reads has a tolerance kind: `zero` (must be 0), `not_worse`
(may not grow), `pct` (within a percentage of the baseline) or `abs` (under a
fixed bound). A breach is a row in the table and a non-zero exit, never a
loosened tolerance (D9 §5.11).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

BASELINES = Path(__file__).resolve().parent / "baselines"
CHANGELOG = Path(__file__).resolve().parents[2] / "design" / "benchmarks" / "CHANGELOG.md"

Kind = Literal["zero", "not_worse", "not_less", "pct", "abs"]


@dataclass(frozen=True, slots=True)
class Tolerance:
    """How far a metric may move before the comparison fails (D9 §4)."""

    kind: Kind
    value: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the tolerance as the baseline stores it."""
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Tolerance:
        """Return the tolerance a baseline stored."""
        return cls(kind=raw["kind"], value=raw.get("value"))


#: D9 §5.11's per-metric rules for the first baseline. The `zero` class starts as
#: `not_worse` and becomes `zero` in the PR whose WP enables it; `fee` is money
#: and may not grow; `writes` may drift ten per cent; the timings are D9 §5.1's
#: budgets (tick < 50 ms with 20 loads, planning < 500 ms). §5.9's 500 ticks/s
#: is the runner's goal and is reported, not gated, until it is met (D-0264).
DEFAULT_TOLERANCES: dict[str, Tolerance] = {
    "total.over_target": Tolerance("not_worse"),
    "total.comfort_violation_min": Tolerance("not_worse"),
    "total.deadline_misses": Tolerance("not_worse"),
    "total.sessions_dropped": Tolerance("not_worse"),
    "total.engine_failures": Tolerance("zero"),
    "total.zero_amp_writes": Tolerance("zero"),
    "total.commitment_breaks": Tolerance("zero"),
    "total.plan_gaps": Tolerance("zero"),
    "total.fee": Tolerance("not_worse"),
    "total.cost_energy": Tolerance("not_worse"),
    "total.savings": Tolerance("not_less"),
    #: Phase 7: the share of the production used at home; only a house with panels.
    "total.self_consumption": Tolerance("not_less"),
    "total.writes": Tolerance("pct", 10.0),
    "perf.tick_p95_ms": Tolerance("abs", 50.0),
    "perf.plan_p95_ms": Tolerance("abs", 500.0),
}


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the comparison table."""

    metric: str
    baseline: Any
    now: Any
    tolerance: Tolerance
    ok: bool

    @property
    def verdict(self) -> str:
        """Return the mark the table prints."""
        return "ok" if self.ok else "BREACH"


def path_for(house: str) -> Path:
    """Return the baseline file of `house`."""
    return BASELINES / f"{house}.json"


def load(house: str) -> dict[str, Any] | None:
    """Return the house's baseline document, or `None` when none was committed."""
    path = path_for(house)
    if not path.is_file():
        return None
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def save(house: str, document: dict[str, Any]) -> Path:
    """Write the baseline document and return its path."""
    path = path_for(house)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _number(value: Any) -> float | None:
    """Return a metric as a number: money strings are `"<amount> <currency>"`."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        head = value.split(" ", 1)[0]
        try:
            return float(head)
        except ValueError:
            return None
    return None


def _lookup(document: dict[str, Any], dotted: str) -> Any:
    node: Any = document
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def compare(
    baseline: dict[str, Any] | None,
    result: dict[str, Any],
    perf: dict[str, Any],
    tolerances: dict[str, Tolerance],
) -> list[Row]:
    """Return one row per tolerated metric; `ok` is False on a breach (D9 §5.11).

    Without a baseline only the `zero` and `abs` rules can fail: there is
    nothing to be worse than.
    """
    now_doc = {**result, "perf": perf}
    rows: list[Row] = []
    for metric, tolerance in sorted(tolerances.items()):
        now = _number(_lookup(now_doc, metric))
        was = None if baseline is None else _number(_lookup(baseline, metric))
        rows.append(
            Row(
                metric=metric,
                baseline=None if baseline is None else _lookup(baseline, metric),
                now=_lookup(now_doc, metric),
                tolerance=tolerance,
                ok=_holds(tolerance, was, now),
            )
        )
    return rows


def _holds(tolerance: Tolerance, was: float | None, now: float | None) -> bool:
    if now is None:
        return tolerance.kind not in ("zero", "abs")
    if tolerance.kind == "zero":
        return now == 0.0
    if tolerance.kind == "abs":
        return tolerance.value is None or now <= tolerance.value
    if was is None:
        return True
    if tolerance.kind in ("not_worse", "not_less"):
        return now <= was + 1e-9 if tolerance.kind == "not_worse" else now >= was - 1e-9
    limit = was * (1.0 + (tolerance.value or 0.0) / 100.0)
    return now <= limit + 1e-9


def render(rows: list[Row], *, title: str) -> str:
    """Return the markdown table a PR description carries (D9 §5.11)."""
    lines = [
        f"### {title}",
        "",
        "| metric | baseline | now | tolerance | |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        tolerance = row.tolerance.kind
        if row.tolerance.value is not None:
            tolerance += f" {row.tolerance.value:g}"
        lines.append(
            f"| {row.metric} | {_cell(row.baseline)} | {_cell(row.now)} | {tolerance} | {row.verdict} |"
        )
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def changelog_mentions(build: str) -> bool:
    """Whether `design/benchmarks/CHANGELOG.md` names the build a baseline carries (D9 §9 9)."""
    if not CHANGELOG.is_file() or not build:
        return False
    return build in CHANGELOG.read_text(encoding="utf-8")
