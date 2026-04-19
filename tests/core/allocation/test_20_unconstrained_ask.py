"""D6 §9 20 - the unconstrained ask, and the one import D6 may never make (INV-68).

`unconstrained_ask_w` is a **diagnostic**: the sum of what the loads that wanted
power would have taken with nothing in the way, so a dashboard can say "held back
3.4 kW this tick". It is not the accounting counterfactual - that word belongs to
D11, which keeps its own per-window figure from its own shadow stores.

The second half is the import rule. `core/accounting/` is **observation only**:
nothing in `core/strategies`, `core/allocation`, `core/loads` or `writegate.py` may
import it, and it runs in the planning loop, never in the tick. The package does
not exist yet - the AST walk below is what keeps it that way when it does.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    allocate,
    unconstrained_ask_w,
)
from tests.core.allocation.conftest import (
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    demand,
    ev_view,
    loop_view,
    tank_view,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ALLOCATION = REPO_ROOT / "custom_components" / "powerplan" / "core" / "allocation"
ALLOCATION_MODULES = sorted(ALLOCATION.rglob("*.py"))


def _imported(tree: ast.Module) -> set[str]:
    """Return every module name imported anywhere in the tree, relative ones included."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            names.add(f"{prefix}{node.module or ''}")
            names.update(f"{prefix}{node.module or ''}.{alias.name}" for alias in node.names)
    return names


def test_20_the_allocation_tree_is_not_empty() -> None:
    """Guard the guard: an import test over nothing proves nothing."""
    assert ALLOCATION_MODULES, f"no modules found under {ALLOCATION}"


@pytest.mark.inv("INV-68")
@pytest.mark.parametrize("module", ALLOCATION_MODULES, ids=[p.name for p in ALLOCATION_MODULES])
def test_20_nothing_in_allocation_imports_accounting(module: Path) -> None:
    """D6 decides; D11 observes. The tick never touches the ledger (INV-68)."""
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

    offenders = sorted(
        name
        for name in _imported(tree)
        if "accounting" in name.replace("..", ".").split(".") or name.endswith("accounting")
    )

    assert not offenders, (
        f"{module.relative_to(REPO_ROOT)} imports {offenders}; core/accounting/ is "
        "observation only and runs in the planning loop, never in the tick (INV-68)."
    )


def test_20_the_ask_is_the_sum_of_what_the_willing_loads_wanted() -> None:
    """Three loads want 7 360 + 3 000 + 960 W; the satisfied one asks for nothing."""
    loads = [
        ev_view(),
        tank_view(),
        loop_view(),
        loop_view(load_id="loop_warm", demand=demand(wants=False, max_w=960.0)),
    ]

    assert unconstrained_ask_w(loads) == pytest.approx(32.0 * W_PER_AMP + 3000.0 + 960.0)


def test_20_the_ask_is_reported_on_every_tick_whatever_was_granted() -> None:
    """Held back to 500 W, the report still says what the house asked for."""
    loads = [ev_view(), tank_view(), loop_view()]
    ctx = alloc_ctx(loads, budget=budget_of(500.0))

    _grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert report.unconstrained_ask_w == pytest.approx(unconstrained_ask_w(loads))
    assert report.unconstrained_ask_w > report.p_free_w


def test_20_a_negative_demand_is_not_an_ask() -> None:
    """A battery offering discharge is not asking for power (power is signed)."""
    battery = ev_view(
        load_id="battery",
        demand=demand(wants=True, min_w=-5000.0, max_w=-5000.0, reason="discharging"),
    )

    assert unconstrained_ask_w([battery]) == 0.0
