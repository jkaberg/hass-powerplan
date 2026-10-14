"""D4 §9 53 - the load flow measures what the recorder already knows (D-0731).

`async_measure` is the provider's call into the recorder (D10 §5.6's fits on
the bound entities' history); here it answers the reference house's bathroom
loop - 39 coasts over 10 days, 609 W at p95 - or nothing, and the flow is held
to what it does with either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from custom_components.powerplan.const import LOAD_PARAMS, SUBENTRY_LOAD
from custom_components.powerplan.core.loads import Measured
from tests.flows.conftest import shown
from tests.flows.test_load_flow import _answer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.e2e.fake_house import FakeHouse

MEASURED = {
    "coast_tau_h": Measured(value=59.3, n=39, days=10.0),
    "nameplate_w": Measured(value=609.1, n=1332, days=10.0),
}


async def _review(
    hass: HomeAssistant,
    site: MockConfigEntry,
    charger: FakeHouse,
    measured: dict[str, Measured],
    **answers: Any,
) -> dict[str, Any]:
    """Run the floor loop's flow to its review with the recorder answering `measured`."""
    with patch(
        "custom_components.powerplan.flow.load.async_measure", return_value=measured
    ) as measure:
        result = dict(
            await hass.config_entries.subentries.async_init(
                (site.entry_id, SUBENTRY_LOAD), context={"source": "user"}
            )
        )
        result = await _answer(hass, result, type="floor_heating")
        result = await _answer(hass, result, device=charger.loads["loop_bath_1"].device_id)
        result = await _answer(hass, result, **shown(result))
        assert result["step_id"] == "questions", result
        result = await _answer(hass, result, **{**result["data_schema"]({}), **answers})
    assert result["step_id"] == "review", result
    assert measure.await_count == 1
    return result


async def _created(hass: HomeAssistant, site: MockConfigEntry, result: dict[str, Any]) -> dict:
    """Accept the review as shown; return the new subentry's params."""
    await _answer(hass, result, **result["data_schema"]({"name": "Bath"}))
    subentry = next(s for s in site.subentries.values() if s.title == "Bath")
    return dict(subentry.data[LOAD_PARAMS])


@pytest.mark.inv("INV-66")
async def test_53_what_the_recorder_measured_is_the_default(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The measured `τ` and rating are materialised, and the review names their source."""
    result = await _review(hass, site, charger, MEASURED)
    explanation = result["description_placeholders"]["explanation"]
    assert "last 10 days" in explanation
    params = await _created(hass, site, result)
    assert params["coast_tau_h"] == 59.3
    assert params["nameplate_w"] == 609


async def test_53_no_history_keeps_the_types_defaults_and_says_so(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """A sensor with nothing in the recorder: the table's 60 h, and the review says why."""
    result = await _review(hass, site, charger, {})
    assert "no history" in result["description_placeholders"]["explanation"]
    params = await _created(hass, site, result)
    assert params["coast_tau_h"] == 60.0


async def test_53_an_answer_beats_the_measurement(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The household's own number under Advanced wins, and the review doesn't claim it measured it."""
    result = await _review(
        hass, site, charger, MEASURED, advanced={"coast_tau_h": {"hours": 80, "minutes": 0}}
    )
    params = await _created(hass, site, result)
    assert params["coast_tau_h"] == 80.0
