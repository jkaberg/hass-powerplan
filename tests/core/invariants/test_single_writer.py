"""INV-3: one writer, one reader (D9 §5.7).

Only `writegate.py` performs **device writes**. `hass.services.async_call` is
allowed there, in `notifications.py` for notify and persistent_notification, and
in the two price providers that read a market through a *response action* -
`providers/prices/nordpool_action.py` for the core Nord Pool integration's
`get_prices_for_date`, and `providers/prices/action.py` for the format table's
action-backed rows (Tibber, EnergyZero, easyEnergy). Every one of those actions is
registered `SupportsResponse.ONLY`: it reads prices and writes nothing. Those two
files are held to the stricter rule the second test below enforces: every call
site in them passes `return_response=True`, so the exemption cannot quietly
become a write (`design/DECISIONS.md` D-0080, D-0101).

`hass.states.get` and `hass.states.async_all` are allowed in `runtime.py` and
under `providers/`. Anywhere else bypasses the write gate's rate limits, dwell
clocks and idempotency at once (INV-20) or reads state behind the runtime's back.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]
INTEGRATION = REPO_ROOT / "custom_components" / "powerplan"

#: The price providers allowed to invoke a read-only response action (D-0080):
#: Nord Pool's own source, and `action.py`, the one call site the format table's
#: action-backed rows reach (`tibber_action`, `energyzero_action` - D-0101).
READ_ONLY_ACTION_CALLERS = (
    "providers/prices/nordpool_action.py",
    "providers/prices/action.py",
)

# (what we grep for, which relative paths or path prefixes may contain it)
RULES: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "service_call",
        r"hass\.services\.async_call",
        ("writegate.py", "notifications.py", *READ_ONLY_ACTION_CALLERS),
    ),
    ("state_get", r"hass\.states\.get\b", ("runtime.py", "providers/")),
    ("state_all", r"hass\.states\.async_all\b", ("runtime.py", "providers/")),
]


def _integration_modules() -> list[Path]:
    return sorted(INTEGRATION.rglob("*.py"))


def _allowed(rel: str, allowed: tuple[str, ...]) -> bool:
    return any(rel == a or rel.startswith(a) for a in allowed)


def _action_calls(tree: ast.Module) -> Iterator[ast.Call]:
    """Yield every `<something>.services.async_call(...)` in the module."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "async_call":
            continue
        owner = node.func.value
        if isinstance(owner, ast.Attribute) and owner.attr == "services":
            yield node


def _passes_return_response(call: ast.Call) -> bool:
    """Return whether the call is explicitly `return_response=True`."""
    return any(
        keyword.arg == "return_response"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in call.keywords
    )


def test_integration_tree_is_not_empty() -> None:
    """Guard the guard: a grep over nothing proves nothing."""
    assert _integration_modules(), f"no modules found under {INTEGRATION}"


@pytest.mark.inv("INV-3")
@pytest.mark.parametrize(
    ("pattern", "allowed"),
    [(pattern, allowed) for _, pattern, allowed in RULES],
    ids=[name for name, _, _ in RULES],
)
def test_call_appears_only_where_it_is_allowed(pattern: str, allowed: tuple[str, ...]) -> None:
    """Each HA access pattern appears only in the module that owns it."""
    compiled = re.compile(pattern)
    offenders: list[str] = []
    for module in _integration_modules():
        rel = module.relative_to(INTEGRATION).as_posix()
        if _allowed(rel, allowed):
            continue
        if compiled.search(module.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert not offenders, f"{pattern} found in {offenders}; allowed only in {list(allowed)} (INV-3)"


@pytest.mark.inv("INV-3")
@pytest.mark.parametrize("caller", READ_ONLY_ACTION_CALLERS)
def test_the_price_provider_only_calls_read_only_actions(caller: str) -> None:
    """Every action call outside the gate is read-only (INV-3, D-0080, D-0101).

    `providers/prices/nordpool_action.py` and `providers/prices/action.py` are on
    the allowlist because every action they call - Nord Pool's
    `get_prices_for_date`, `tibber.get_prices`, EnergyZero's and easyEnergy's
    price services - is registered `SupportsResponse.ONLY`: they read prices and
    write nothing. The exemption holds only while that stays true, so every call
    site in each file must pass `return_response=True`. A call without it is a
    call that could actuate something, and it belongs behind the write gate
    (INV-20, INV-24).
    """
    module = INTEGRATION / caller
    assert module.is_file(), f"{caller} is on the INV-3 allowlist but is missing"

    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    calls = list(_action_calls(tree))

    assert calls, (
        f"{caller} is exempted from the single-writer rule but calls "
        "no action at all; take it off the allowlist"
    )
    offenders = [call.lineno for call in calls if not _passes_return_response(call)]
    assert not offenders, (
        f"{caller} lines {offenders} call an action without "
        "return_response=True; only a read-only response action is exempt from INV-3"
    )
