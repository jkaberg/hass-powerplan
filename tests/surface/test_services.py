"""D8 §9 7: the services validate their schema and reach the documented runtime call."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.runtime import Runtime
from custom_components.powerplan.services import SERVICES

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


def test_07_every_service_is_registered_once(hass: HomeAssistant, site: MockConfigEntry) -> None:
    """The eight v1 services exist under the domain (D8 §5.7)."""
    for name in SERVICES:
        assert hass.services.has_service(DOMAIN, name), name


async def test_07b_schema_validation_refuses_a_bad_call(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """A boost of 40 hours and a peak without a day are refused before any engine call."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "boost", {"load": "ev", "hours": 40}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_peak", {"kw": 5.0}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "release", {"load": "nobody"}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "replan", {"site": "no-such-site"}, blocking=True)


@pytest.mark.parametrize(
    "row",
    [
        ("replan", {}, "async_replan", ()),
        ("set_presence", {"mode": "away"}, "async_set_presence_setting", ("away",)),
        ("reset_window_anchor", {}, "async_reset_window_anchor", ()),
        (
            "set_peak",
            {"month": "2026-01", "kw": 7.5},
            "async_set_peak",
            ("month", "2026-01", 7.5, ""),
        ),
    ],
)
async def test_07c_each_site_service_reaches_its_runtime_call(
    hass: HomeAssistant,
    site: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
    row: tuple[str, dict[str, Any], str, tuple[Any, ...]],
) -> None:
    """One service, one call, the documented arguments."""
    service, data, method, expected = row
    calls: list[tuple[Any, ...]] = []

    async def spy(self: Runtime, *args: Any, **kwargs: Any) -> None:
        calls.append(
            tuple(args)
            + tuple(value for _key, value in sorted(kwargs.items()) if value is not None)
        )

    monkeypatch.setattr(Runtime, method, spy)
    await hass.services.async_call(DOMAIN, service, data, blocking=True)
    assert calls == [expected]


async def test_07d_dump_state_answers_with_the_snapshot_and_the_inputs(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """`dump_state` returns JSON with the site's snapshot, inputs and store sections."""
    response = await hass.services.async_call(
        DOMAIN, "dump_state", {}, blocking=True, return_response=True
    )
    assert response is not None
    dump = response["sites"][site.entry_id]
    assert dump["snapshot"]["site"]["name"] == "Test site"
    assert dump["inputs"]["trigger"] == "dump"
    assert "runtime" in dump["state"]


async def test_07e_set_presence_and_set_peak_take_effect(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """The two knob services move the runtime and the tariff history (D2 §5.12)."""
    runtime: Runtime = site.runtime_data
    await hass.services.async_call(DOMAIN, "set_presence", {"mode": "vacation"}, blocking=True)
    assert runtime.presence_setting == "vacation"
    assert runtime.snapshot is not None
    assert runtime.snapshot.site.presence is not None
    assert runtime.snapshot.site.presence.value == "vacation"

    await hass.services.async_call(
        DOMAIN, "set_peak", {"date": "2026-01-10", "kw": 9.0, "note": "the sauna"}, blocking=True
    )
    overrides = runtime.build.tariff.history.overrides
    assert any(row.kw == 9.0 and row.note == "the sauna" for row in overrides)
