"""D12 §9 14 (B6): `sensor.<site>_level` carries the tariff's ladder for the month gauge.

`steps` is `[{name, from_kw, to_kw, fee}]` from the version in force: each step
starts where the one below it ends, the top is open (`to_kw` `None`), the fee is
written as every money attribute is. Static per version, so the recorder keeps
none of it. A tariff without a step table has no `steps`.
"""

from __future__ import annotations

from itertools import pairwise
from types import SimpleNamespace
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.tariffs.model import StepTable
from custom_components.powerplan.entity import money_text, unique_id
from custom_components.powerplan.sensor import level_steps

if TYPE_CHECKING:
    import pytest
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime


def _level(hass: HomeAssistant, site: MockConfigEntry) -> tuple[str, dict[str, object]]:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "level")
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return entity_id, dict(state.attributes)


async def test_14_the_level_sensor_carries_the_ladder(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """Tensio's steps, contiguous from 0 kW to an open top, each with its fee."""
    peak = runtime.build.tariff.active_version().peak
    assert peak is not None
    assert isinstance(peak.pricing, StepTable)
    table = peak.pricing.steps

    entity_id, attributes = _level(hass, site)
    steps = attributes["steps"]
    assert isinstance(steps, list)
    assert [row["name"] for row in steps] == [step.name for step in table]
    assert steps[0]["from_kw"] == 0.0
    for below, above in pairwise(steps):
        assert above["from_kw"] == below["to_kw"]
    assert steps[-1]["to_kw"] is None
    assert [row["fee"] for row in steps] == [money_text(step.fee_per_period) for step in table]

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state_info is not None
    assert "steps" in state.state_info["unrecorded_attributes"]


async def test_14_no_step_table_no_steps(runtime: Runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    """A version without a step table - here, no capacity tariff at all - publishes no `steps`."""
    monkeypatch.setattr(runtime.build.tariff, "active_version", lambda: SimpleNamespace(peak=None))
    assert level_steps(runtime) == {}
