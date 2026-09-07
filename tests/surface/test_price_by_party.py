"""D12 §9 21, 22 and D13 §7 on the entities: the price by party, the credit, the wait's party.

`sensor.<site>_price_forecast` carries each slot's split by party, which sums to
its total; its `credit` names the copy's source and is empty for a copy no
source fetched (a template, a custom tariff, a WP4.6 copy).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.sensor import tariff_credit
from tests.runtime.conftest import SITE_ENTRY_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime


def _forecast(hass: HomeAssistant) -> dict[str, object]:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "price_forecast")
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return dict(state.attributes)


def test_21_every_slot_is_split_by_party_and_the_parts_sum_to_its_total(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """The test site: a fixed 0.50 contract, Tensio's copy, Norway's taxes."""
    slots = _forecast(hass)["slots"]
    assert isinstance(slots, list)
    assert slots
    for slot in slots:
        parties = slot["parties"]
        assert set(parties) == {"supplier", "grid", "state"}
        assert sum(Decimal(value) for value in parties.values()) == Decimal(slot["total"])


def test_22_a_copy_no_source_fetched_credits_nobody(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """The test site runs on a migrated WP4.6 copy: no credit line (D12 §9 22)."""
    assert _forecast(hass)["credit"] == []
    assert tariff_credit(runtime) == []
    assert _forecast(hass)["vat"] == 0.25
