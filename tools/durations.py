"""Refresh `tests/durations.json` from a JUnit report (D9 §5.13).

The PR suite's wall clock is set by a handful of long simulations, not by the
two thousand short tests around them. pytest-xdist starts work in collection
order, so `tests/conftest.py` moves the longest xdist groups (and the longest
ungrouped tests) to the front, and this file is where it learns which those are.

Usage::

    uv run pytest -m "not perf and not backtest" -n auto --dist=loadgroup --junitxml=report.xml
    uv run python tools/durations.py report.xml

Only entries of at least `MIN_SECONDS` are kept: the order of the short tests
does not move the wall clock, and a small file is one a reviewer can read.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "tests" / "durations.json"

#: Below this a test's place in the queue does not matter.
MIN_SECONDS = 2.0


def durations(report: Path) -> dict[str, dict[str, float]]:
    """Return `{"groups": {name: s}, "tests": {nodeid: s}}` from a JUnit report."""
    groups: dict[str, float] = defaultdict(float)
    tests: dict[str, float] = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        seconds = float(case.get("time", 0.0))
        name = case.get("name", "")
        classname = case.get("classname", "")
        if "@" in name:
            groups[name.rsplit("@", 1)[1]] += seconds
            continue
        module, _, cls = classname.rpartition(".") if "." in classname else ("", "", classname)
        path = classname.replace(".", "/") + ".py"
        nodeid = f"{path}::{name}"
        if cls and cls[:1].isupper():
            nodeid = f"{module.replace('.', '/')}.py::{cls}::{name}"
        tests[nodeid] = tests.get(nodeid, 0.0) + seconds
    return {
        "groups": {k: round(v, 1) for k, v in sorted(groups.items()) if v >= MIN_SECONDS},
        "tests": {k: round(v, 1) for k, v in sorted(tests.items()) if v >= MIN_SECONDS},
    }


def main(argv: list[str] | None = None) -> int:
    """Write `tests/durations.json` from the report named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="a pytest --junitxml report")
    args = parser.parse_args(argv)
    data = durations(args.report)
    OUT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(REPO_ROOT)}: {len(data['groups'])} groups, {len(data['tests'])} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
