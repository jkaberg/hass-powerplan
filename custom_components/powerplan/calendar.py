"""`calendar.<site>_planned_runs` (D12 §5.6, D8 §5.5): the adopted plans as events.

One event per contiguous active block of each load's plan - "<load>: <kWh>
kWh", with the block's estimated cost - so HA's own `calendar` card and the
Calendar panel show what will run when. The events are read from the adopted
plans, so they move only when a plan is adopted; a block that has ended drops
off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.util import dt as dt_util

from .entity import PowerplanEntity

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import PowerplanConfigEntry
    from .core.model import Plan, PlanSlot
    from .runtime import Runtime

#: Every entity is pushed by the coordinator; none polls (HA rule `parallel-updates`).
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerplanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the site's plan calendar."""
    async_add_entities([SitePlanCalendar(entry.runtime_data)])


def plan_events(
    plans: Mapping[str, Plan], names: Mapping[str, str], currency: str, now: datetime
) -> list[CalendarEvent]:
    """Return every plan's contiguous active blocks that have not ended, by start.

    A slot is active when its envelope is a cap above zero - the plan's own
    reading (`Plan.next_start`); `None` (no plan) and `0.0` (stand still) are
    not runs (INV-30). Two active slots join when one ends where the next starts.
    """
    events: list[CalendarEvent] = []
    for load_id, plan in plans.items():
        block: list[PlanSlot] = []
        for slot in (*plan.slots, None):
            active = slot is not None and slot.envelope_w is not None and slot.envelope_w > 0.0
            if active and block and block[-1].end == slot.start:
                block.append(slot)
                continue
            if block and block[-1].end > now:
                kwh = sum(part.kwh for part in block)
                cost = sum(float(part.price) * part.kwh for part in block)
                events.append(
                    CalendarEvent(
                        start=block[0].start,
                        end=block[-1].end,
                        summary=f"{names.get(load_id, load_id)}: {kwh:.1f} kWh",
                        description=f"{kwh:.2f} kWh · ≈ {cost:.2f} {currency}",
                        uid=f"{load_id}:{block[0].start.isoformat()}",
                    )
                )
            block = [slot] if active else []
    return sorted(events, key=lambda event: (event.start, event.summary))


class SitePlanCalendar(PowerplanEntity, CalendarEntity):
    """The adopted plans' runs, one event per block (D12 §5.6).

    The unique id's key is `plan_calendar`, apart from `sensor.<site>_plan`'s
    `plan`; the name, "Planned runs", gives `calendar.<site>_planned_runs`.
    """

    def __init__(self, runtime: Runtime) -> None:
        """Bind to the site."""
        super().__init__(runtime, "plan_calendar")

    def _events(self) -> list[CalendarEvent]:
        runtime = self.runtime
        names = {load.load_id: load.config.name for load in runtime.build.loads}
        return plan_events(
            runtime.state.plans.plans, names, runtime.build.cfg.currency, dt_util.utcnow()
        )

    @property
    def event(self) -> CalendarEvent | None:
        """The run in progress, else the next one."""
        events = self._events()
        return events[0] if events else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the runs that overlap `[start_date, end_date)`."""
        del hass
        return [
            event
            for event in self._events()
            if event.end_datetime_local > start_date and event.start_datetime_local < end_date
        ]

    def _digest(self) -> str | None:
        """Write on an adoption, and when the first run starts or ends - never per tick."""
        first = self.event
        return f"{self.runtime.state.plans.built_at}:{None if first is None else first.uid}"
