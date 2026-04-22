"""INV-68: accounting is observation only (D11 §9 16, HLD §6.11).

An AST walk, not a grep: an import inside a function or a `TYPE_CHECKING` block is
still an import, and a docstring that mentions savings is not.

The rule is a one-way edge. Nothing under `core/strategies`, `core/allocation`,
`core/loads` or in `writegate.py` may import `core.accounting`, read a ledger or
see a savings figure - because a controller that could read the number it is
measured on has an incentive loop, and the cheapest way to raise a savings figure
is to make the counterfactual worse.

The other half of §9 16 - `close_slot` is called from `Engine.plan` and never from
`Engine.tick` - lands with WP0.8, which is where an engine exists to assert it of.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = REPO_ROOT / "custom_components" / "powerplan" / "core"
INTEGRATION = REPO_ROOT / "custom_components" / "powerplan"

#: The four places HLD §6.11 names, by path relative to the integration.
FORBIDDEN_IN = ("core/strategies", "core/allocation", "core/loads", "writegate.py")

#: What none of them may name.
ACCOUNTING = "accounting"


def _downstream_modules() -> list[Path]:
    """Return every module the invariant covers that exists today."""
    found: list[Path] = []
    for entry in FORBIDDEN_IN:
        target = INTEGRATION / entry
        if target.is_dir():
            found.extend(sorted(target.rglob("*.py")))
        elif target.is_file():
            found.append(target)
    return found


MODULES = _downstream_modules()


def _ids(paths: list[Path]) -> list[str]:
    return [path.relative_to(INTEGRATION).as_posix() for path in paths]


def _imports_accounting(tree: ast.Module) -> set[str]:
    """Return the offending import statements, as source-ish strings.

    Both spellings are caught: `from..accounting import x` (a relative import
    whose module part names it) and `import custom_components.powerplan.core
    .accounting`.
    """
    offenders: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.update(
                alias.name for alias in node.names if ACCOUNTING in alias.name.split(".")
            )
        elif isinstance(node, ast.ImportFrom):
            parts = (node.module or "").split(".")
            if ACCOUNTING in parts:
                offenders.add(f"from {'.' * node.level}{node.module or ''}")
            elif node.module is None or not parts[-1]:
                offenders.update(
                    f"from {'.' * node.level} import {alias.name}"
                    for alias in node.names
                    if alias.name == ACCOUNTING
                )
            else:
                offenders.update(
                    f"from {'.' * node.level}{node.module} import {alias.name}"
                    for alias in node.names
                    if alias.name == ACCOUNTING
                )
    return offenders


def test_the_covered_tree_is_not_empty() -> None:
    """Guard the guard: an isolation test over nothing proves nothing."""
    assert MODULES, f"no modules found under {FORBIDDEN_IN} in {INTEGRATION}"
    assert (CORE / "accounting" / "close.py").is_file(), "there is something to isolate"


@pytest.mark.inv("INV-68")
@pytest.mark.parametrize("module", MODULES, ids=_ids(MODULES))
def test_no_decision_module_imports_the_accounting(module: Path) -> None:
    """Nothing that decides may import `core.accounting` (INV-68)."""
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    offenders = _imports_accounting(tree)
    assert not offenders, (
        f"{module.relative_to(REPO_ROOT)} imports core.accounting ({sorted(offenders)}). "
        "Accounting is observation only: a savings figure a decision can read is an "
        "incentive loop (INV-68). Take the number out through D7's Snapshot instead."
    )


@pytest.mark.inv("INV-68")
def test_the_walk_would_catch_an_import_in_any_of_its_spellings() -> None:
    """Guard the guard again: each spelling the walk claims to see, it sees."""
    spellings = (
        "from ..accounting import Accounting",
        "from ...accounting.ledger import Ledger",
        "import custom_components.powerplan.core.accounting",
        "from custom_components.powerplan.core import accounting",
        "from . import accounting",
        "def f():\n    from ..accounting import close_slot",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from ..accounting import Ledger",
    )
    for source in spellings:
        assert _imports_accounting(ast.parse(source)), source

    for innocent in (
        "from ..metering import LoadSlot",
        '"""A docstring about accounting, savings and the ledger."""',
        "accounting = 1",
    ):
        assert not _imports_accounting(ast.parse(innocent)), innocent


@pytest.mark.inv("INV-68")
def test_the_accounting_never_imports_a_decision_module() -> None:
    """The edge is one-way by design, and the reverse would make a cycle (D11 §3)."""
    forbidden = {"strategies", "allocation"}
    for module in sorted((CORE / "accounting").rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                named = set((node.module or "").split(".")) | {alias.name for alias in node.names}
                assert not named & forbidden, f"{module.name} imports {named & forbidden}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not set(alias.name.split(".")) & forbidden, alias.name
