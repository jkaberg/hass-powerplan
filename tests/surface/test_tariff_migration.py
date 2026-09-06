"""D13 §19 6 - the tariff copy by party, migrated at start, offline (§10, INV-73).

An entry on `no/tensio` (retired, no copy) or on a WP4.6 copy of `no/tensio-ts`
starts on its copy with no network - sockets are closed in this harness, so any
fetch would fail the setup. A `vat` or `levy` add-on equal to Norway's module is
dropped; one that differs is kept as the household's override and raises
`tariff_review`. The reference house's own entry composes to the same price
before and after, to the øre.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import repairs
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.model import Confidence, Slot
from custom_components.powerplan.core.pricing import modifiers, party
from custom_components.powerplan.core.tariffs import household
from custom_components.powerplan.core.tariffs.rules import loader
from custom_components.powerplan.storage import ENTRY_MINOR_PRICE, migrate_tariff
from tests.builders.curves import OSLO, context, no3_shape
from tests.runtime.conftest import site_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime
    from tests.runtime.conftest import FakeMeter

VAT = {"key": "vat", "component": "vat", "options": {"rate": "0.25"}, "source": "user"}


def _levy(amount: str) -> dict[str, Any]:
    return {"key": "levy", "component": "levy", "options": {"amount": amount}, "source": "user"}


def _issue(hass: HomeAssistant, entry_id: str, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, repairs.registry_id(entry_id, issue_id))


async def _start(hass: HomeAssistant, entry_id: str, data: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Old site", entry_id=entry_id, data=data, version=1, minor_version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.inv("INV-73")
async def test_06_an_entry_on_no_tensio_starts_on_its_copy_offline(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """The reference house's first file: retired, no copy - its successor, as a copy."""
    data = site_data(hass)
    data["tariff"].update(preset_file="no/tensio", version_ids=["no.tensio.household@2026-01-01"])
    data["prices"]["modifiers"] = [VAT, _levy("0.0813")]
    entry = await _start(hass, "TENSIO", data)

    assert entry.minor_version == ENTRY_MINOR_PRICE
    tariff = entry.data["tariff"]
    assert "spec" not in tariff
    price = household.from_json(tariff["price"])
    assert price.grid.capacity_id == "no.tensio-ts.household"
    assert price.grid.provenance.source == "shipped"
    assert price.grid.basis == household.Basis(
        vat=True, levies=frozenset({"forbruksavgift", "enova"})
    )
    # Both add-ons equal Norway's module today: dropped, the module applies them.
    assert entry.data["prices"]["modifiers"] == []
    assert price.state.overrides == {}
    assert tariff["review"] == []

    runtime: Runtime = entry.runtime_data
    assert runtime.build.tariff.active_version().version_id.startswith("no.tensio-ts.household@")
    assert _issue(hass, entry.entry_id, "preset_outdated") is not None
    assert _issue(hass, entry.entry_id, "tariff_review") is None
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.inv("INV-73")
async def test_06_a_vat_that_differs_is_kept_and_raises_tariff_review(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """A WP4.6 copy with the household's own 0 % VAT (a Nord-Norge house): kept, reviewed."""
    data = site_data(hass)
    data["tariff"]["spec"] = loader.load_raw("no/tensio-ts")
    data["prices"]["modifiers"] = [
        {**VAT, "options": {"rate": "0"}},
        {
            "key": "tou_schedule",
            "component": "grid_energy",
            "options": {"periods": [{"price": 0.3779, "hours": [[360, 1320]]}]},
            "source": "no.tensio-ts.household",
        },
    ]
    entry = await _start(hass, "NORD", data)

    price = household.from_json(entry.data["tariff"]["price"])
    assert price.state.overrides == {"vat": Decimal(0)}
    assert entry.data["tariff"]["review"] == ["vat"]
    # The preset's own energy charge left the add-ons for the copy (§10).
    assert entry.data["prices"]["modifiers"] == []
    assert price.grid.energy, "Tensio's day/night charge is in the copy"
    assert _issue(hass, entry.entry_id, "tariff_review") is not None
    assert _issue(hass, entry.entry_id, "preset_outdated") is None
    await hass.config_entries.async_unload(entry.entry_id)


def _old_house() -> dict[str, Any]:
    """Return the reference house's entry before the price-by-party migration, as it is stored."""
    copy = loader.load_raw("no/tensio-ts")
    return {
        "currency": "NOK",
        "electrical": {"country": "NO"},
        "prices": {
            "sources": [{"key": "nordpool_action", "options": {"area": "NO3"}}],
            "modifiers": [
                {
                    "key": "fixed_price",
                    "component": "spot",
                    "options": {"cap_kwh_per_month": 5000.0, "price": "0.4"},
                    "source": "user",
                },
                VAT,
                {
                    "key": "tou_schedule",
                    "component": "grid_energy",
                    "options": copy["versions"][-1]["energy_components"]["tou_schedule"],
                    "source": "no.tensio-ts.household",
                },
            ],
        },
        "tariff": {
            "preset_file": "no/tensio-ts",
            "version_ids": [f"no.tensio-ts.household@{v['valid_from']}" for v in copy["versions"]],
            "spec": copy,
        },
    }


@pytest.mark.inv("INV-72")
def test_06_the_reference_house_pays_the_same_before_and_after() -> None:
    """Norgespris, a 25 % VAT add-on and Tensio's incl.-VAT charge: 0.8779 by day, as before."""
    old = _old_house()
    before = modifiers.chain_from(
        [(row["key"], row["options"]) for row in old["prices"]["modifiers"]]
    )
    migrated, review = migrate_tariff(old, date(2026, 9, 24))
    assert review == []
    assert [row["key"] for row in migrated["prices"]["modifiers"]] == ["fixed_price"]
    price = household.from_json(migrated["tariff"]["price"])
    added = modifiers.chain_from(
        [(row["key"], row["options"]) for row in migrated["prices"]["modifiers"]]
    )
    after, _ = party.chain(price, added, frozenset({"spot"}))

    start = datetime(2026, 9, 24, tzinfo=OSLO).astimezone(UTC)
    for hour in range(48):
        when = start + timedelta(hours=hour)
        spot = no3_shape(when.astimezone(OSLO))
        totals = []
        for chain in (before, after):
            slot = Slot(
                start=when,
                end=when + timedelta(hours=1),
                total=spot,
                components={"spot": spot},
                confidence=Confidence.KNOWN,
            )
            ctx = context(when)
            for modifier in chain:
                slot = modifier.apply(slot, ctx)
            totals.append(slot.total)
        assert totals[0] == totals[1], when
    assert totals[0] in (Decimal("0.8779"), Decimal("0.7379")), "0.50 + Tensio's day or night"
