"""D12 §9 15 (B4): every bus event reads as one line in the logbook, on the appliance's row.

The describer is Home Assistant's own platform hook (`logbook.async_describe_events`);
the test collects what it registers rather than loading the logbook component,
which would need a recorder. What the logbook card needs from the event itself -
the `entity_id` in the event's data, which its query and its live stream both
filter on - is read off the real bus.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.logbook.models import LazyEventPartialState, async_event_to_row
from homeassistant.core import Event, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from custom_components.powerplan import logbook
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.engine import EventKind
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.events import event_name
from tests.flows.test_accounting_surface import _add_charger_load

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "powerplan"
LANGUAGES = ("en", "nb")
OPTIONS = f"component.{DOMAIN}.selector.logbook.options."


def _options(language: str) -> dict[str, str]:
    document = json.loads(
        (INTEGRATION / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )
    options: dict[str, str] = document["selector"]["logbook"]["options"]
    return options


@pytest.mark.parametrize("language", LANGUAGES)
def test_15_every_event_kind_has_its_lines_in_both_languages(language: str) -> None:
    """Every `EventKind`, every state its payload names, and no line nobody reads."""
    options = _options(language)
    assert set(logbook.MESSAGES) == set(EventKind)
    wanted = {key for keys in logbook.MESSAGES.values() for key in keys}
    assert wanted == set(options), sorted(wanted ^ set(options))
    assert all(options[key] for key in wanted)


def test_15_the_payload_states_pick_their_own_line() -> None:
    """A state-carrying kind reads the line for its state; every key it can pick exists."""
    cases: list[tuple[EventKind, dict[str, Any], str]] = [
        (EventKind.PLAN_ADOPTED, {"planned_kwh": 0.0, "next_start": None}, "plan_adopted_idle"),
        (EventKind.PLAN_ADOPTED, {"planned_kwh": 1.0, "next_start": "x"}, "plan_adopted"),
        (EventKind.PEAK_WARNING, {"cleared": True}, "peak_warning_cleared"),
        (EventKind.SAFE_MODE, {"entered": False}, "safe_mode_left"),
        (EventKind.EV_CONNECTED, {"connected": False}, "ev_connected_off"),
        (EventKind.PRESENCE_CHANGED, {"new": "vacation"}, "presence_changed_vacation"),
        (EventKind.CYCLE, {"state": "aborted"}, "cycle_aborted"),
    ]
    for kind, data, key in cases:
        assert logbook.message_key(kind, data) == key
        assert key in logbook.MESSAGES[kind]


def test_15_a_new_plan_reads_like_the_household_s_example() -> None:
    """'Ny plan: 1,06 kWh fra 22:00 · ≈ 0,77 kr' - numbers in the language, the time local."""
    data = {
        "load": "floor",
        "planned_kwh": 1.0612,
        "cost": "0.7712",
        "currency": "NOK",
        "next_start": datetime(2027, 1, 12, 21, 0, tzinfo=UTC).isoformat(),
    }
    strings = {f"{OPTIONS}{key}": value for key, value in _options("nb").items()}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(logbook, "_clock", lambda _value: "22:00")
        line = logbook.describe(EventKind.PLAN_ADOPTED, data, strings, "nb")
    assert line == "Ny plan: 1,06 kWh fra 22:00 · ≈ 0,77 kr"


def test_15_a_plan_already_running_says_so_not_the_minute_it_was_adopted() -> None:
    """D8: `next_start` at or before the event reads "går nå", never "fra 22:08"."""
    fired = datetime(2027, 1, 12, 21, 8, tzinfo=UTC)
    data = {
        "planned_kwh": 1.0612,
        "cost": "0.7712",
        "currency": "NOK",
        "next_start": fired.isoformat(),
    }
    assert logbook.message_key(EventKind.PLAN_ADOPTED, data, fired) == "plan_adopted_now"
    assert (
        logbook.message_key(EventKind.PLAN_ADOPTED, data, fired - timedelta(minutes=8))
        == "plan_adopted"
    )
    strings = {f"{OPTIONS}{key}": value for key, value in _options("nb").items()}
    assert logbook.describe(EventKind.PLAN_ADOPTED, data, strings, "nb", fired) == (
        "Ny plan: 1,06 kWh, går nå · ≈ 0,77 kr"
    )


async def test_15_a_fired_event_is_described_on_the_appliance_s_plan_status(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The bus event carries `entity_id` (the card's filter); the line names the appliance."""
    load_id = await _add_charger_load(hass, site, charger)
    runtime: Runtime = site.runtime_data
    registry = er.async_get(hass)
    plan_status = registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "plan_status", load_id)
    )
    site_events = registry.async_get_entity_id("event", DOMAIN, unique_id(site.entry_id, "events"))
    assert plan_status is not None
    assert site_events is not None

    seen: list[Event[Any]] = []

    @callback
    def record(event: Event[Any]) -> None:
        seen.append(event)

    for kind in (EventKind.PLAN_ADOPTED, EventKind.SAFE_MODE):
        site.async_on_unload(hass.bus.async_listen(event_name(kind), record))

    describers: dict[str, Callable[[Event[Any]], dict[str, str]]] = {}

    def register(domain: str, name: str, describer: Callable[[Event[Any]], dict[str, str]]) -> None:
        assert domain == DOMAIN
        describers[name] = describer

    logbook.async_describe_events(hass, register)
    assert set(describers) == {event_name(kind) for kind in EventKind}

    hass.config.language = "nb"
    await async_get_translations(hass, "nb", "selector", {DOMAIN})
    runtime.fire_event(
        EventKind.PLAN_ADOPTED,
        {
            "load": load_id,
            "mode": "normal",
            "planned_kwh": 1.06,
            "cost": "0.77",
            "currency": "NOK",
            "next_start": datetime(2027, 1, 12, 21, 0, tzinfo=UTC).isoformat(),
            "reason": "cheapest hours",
        },
    )
    runtime.fire_event(EventKind.SAFE_MODE, {"entered": True, "reason": "test"})
    await hass.async_block_till_done()

    plan, safe = seen
    assert plan.data["entity_id"] == plan_status
    assert safe.data["entity_id"] == site_events
    # The event entity mirrors the payload without it: an `entity_id` attribute reads as a group.
    state = hass.states.get(site_events)
    assert state is not None
    assert "entity_id" not in state.attributes

    line = describers[plan.event_type](plan)
    # No `entity_id` in the line: the frontend would title it "Planstatus" instead of the name (D8).
    assert "entity_id" not in line
    assert line["name"] == "Charger"
    assert line["message"] == "Ny plan: 1,06 kWh fra 22:00 · ≈ 0,77 kr"
    # The logbook's processor hands a `LazyEventPartialState`: no `time_fired`, the timestamp
    # only in its row (D-0504).
    lazy = LazyEventPartialState(async_event_to_row(plan), {})
    assert describers[plan.event_type](lazy) == line  # type: ignore[arg-type]
    line = describers[safe.event_type](safe)
    assert "entity_id" not in line
    assert line["message"] == "Nødmodus: styringen har stoppet"
