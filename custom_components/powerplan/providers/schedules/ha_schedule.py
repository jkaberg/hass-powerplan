"""`ha_schedule`: a bound `schedule.*` helper's weekly windows (D4 §4.4, D-0300).

A `schedule.*` entity's state is `on`/`off` and its attributes are only
`next_event`/`editable` - the weekly table itself is private to the entity
object. The one public way to read it is the integration's own `get_schedule`
action, registered `SupportsResponse.ONLY` (no side effect, nothing to block on
twice), which is why this file - and only this file, besides
`providers/prices/nordpool_action.py` - is on `test_single_writer.py`'s
allowlist for `hass.services.async_call` (INV-3, D-0300).
"""

from __future__ import annotations

import logging
from datetime import time
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError

from custom_components.powerplan.core.loads.targets import LocalWindow

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

#: `get_schedule`'s response keys, Monday first - `LocalWindow.weekday` 0 is Monday.
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


async def fetch_windows(hass: HomeAssistant, entity_id: str) -> tuple[LocalWindow, ...] | None:
    """Return every local window `entity_id`'s schedule holds, across the week.

    `None` means "could not be read" - a missing helper, a `schedule`
    integration not loaded, a service refusal, or an `entity_id` the response
    never mentions - and is the caller's signal to keep the `ConstantSchedule`
    fallback rather than adopt an `HaScheduleEntity` that is wrongly always off
    (D4 §4.4). A real schedule with no windows configured at all is `()`, not
    `None`: the helper answered, and off all week is what it said.
    """
    try:
        response = await hass.services.async_call(
            "schedule",
            "get_schedule",
            {"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
    except HomeAssistantError as err:
        _LOGGER.warning("schedule %s could not be read: %s", entity_id, err)
        return None

    week = (response or {}).get(entity_id)
    if not isinstance(week, dict):
        _LOGGER.warning("schedule %s: get_schedule did not answer for it", entity_id)
        return None

    windows: list[LocalWindow] = []
    for weekday, name in enumerate(_WEEKDAYS):
        rows = week.get(name)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            start, end = row.get("from"), row.get("to")
            if isinstance(start, time) and isinstance(end, time):
                windows.append(LocalWindow(weekday=weekday, start=start, end=end))
    return tuple(windows)


__all__ = ["fetch_windows"]
