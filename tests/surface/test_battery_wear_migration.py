"""D4 §9 67 - config entry minor version 9: a battery carries `wear_cost` (D-0769).

`threshold` is gone. A stored battery with it still at the default 0.05 gets the chemistry's
wear cost in the site's currency; one the household typed keeps its number, as `wear_cost`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import async_migrate_entry
from custom_components.powerplan.const import DOMAIN, SUBENTRY_LOAD
from custom_components.powerplan.storage import (
    ENTRY_MINOR,
    ENTRY_MINOR_BATTERY_WEAR,
    migrate_battery_wear,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _battery(chemistry: str = "lfp", **strategy_params: Any) -> dict[str, Any]:
    return {
        "type": "battery",
        "strategy": "peak_shave",
        "strategy_params": {"reserve_soc": 20.0, "max_soc": 100.0, **strategy_params},
        "answers": {"chemistry": chemistry},
        "params": {"chemistry": chemistry},
    }


@pytest.mark.inv("INV-66")
def test_67_a_default_threshold_is_replaced_by_the_chemistrys_wear_in_the_currency() -> None:
    """0.05 untyped: LFP is 0.46 NOK, NMC 1.15 NOK, and `threshold` is gone."""
    lfp = migrate_battery_wear(_battery(threshold=0.05), "NOK")
    nmc = migrate_battery_wear(_battery("nmc", threshold=0.05), "NOK")

    assert lfp is not None
    assert lfp["strategy_params"]["wear_cost"] == pytest.approx(0.46)
    assert "threshold" not in lfp["strategy_params"]
    assert lfp["strategy_params"]["reserve_soc"] == 20.0
    assert nmc is not None
    assert nmc["strategy_params"]["wear_cost"] == pytest.approx(1.15)


def test_67_a_battery_that_never_stored_a_threshold_gets_the_chemistrys_wear() -> None:
    """The usual battery has no `threshold` at all: the default stood in for it."""
    migrated = migrate_battery_wear(_battery(), "EUR")

    assert migrated is not None
    assert migrated["strategy_params"]["wear_cost"] == 0.04


def test_67_a_typed_threshold_is_kept_as_wear_cost() -> None:
    """The household chose 0.12: it is theirs, in their currency already."""
    migrated = migrate_battery_wear(_battery(threshold=0.12), "NOK")

    assert migrated is not None
    assert migrated["strategy_params"]["wear_cost"] == 0.12
    assert "threshold" not in migrated["strategy_params"]


def test_67_a_battery_with_wear_cost_and_other_types_are_left() -> None:
    """Idempotent, and nothing but a battery is touched."""
    assert migrate_battery_wear(_battery(wear_cost=0.5), "NOK") is None
    assert migrate_battery_wear({"type": "ev", "strategy": "deadline_fill"}, "NOK") is None


async def test_67_the_entry_migrates_in_the_site_s_currency(hass: HomeAssistant) -> None:
    """From minor 8, through `async_migrate_entry`: Home Assistant's currency is the site's."""
    hass.config.currency = "NOK"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Site",
        data={},
        version=1,
        minor_version=ENTRY_MINOR_BATTERY_WEAR - 1,
        subentries_data=[
            ConfigSubentryData(
                data=_battery(threshold=0.05),
                subentry_type=SUBENTRY_LOAD,
                title="Battery",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == ENTRY_MINOR == ENTRY_MINOR_BATTERY_WEAR
    (sub,) = entry.subentries.values()
    assert sub.data["strategy_params"]["wear_cost"] == pytest.approx(0.46)
