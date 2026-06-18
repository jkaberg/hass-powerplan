"""`ha_schedule.fetch_windows` against the real `schedule` integration (D-0300).

The component is loaded for real, the way `providers/prices/test_nordpool_action.py`
loads a fake Nord Pool action - except here nothing needs faking: HA's own
`schedule` component ships with `pytest-homeassistant-custom-component`, and the
whole point of D-0300 is that the weekly table is reachable only through its
`get_schedule` action, never through state or attributes.

A live `schedule.*` entity schedules its own `next_event` timer with
`async_track_point_in_utc_time`, which is never cancelled by a YAML-configured
helper's teardown (there is no config entry to unload it). Every test that sets
one up is parametrized `expected_lingering_timers=True`, the harness's own
sanctioned escape hatch for exactly this - the entity's future callback outliving
the test is expected, not a leak this provider caused.
"""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from custom_components.powerplan.core.loads.targets import LocalWindow
from custom_components.powerplan.providers.schedules import fetch_windows

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ENTITY_ID = "schedule.test"


async def _setup_schedule(hass: HomeAssistant, **days: list[dict[str, str]]) -> None:
    """Load the `schedule` component with one helper, `entity_id` `schedule.test`."""
    assert await async_setup_component(
        hass, "schedule", {"schedule": {"test": {"name": "Test", **days}}}
    )
    await hass.async_block_till_done()


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_a_bound_helper_s_windows_come_back_as_local_windows(hass: HomeAssistant) -> None:
    """Monday and Friday's rows become `LocalWindow`s on the matching weekday."""
    await _setup_schedule(
        hass,
        monday=[{"from": "06:00:00", "to": "22:00:00"}],
        friday=[{"from": "08:00:00", "to": "12:00:00"}, {"from": "14:00:00", "to": "18:00:00"}],
    )

    windows = await fetch_windows(hass, ENTITY_ID)

    assert windows == (
        LocalWindow(weekday=0, start=time(6, 0), end=time(22, 0)),
        LocalWindow(weekday=4, start=time(8, 0), end=time(12, 0)),
        LocalWindow(weekday=4, start=time(14, 0), end=time(18, 0)),
    )


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_a_day_with_no_rows_contributes_no_window(hass: HomeAssistant) -> None:
    """Tuesday through Thursday, Saturday and Sunday are absent, not empty windows."""
    await _setup_schedule(hass, monday=[{"from": "06:00:00", "to": "22:00:00"}])

    windows = await fetch_windows(hass, ENTITY_ID)

    assert windows is not None
    assert {w.weekday for w in windows} == {0}


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_a_genuinely_empty_schedule_is_an_empty_tuple_not_none(hass: HomeAssistant) -> None:
    """Off all week is a real answer (`()`), distinct from `None`'s "could not read"."""
    await _setup_schedule(hass)

    windows = await fetch_windows(hass, ENTITY_ID)

    assert windows == ()


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_weekly_table_is_not_on_state_or_attributes(hass: HomeAssistant) -> None:
    """The provider exists because this is true - D-0300's own premise, checked."""
    await _setup_schedule(hass, monday=[{"from": "06:00:00", "to": "22:00:00"}])

    state = hass.states.get(ENTITY_ID)

    assert state is not None
    assert "monday" not in state.attributes
    assert set(state.attributes) <= {"editable", "next_event", "friendly_name"}


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_an_unknown_entity_degrades_to_none(hass: HomeAssistant) -> None:
    """A stale or removed binding is `None` - "could not read" - not a crash."""
    await _setup_schedule(hass, monday=[{"from": "06:00:00", "to": "22:00:00"}])

    windows = await fetch_windows(hass, "schedule.does_not_exist")

    assert windows is None


async def test_the_schedule_integration_missing_degrades_to_none(hass: HomeAssistant) -> None:
    """No `schedule` component loaded at all is the same fallback, not a crash."""
    windows = await fetch_windows(hass, ENTITY_ID)

    assert windows is None


@pytest.mark.inv("INV-3")
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_the_read_is_a_response_only_action(hass: HomeAssistant) -> None:
    """`get_schedule` refuses any call without `return_response=True` (INV-3, D-0300).

    That refusal would surface as a `HomeAssistantError` `fetch_windows` would
    swallow into `()`, so this test calls the action directly to prove the
    *provider's own* call site asks for the response - the runtime half of the
    exemption D-0080 established and D-0300 extends.
    """
    await _setup_schedule(hass, monday=[{"from": "06:00:00", "to": "22:00:00"}])

    response = await hass.services.async_call(
        "schedule", "get_schedule", {"entity_id": ENTITY_ID}, blocking=True, return_response=True
    )

    assert response is not None
    assert response[ENTITY_ID]["monday"] == [{"from": time(6, 0), "to": time(22, 0)}]
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "schedule", "get_schedule", {"entity_id": ENTITY_ID}, blocking=True
        )
