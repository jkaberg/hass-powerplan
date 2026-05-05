"""D8 §9 8: every bus payload validates against its schema; edges fire once.

The site is real: its first tick fires `stage_changed`, its first fetch
`prices_received`, and a repeated stage fires nothing again. The builder
refuses a payload missing what the row promises.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
import voluptuous as vol
from homeassistant.core import Event, callback
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan import events
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.engine import EventKind
from custom_components.powerplan.entity import unique_id
from tests.runtime.conftest import SITE_ENTRY_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.runtime.conftest import FakeMeter

AT = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)


def test_08_every_kind_has_a_schema_and_a_name() -> None:
    """The table of §5.6 is complete and the names carry the domain prefix."""
    for kind in EventKind:
        assert kind in events.SCHEMAS, kind
        assert events.event_name(kind) == f"{DOMAIN}_{kind.value}"
    assert set(events.EVENT_TYPES) == {kind.value for kind in EventKind}


def test_08b_the_builder_adds_the_envelope_and_refuses_a_short_payload() -> None:
    """`schema`, `site_id`, `at`, `kind` ride on every event; a missing field is refused."""
    payload = events.build(
        EventKind.SAFE_MODE, {"entered": True, "reason": "boom"}, site_id="e1", at=AT
    )
    assert payload == {
        "schema": 1,
        "site_id": "e1",
        "at": AT.isoformat(),
        "kind": "safe_mode",
        "entered": True,
        "reason": "boom",
    }
    with pytest.raises(vol.Invalid):
        events.build(EventKind.SAFE_MODE, {"entered": True}, site_id="e1", at=AT)
    with pytest.raises(vol.Invalid):
        events.build(
            EventKind.BREACH,
            {"kind": "meteor", "excess_w": 1.0, "scope": "site", "table": []},
            site_id="e1",
            at=AT,
        )


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (
            EventKind.PEAK_WARNING,
            {
                "window_start": AT.isoformat(),
                "expected_kwh": 9.9,
                "ceiling_kwh": 10.0,
                "cleared": False,
                "drivers": [["ev", 5.0]],
                "advice": ["stop the charger"],
            },
        ),
        (
            EventKind.COMFORT_VIOLATION,
            {
                "load": "loop",
                "current": 20.0,
                "floor": 21.0,
                "served": True,
                "over_allowance": False,
            },
        ),
        (
            EventKind.MONTH_CLOSED,
            {
                "month": "2026-01",
                "cost": "100.00 NOK",
                "savings": "5.00 NOK",
                "energy_savings": "5.00 NOK",
                "capacity_savings": "0.00 NOK",
                "confidence": "none",
                "by_load": [],
            },
        ),
        (EventKind.PRESENCE_CHANGED, {"old": "home", "new": "away", "source": "auto"}),
        (
            EventKind.LEVEL_CHANGED,
            {"old": "2–5 kW", "new": "5–10 kW", "metric_kw": 6.1, "fee": "441", "projected": True},
        ),
    ],
)
def test_08c_documented_payloads_validate(kind: EventKind, data: dict[str, Any]) -> None:
    """D8 §5.6's rows, as data."""
    payload = events.build(kind, data, site_id="e1", at=AT)
    assert payload["kind"] == kind.value


async def test_08d_a_running_site_fires_valid_events_once_per_edge(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime, meter: FakeMeter
) -> None:
    """`stage_changed` on the first tick, `prices_received` on the first fetch, no repeats."""
    seen: list[Event[Any]] = []

    @callback
    def record(event: Event[Any]) -> None:
        seen.append(event)

    for kind in EventKind:
        site.async_on_unload(hass.bus.async_listen(events.event_name(kind), record))

    for _ in range(3):
        await runtime.run_tick("heartbeat")
    await runtime.async_replan()
    await hass.async_block_till_done()

    kinds = [event.event_type for event in seen]
    assert kinds.count(events.event_name(EventKind.STAGE_CHANGED)) == 0, (
        "the stage did not move after the first tick, so no edge fired again"
    )
    for event in seen:
        assert event.data["schema"] == 1
        assert event.data["site_id"] == site.entry_id
        assert event.data["site"] == "Test site"
        kind = EventKind(event.data["kind"])
        assert events.SCHEMAS[kind](dict(event.data))

    # The event entity mirrors the last one.
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("event", DOMAIN, unique_id(SITE_ENTRY_ID, "events"))
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    if runtime.last_event is not None:
        assert state.attributes["event_type"] == runtime.last_event[0]


async def test_08e_the_first_tick_fired_stage_changed_and_the_fetch_prices_received(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """Listen before the site starts: the startup edges are on the bus."""
    from tests.runtime.conftest import site_entry  # noqa: PLC0415 - after the listener is armed

    seen: list[Event[Any]] = []

    @callback
    def record(event: Event[Any]) -> None:
        seen.append(event)

    hass.bus.async_listen(events.event_name(EventKind.STAGE_CHANGED), record)
    hass.bus.async_listen(events.event_name(EventKind.PRICES_RECEIVED), record)
    entry = site_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    kinds = [event.data["kind"] for event in seen]
    assert kinds.count("stage_changed") == 1
    stage = next(event for event in seen if event.data["kind"] == "stage_changed")
    assert stage.data["old"] == 0
    assert stage.data["new"] == 0
    assert "reason" in stage.data
    assert kinds.count("prices_received") >= 1
    prices = next(event for event in seen if event.data["kind"] == "prices_received")
    assert prices.data["source"] == "manual"
    assert prices.data["coverage_h"] > 0
    await hass.config_entries.async_unload(entry.entry_id)
