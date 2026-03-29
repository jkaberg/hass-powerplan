"""INV-2: `core/` never imports Home Assistant (D9 §5.7).

An AST walk, not a grep: an import inside a function or a `TYPE_CHECKING`
block is still an import, and a docstring that mentions Home Assistant is not.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = REPO_ROOT / "custom_components" / "powerplan" / "core"
CORE_MODULES = sorted(CORE.rglob("*.py"))


def _ids(paths: list[Path]) -> list[str]:
    return [p.relative_to(CORE).as_posix() for p in paths]


def _imported_roots(tree: ast.Module) -> set[str]:
    """Return the top-level package of every import in the module."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_core_tree_is_not_empty() -> None:
    """Guard the guard: a purity test over nothing proves nothing."""
    assert CORE_MODULES, f"no modules found under {CORE}"


@pytest.mark.inv("INV-2")
@pytest.mark.parametrize("module", CORE_MODULES, ids=_ids(CORE_MODULES))
def test_core_module_does_not_import_homeassistant(module: Path) -> None:
    """No module under `core/` imports `homeassistant` (HLD §5, INV-2)."""
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    offenders = {root for root in _imported_roots(tree) if root == "homeassistant"}
    assert not offenders, (
        f"{module.relative_to(REPO_ROOT)} imports homeassistant; "
        "core/ is a pure library (INV-2). Take the value in through `Inputs` "
        "and give the write back as an `Effect`."
    )
