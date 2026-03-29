"""INV-3: one writer, one reader (D9 §5.7).

`hass.services.async_call` is allowed in `writegate.py` and, for notify and
persistent_notification only, in `notifications.py`. `hass.states.get` and
`hass.states.async_all` are allowed in `runtime.py` and under `providers/`.
Anywhere else bypasses the write gate's rate limits, dwell clocks and
idempotency at once (INV-20) or reads state behind the runtime's back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INTEGRATION = REPO_ROOT / "custom_components" / "powerplan"

# (what we grep for, which relative paths or path prefixes may contain it)
RULES: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "service_call",
        r"hass\.services\.async_call",
        ("writegate.py", "notifications.py"),
    ),
    ("state_get", r"hass\.states\.get\b", ("runtime.py", "providers/")),
    ("state_all", r"hass\.states\.async_all\b", ("runtime.py", "providers/")),
]


def _integration_modules() -> list[Path]:
    return sorted(INTEGRATION.rglob("*.py"))


def _allowed(rel: str, allowed: tuple[str, ...]) -> bool:
    return any(rel == a or rel.startswith(a) for a in allowed)


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
