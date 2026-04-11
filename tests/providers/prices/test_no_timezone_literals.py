"""No timezone is a literal in the integration (`design/DECISIONS.md` D-0100).

Two different zones get confused the moment either is hardcoded. The **site's**
zone is Home Assistant's - `hass.config.time_zone`, set once by the site flow -
and a house that moves, or an installation that was set up before its owner fixed
their HA zone, must not find "Europe/Oslo" compiled into a planner. A **market's**
zone is a fact about an auction, the same for every house that buys on it, and it
belongs in a table next to the areas it applies to (`providers/prices/markets.py`)
where a user can see it and override it under Advanced (D1 §6).

So: a zone name may appear in the market table, and nowhere else under
`custom_components/powerplan/`. `ZoneInfo(...)` of a *value* is fine - that is
the site's own zone or a market's, arriving from configuration.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INTEGRATION = REPO_ROOT / "custom_components" / "powerplan"

#: The one module allowed to name a zone: D1 §5.1's market clocks, as data.
MARKET_DATA_TABLES = ("providers/prices/markets.py",)

#: An IANA zone name in a string literal - `"Europe/Oslo"`, `ZoneInfo("UTC")`'s
#: continental siblings, a `tz=` default someone typed in a hurry.
ZONE_LITERAL = re.compile(
    r"""["'](Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific|Etc|US)/[\w+\-]+["']"""
)


def test_the_integration_tree_is_not_empty() -> None:
    """Guard the guard: a grep over nothing proves nothing."""
    assert sorted(INTEGRATION.rglob("*.py"))


def test_only_the_market_table_names_a_timezone() -> None:
    """A zone literal outside the market table is a site zone in disguise."""
    offenders: list[str] = []
    for module in sorted(INTEGRATION.rglob("*.py")):
        rel = module.relative_to(INTEGRATION).as_posix()
        if rel in MARKET_DATA_TABLES:
            continue
        for number, line in enumerate(module.read_text(encoding="utf-8").splitlines(), start=1):
            if ZONE_LITERAL.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")

    assert not offenders, (
        "a timezone is written down outside "
        f"{list(MARKET_DATA_TABLES)}: {offenders}. The site's zone comes from "
        "hass.config.time_zone; a market's belongs in the market table (D-0100)."
    )


def test_the_market_table_really_holds_the_zones() -> None:
    """The allowlist is not an empty exemption: the table is where they live."""
    table = (INTEGRATION / MARKET_DATA_TABLES[0]).read_text(encoding="utf-8")

    assert ZONE_LITERAL.search(table), f"{MARKET_DATA_TABLES[0]} names no market zone at all"
