#!/usr/bin/env python3
"""Print the INV → tests matrix (D9 §3, §5.6).

`uv run python tools/inv_report.py` emits the markdown table that goes in a PR
description. It scans the test tree's `@pytest.mark.inv(...)` markers with the
`ast` module rather than by collecting a pytest session, so it runs with no
Home Assistant installed and gives the same answer whichever subset of the
suite is being run.

`tests/core/invariants/test_inv_traceability.py` imports the two parsers below,
so this file is the single source of truth for "which INVs are safety INVs" and
"which INVs have a marked test".
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
HLD = REPO_ROOT / "design" / "HLD.md"
TESTS = REPO_ROOT / "tests"

# HLD §7.5, verbatim: "**7.5 Safety invariants.** INV-1, 13–29, 34–48, 54–64,
# 68 are the product." The parser is deliberately strict: if the sentence ever
# changes shape, the traceability test fails loudly instead of silently
# shrinking the safety set.
_SAFETY_SECTION = re.compile(r"Safety invariants\.\*\*(?P<spec>.*?)are the product", re.DOTALL)
_RANGE = re.compile(r"^(\d+)\s*[–—-]\s*(\d+)$")
_SINGLE = re.compile(r"^(\d+)$")
_DECLARED = re.compile(r"\bINV-(\d+)\b")


class HldParseError(RuntimeError):
    """The HLD does not have the shape the parsers expect."""


def parse_safety_invs(hld: Path = HLD) -> tuple[str, ...]:
    """Return the safety INV ids from HLD §7.5, ranges expanded, in order."""
    text = hld.read_text(encoding="utf-8")
    match = _SAFETY_SECTION.search(text)
    if match is None:
        raise HldParseError(f"no '7.5 Safety invariants' sentence found in {hld}")

    numbers: list[int] = []
    spec = match.group("spec").replace("INV-", "")
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if found := _RANGE.match(part):
            lo, hi = int(found.group(1)), int(found.group(2))
            if hi < lo:
                raise HldParseError(f"descending INV range {part!r} in HLD §7.5")
            numbers.extend(range(lo, hi + 1))
        elif found := _SINGLE.match(part):
            numbers.append(int(found.group(1)))
        else:
            raise HldParseError(f"cannot read {part!r} in HLD §7.5")

    if not numbers:
        raise HldParseError("HLD §7.5 lists no safety invariants")
    return tuple(f"INV-{n}" for n in sorted(set(numbers)))


def parse_declared_invs(hld: Path = HLD) -> frozenset[str]:
    """Return every INV id the HLD mentions anywhere."""
    text = hld.read_text(encoding="utf-8")
    return frozenset(f"INV-{n}" for n in _DECLARED.findall(text))


def _inv_ids_from_decorator(node: ast.expr) -> Iterator[str]:
    """Yield the ids in one `pytest.mark.inv("INV-n", …)` expression."""
    if not isinstance(node, ast.Call):
        return
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "inv"):
        return
    if not (isinstance(func.value, ast.Attribute) and func.value.attr == "mark"):
        return
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            yield arg.value


def _pytestmark_values(node: ast.Assign) -> Iterator[ast.expr]:
    """Yield the mark expressions of a `pytestmark = …` assignment."""
    if not any(
        isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
    ):
        return
    value = node.value
    if isinstance(value, ast.List | ast.Tuple):
        yield from value.elts
    else:
        yield value


def scan_inv_markers(tests_root: Path = TESTS) -> dict[str, tuple[str, ...]]:
    """Map each INV id to the sorted test locations that mark it.

    A location is `<path relative to the repo root>::<test or class name>`, or
    `::<module>` for a module-level `pytestmark`.
    """
    found: dict[str, set[str]] = {}

    def record(inv_id: str, where: str) -> None:
        found.setdefault(inv_id, set()).add(where)

    for path in sorted(tests_root.rglob("test_*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                for decorator in node.decorator_list:
                    for inv_id in _inv_ids_from_decorator(decorator):
                        record(inv_id, f"{rel}::{node.name}")
            elif isinstance(node, ast.Assign):
                for mark in _pytestmark_values(node):
                    for inv_id in _inv_ids_from_decorator(mark):
                        record(inv_id, f"{rel}::<module>")

    return {inv_id: tuple(sorted(where)) for inv_id, where in found.items()}


def _sort_key(inv_id: str) -> int:
    return int(inv_id.removeprefix("INV-"))


def render_table(
    safety: Iterable[str],
    markers: dict[str, tuple[str, ...]],
) -> str:
    """Render the INV → tests matrix as a markdown table."""
    safety_set = set(safety)
    rows = sorted(safety_set | set(markers), key=_sort_key)
    lines = ["| INV | safety | tests |", "|---|---|---|"]
    for inv_id in rows:
        tests = markers.get(inv_id, ())
        cell = "<br>".join(f"`{t}`" for t in tests) if tests else "**none**"
        lines.append(f"| {inv_id} | {'yes' if inv_id in safety_set else ''} | {cell} |")
    covered = sum(1 for inv_id in safety_set if inv_id in markers)
    lines.append("")
    lines.append(f"{covered}/{len(safety_set)} safety invariants (HLD §7.5) have a marked test.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the matrix."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hld", type=Path, default=HLD, help="path to design/HLD.md")
    parser.add_argument("--tests", type=Path, default=TESTS, help="path to tests/")
    args = parser.parse_args(argv)

    print(render_table(parse_safety_invs(args.hld), scan_inv_markers(args.tests)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
