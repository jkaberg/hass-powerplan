"""D14 §9 5: no generated block is stale.

`tools/docs.py --check` covers the blocks a registry or a translation file
fills. The entity tables come from the entity registry of the reference house,
added through the flows as D8 §9 18c adds it, so this test is their check -
and, with `POWERPLAN_DOCS_WRITE=1` (what `tools/docs.py --write` sets), their
writer (D-0540).
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from typing import TYPE_CHECKING

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from custom_components.powerplan.const import DOMAIN, SUBENTRY_LOAD
from tests.docs.pages import DOCS, REPO_ROOT, is_pending
from tests.flows.test_text import HOUSE_LOADS, _subentry

sys.path.insert(0, str(REPO_ROOT / "tools"))
import docs

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.e2e.fake_house import FakeHouse

ENTITIES = DOCS / "entities.md"
CURRENCY = re.compile(r"\b[A-Z]{3}\b")
WRITE = os.environ.get("POWERPLAN_DOCS_WRITE") == "1"


def test_05_no_generated_block_is_stale() -> None:
    """`tools/docs.py --check`: every registry-fed block equals what its source renders."""
    found = [
        f"{path.relative_to(REPO_ROOT)} {block_id}"
        for path in docs.pages()
        for block_id in docs.stale(path)
    ]
    assert not found, f"stale blocks {found}: run `uv run python tools/docs.py --write`"


@pytest.mark.parametrize(
    "block_id",
    [
        "requirements",
        "types",
        "strategies",
        "profiles",
        "formats",
        "modifiers",
        "repairs",
        "actions",
        "events",
        "dashboard_options",
        "fields:config.tariff",
        "fields:circuit.user",
    ],
)
def test_05_every_block_renders_a_table(block_id: str) -> None:
    """Every block of §5.6 whose registry exists renders, the pages that hold it or not."""
    body = docs.render(block_id)
    assert body.startswith("| "), body
    assert body.count("\n") >= 3, body


def _where(entry: er.RegistryEntry) -> str:
    """Return the category and the default in one cell: shown, Configuration, Diagnostic, off."""
    parts = []
    if entry.entity_category is EntityCategory.CONFIG:
        parts.append("Configuration")
    elif entry.entity_category is EntityCategory.DIAGNOSTIC:
        parts.append("Diagnostic")
    if entry.disabled_by is not None:
        parts.append("off by default")
    return ", ".join(parts) or "shown"


def _unit(entry: er.RegistryEntry) -> str:
    """Return the unit, a currency (ISO 4217) named as the household's own."""
    return (
        CURRENCY.sub(
            "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}currency\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}",
            entry.unit_of_measurement or "",
        )
        or "–"
    )


def _pattern(entity_id: str, device_name: str, owner: str) -> str:
    domain, _, object_id = entity_id.partition(".")
    prefix = slugify(device_name)
    rest = object_id.removeprefix(prefix)
    return f"`{domain}.<{owner}>{rest}`" if rest != object_id else f"`{entity_id}`"


def _tables(hass: HomeAssistant, site: MockConfigEntry, types: dict[str, str]) -> dict[str, str]:
    """Render `entities:home` and `entities:appliance` from the registry."""
    names = docs.strings()["entity"]
    devices = dr.async_get(hass)
    home: dict[str, tuple[str, ...]] = {}
    appliance: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for entry in er.async_get(hass).entities.values():
        if entry.platform != DOMAIN or entry.translation_key is None:
            continue
        device = devices.async_get(entry.device_id) if entry.device_id else None
        device_name = (device.name_by_user or device.name) if device else ""
        name = names[entry.domain][entry.translation_key]["name"]
        subentry = entry.config_subentry_id
        if subentry is None:
            row = (
                _pattern(entry.entity_id, device_name or "", "home"),
                name,
                _unit(entry),
                _where(entry),
            )
            home[row[0]] = row
            continue
        row = (
            _pattern(entry.entity_id, device_name or "", "appliance"),
            name,
            _unit(entry),
            _where(entry),
        )
        appliance[row].add(types[subentry])
    by_type = docs.strings()["selector"]["load_type"]["options"]
    house = set(types.values())
    return {
        "entities:home": docs._table(
            ("Entity", "Name", "Unit", "Where"), [home[k] for k in sorted(home)]
        ),
        "entities:appliance": docs._table(
            ("Entity", "Name", "Unit", "Where", "Appliances"),
            [
                (*row, "all" if found == house else ", ".join(sorted(by_type[t] for t in found)))
                for row, found in sorted(appliance.items())
            ],
        ),
    }


@pytest.mark.skipif(is_pending("entities.md"), reason="entities.md is pending")
async def test_05_house_entity_tables_match_the_registry(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The reference house's every appliance, through the flow; its registry is the table."""
    hass.config.language = "en"
    types: dict[str, str] = {}
    for load_id in HOUSE_LOADS:
        kind = charger.loads[load_id].kind
        result = await _subentry(
            hass,
            site.entry_id,
            SUBENTRY_LOAD,
            language="en",
            answers={
                "user": {"type": kind},
                "device": {"device": charger.loads[load_id].device_id},
                "review": {"name": load_id},
            },
        )
        assert result["type"] == "create_entry", result
        await hass.async_block_till_done()
        subentry_id = next(s.subentry_id for s in site.subentries.values() if s.title == load_id)
        types[subentry_id] = kind

    tables = _tables(hass, site, types)
    text = ENTITIES.read_text("utf-8")
    if WRITE:
        for block_id, body in tables.items():
            text = docs.replace(text, block_id, body)
        ENTITIES.write_text(text, "utf-8")
    found = {m["id"]: m["body"] for m in docs.blocks(text)}
    for block_id, body in tables.items():
        assert found.get(block_id) == body, (
            f"entities.md {block_id} is stale: run `uv run python tools/docs.py --write`"
        )
