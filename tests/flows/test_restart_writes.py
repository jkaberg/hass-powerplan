"""A restart in control leaves a device powerplan never wrote to alone.

The house's charger: a household watchdog automation sets its current limit to
10 A a few minutes after every Home Assistant start, and a start of powerplan
used to write 32 A over it, `restore_all()` handing back a charger it had never
steered. Release and restore undo only writes powerplan itself made and has on
record (INV-26, INV-27), so a restart of a site in control, with the charger
bound through the real flow and the Easee-shaped fake behind the service bus,
sends the charger nothing - not on the way out, and not before the first tick on
the way back in.

Every service call is spied at Home Assistant's service registry: once the site's
own `number`, `select` and `switch` platforms load, those domains' services are
Home Assistant's, not the fake house's, so the fake's own call log would miss a
write to the charger's `number` entity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.const import CONF_ACTIVE
from custom_components.powerplan.core.engine import Engine
from tests.flows.test_load_flow import _add_charger, _answer
from tests.runtime.conftest import (  # noqa: F401 - `hass_config_dir`: this test's own store file
    hass_config_dir,
    restart_entry,
)
from tests.sim.base import Command as SimCommand

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

LIMIT = "number.ev_dynamic_charger_current"
WATCHDOG_A = 10.0


def spy_service_calls(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the target entity of every service call Home Assistant's registry sees."""
    targets: list[str] = []
    registry = type(hass.services)
    original = registry.async_call

    async def spy(self: Any, domain: str, service: str, *args: Any, **kwargs: Any) -> Any:
        target = (kwargs.get("target") or {}).get("entity_id")
        data = args[0] if args else kwargs.get("service_data")
        entity = target or (data or {}).get("entity_id")
        targets.append(f"{domain}.{service} on {entity}")
        return await original(self, domain, service, *args, **kwargs)

    monkeypatch.setattr(registry, "async_call", spy)
    return targets


@pytest.mark.inv("INV-26")
@pytest.mark.inv("INV-27")
@pytest.mark.inv("INV-48")
async def test_a_restart_in_control_leaves_a_charger_it_never_wrote_to_alone(
    *,
    hass: HomeAssistant,
    site: MockConfigEntry,
    charger: FakeHouse,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another automation owns the limit at 10 A; unload and start send the charger nothing."""
    assert site.data[CONF_ACTIVE] is True, "a site in control"
    result = await _add_charger(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Charger"})
    await hass.async_block_till_done()
    ev_id = next(s.subentry_id for s in site.subentries.values() if s.title == "Charger")
    runtime: Runtime = site.runtime_data
    assert ev_id in runtime.build.devices

    # The household's watchdog, as in the house: 10 A, set by somebody else.
    charger.pending["ev"] = SimCommand(limit_a=WATCHDOG_A)
    for _ in range(4):  # the Bluetooth poll reports it within thirty seconds
        await charger.advance(freezer)
    state = hass.states.get(LIMIT)
    assert state is not None
    assert float(state.state) == WATCHDOG_A
    record = runtime.state.loads.get(ev_id)
    assert record is None or record.gate.last_value is None, "powerplan never wrote to it"

    calls = spy_service_calls(hass, monkeypatch)
    before_first_tick: list[list[str]] = []
    original = Engine.tick

    def spy(self: Engine, engine_state: Any, inputs: Any) -> Any:
        if inputs.trigger == "startup":
            before_first_tick.append([call for call in calls if ".ev_" in call])
        return original(self, engine_state, inputs)

    monkeypatch.setattr(Engine, "tick", spy)
    await restart_entry(hass, site, hass_storage)

    assert before_first_tick == [[]], "neither the unload nor the start wrote to the charger"
    state = hass.states.get(LIMIT)
    assert state is not None
    assert float(state.state) == WATCHDOG_A, "the watchdog's 10 A stands"
