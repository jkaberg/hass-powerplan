"""The format registry is D1 §2's table, and cannot drift from it (D1 §9 item 1).

The table in the LLD is the specification: one row per price integration
powerplan can read. This module parses that table out of `design/lld/D1-pricing.md`
and asserts three things about every row - an adapter is registered under its
key, the registered adapter claims the platform the table names, and a fixture
exists for it - plus the converse, that nothing is registered the table does not
mention.

That is deliberately a test against a document. The failure it is here to catch
is the one the format table exists to prevent: a row someone added to the design
and never implemented, an adapter whose `platform` no longer matches what the
config flow will detect (D1 §6), or an adapter that parses nothing but its own
author's imagination because no payload was ever written down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.powerplan.providers.prices import formats

REPO_ROOT = Path(__file__).resolve().parents[4]
LLD = REPO_ROOT / "design" / "lld" / "D1-pricing.md"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "formats"

#: The header row of D1 §2's "Entity source format table".
TABLE_HEADER = "| key | platform | where prices live | resolution |"

BACKTICKED = re.compile(r"`([^`]+)`")


def table_rows() -> list[tuple[str, str | None]]:
    """Return `(format key, platform)` for every row of D1 §2's format table.

    A row may name two keys and two platforms (`energyzero_action` /
    `easyenergy_action`), and a row that fits any entity says "any" where a
    platform would be - that is the registry's `platform = None`.
    """
    lines = LLD.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == TABLE_HEADER)

    out: list[tuple[str, str | None]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        keys = BACKTICKED.findall(cells[0])
        platforms: list[str | None] = list(BACKTICKED.findall(cells[1]))
        if not platforms and "any" in cells[1]:
            platforms = [None]
        if len(platforms) == 1:
            platforms = platforms * len(keys)
        assert len(keys) == len(platforms), f"cannot pair keys with platforms in {line!r}"
        out.extend(zip(keys, platforms, strict=True))
    return out


TABLE = table_rows()


def test_the_table_was_found_and_is_the_whole_table() -> None:
    """Guard the guard: a parse that found nothing would prove nothing."""
    assert len(TABLE) >= 13, TABLE
    assert ("nordpool_hacs", "nordpool") in TABLE
    assert ("generic_list", None) in TABLE


@pytest.mark.parametrize(("key", "platform"), TABLE, ids=[key for key, _ in TABLE])
def test_every_row_of_the_table_has_a_registered_adapter(key: str, platform: str | None) -> None:
    """Extension is by registry: a row of D1 §2 is one registered module (D1 §6)."""
    assert key in set(formats.keys()), f"D1 §2 names {key} and no adapter is registered for it"
    assert formats.entry(key).platform == platform
    if platform is not None:
        assert key in formats.for_platform(platform)


@pytest.mark.parametrize("key", [key for key, _ in TABLE])
def test_every_row_of_the_table_has_a_fixture(key: str) -> None:
    """A row nobody wrote a payload for is a row nobody has parsed (D-0081)."""
    assert sorted(FIXTURES.glob(f"{key}*.json")), (
        f"D1 §2 names {key} and tests/fixtures/formats/ holds no payload for it"
    )


def test_nothing_is_registered_that_the_table_does_not_name() -> None:
    """An adapter the design does not mention is a row missing from D1 §2."""
    assert set(formats.keys()) == {key for key, _ in TABLE}
