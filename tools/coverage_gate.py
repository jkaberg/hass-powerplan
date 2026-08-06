#!/usr/bin/env python3
r"""Enforce D9 §2's coverage floors.

    uv run pytest -m "not perf and not backtest and not bench and not e2e" \\
        --cov --cov-report=json -q
    uv run python tools/coverage_gate.py

Reads `coverage.json` (`coverage.py`'s own JSON report, produced by
`--cov-report=json`) and checks each floor against the *aggregate* of every
file under its path - covered lines summed over statements summed, not an
average of per-file percentages, the same weighting `coverage report` itself
uses. `pyproject.toml`'s own `[tool.coverage.report]` comment names this
script as where the floors become a gate; a PR may not lower them
there or here. Exits 1 and prints every offending group on a miss.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The coverage floors (D9 §2): a path prefix under `custom_components/
#: powerplan/`, its line floor, and its branch floor (`None` where D9 names
#: none - only `core/` gets one).
FLOORS: tuple[tuple[str, float, float | None], ...] = (
    ("custom_components/powerplan/core/", 90.0, 85.0),
    ("custom_components/powerplan/providers/", 80.0, None),
    ("custom_components/powerplan/writegate.py", 100.0, None),
    ("custom_components/powerplan/flow/", 80.0, None),
)
OVERALL_PREFIX = "custom_components/powerplan/"
OVERALL_FLOOR = 85.0


@dataclass
class _Totals:
    statements: int = 0
    covered_lines: int = 0
    branches: int = 0
    covered_branches: int = 0

    def add(self, summary: dict[str, int]) -> None:
        """Fold one file's `coverage.json` summary into the running total."""
        self.statements += summary.get("num_statements", 0)
        self.covered_lines += summary.get("covered_lines", 0)
        self.branches += summary.get("num_branches", 0)
        self.covered_branches += summary.get("covered_branches", 0)

    @property
    def line_pct(self) -> float | None:
        """Return the aggregate line coverage, or `None` with nothing measured."""
        return None if self.statements == 0 else 100.0 * self.covered_lines / self.statements

    @property
    def branch_pct(self) -> float | None:
        """Return the aggregate branch coverage, or `None` with no branches."""
        return None if self.branches == 0 else 100.0 * self.covered_branches / self.branches


def _load(report_path: Path) -> dict[str, dict[str, int]]:
    """Return `{relative_path: summary}` from `coverage.json`, repo-relative keys."""
    data = json.loads(report_path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, int]] = {}
    for raw_path, entry in data["files"].items():
        try:
            rel = Path(raw_path).resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = raw_path.replace("\\", "/")
        out[rel] = entry["summary"]
    return out


def check(report_path: Path) -> list[str]:
    """Return one failure line per floor missed; empty means every gate holds."""
    files = _load(report_path)
    failures: list[str] = []

    for prefix, line_floor, branch_floor in FLOORS:
        totals = _Totals()
        for rel, summary in files.items():
            if rel == prefix or rel.startswith(prefix):
                totals.add(summary)
        line_pct = totals.line_pct
        if line_pct is None:
            failures.append(f"{prefix}: no covered file matched — check the path")
            continue
        if line_pct < line_floor:
            failures.append(f"{prefix}: {line_pct:.1f}% lines, floor is {line_floor:.0f}%")
        if branch_floor is not None:
            branch_pct = totals.branch_pct
            if branch_pct is not None and branch_pct < branch_floor:
                failures.append(
                    f"{prefix}: {branch_pct:.1f}% branches, floor is {branch_floor:.0f}%"
                )

    overall = _Totals()
    for rel, summary in files.items():
        if rel.startswith(OVERALL_PREFIX):
            overall.add(summary)
    overall_pct = overall.line_pct
    if overall_pct is None or overall_pct < OVERALL_FLOOR:
        got = "nothing measured" if overall_pct is None else f"{overall_pct:.1f}%"
        failures.append(f"overall: {got} lines, floor is {OVERALL_FLOOR:.0f}%")

    return failures


def main() -> int:
    """Check `coverage.json` against every floor and print the verdict."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, default=REPO_ROOT / "coverage.json", help="coverage.py JSON report"
    )
    args = parser.parse_args()

    if not args.report.exists():
        print(f"{args.report} not found — run pytest with --cov-report=json first", file=sys.stderr)
        return 2

    failures = check(args.report)
    if failures:
        print("Coverage floor(s) missed (D9 §2):")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("Every coverage floor holds (D9 §2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
