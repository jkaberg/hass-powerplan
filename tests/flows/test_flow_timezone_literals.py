"""No IANA zone is ever a literal in the flow (D-0120).

The site timezone is derived from the environment - `hass.config.time_zone` -
and materialised into `entry.data` at creation; the flow asks only when Home
Assistant has no usable zone. A literal `Europe/Oslo` anywhere in the flow is a
Norwegian assumption that will be wrong in the second house, and a market's
publication zone is data on the price source, not on the site.

See `design/DECISIONS.md` D-0120.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "powerplan"

#: `Area/Location`, the shape of every IANA key, over the real tzdata areas.
IANA = re.compile(
    r"\b(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Brazil|Canada|Chile|"
    r"Etc|Europe|Indian|Mexico|Pacific|US)/[A-Za-z_]+"
)


def _flow_modules() -> list[Path]:
    return [INTEGRATION / "config_flow.py", *sorted((INTEGRATION / "flow").rglob("*.py"))]


def test_the_flow_modules_exist() -> None:
    """Guard the guard: a grep over nothing proves nothing."""
    modules = _flow_modules()
    assert len(modules) > 1, f"expected config_flow.py and a flow/ package under {INTEGRATION}"
    assert all(module.is_file() for module in modules)


@pytest.mark.parametrize("module", _flow_modules(), ids=lambda path: path.name)
def test_no_iana_zone_is_hard_coded(module: Path) -> None:
    """Code, comments and docstrings alike: the zone comes from the environment."""
    found = IANA.findall(module.read_text(encoding="utf-8"))
    assert not found, f"{module.name} hard-codes the timezone(s) {sorted(set(found))}"
