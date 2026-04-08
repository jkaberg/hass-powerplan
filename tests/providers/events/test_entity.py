"""An entity's state or attributes become an `Event` (D1 §5.6, §9 item 16's half).

A state change becomes an announcement with the right kind, start and end. What
the store then does with it - replace on a later `issued_at`, revoke, expire - is
pure and already covered in `tests/core/pricing/test_events.py`; here the question
is only whether the provider reads the entity correctly.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import EventKind, EventStore
from custom_components.powerplan.providers.events import EntityEventSource

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

OSLO = ZoneInfo("Europe/Oslo")
TEMPO = "sensor.rte_tempo_couleur_actuelle"
DR_WINDOW = "binary_sensor.saving_session"

#: RTE Tempo's own vocabulary → powerplan's (D1 §2, the `day_type` row).
TEMPO_COLOURS = {"BLUE": "tempo_blue", "WHITE": "tempo_white", "RED": "tempo_red"}


def day_type(hass: HomeAssistant, **kwargs: object) -> EntityEventSource:
    """Build a day-type source over the Tempo colour sensor."""
    return EntityEventSource(
        hass,
        entity_id=TEMPO,
        kind=EventKind.DAY_TYPE,
        tz=OSLO,
        state_map=TEMPO_COLOURS,
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# a day type: the window is the local day
# --------------------------------------------------------------------------- #


async def test_a_day_type_covers_the_whole_local_day(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A colour sensor announces a day, and the day is local (D1 §5.6)."""
    freezer.move_to("2026-09-19T09:00:00+00:00")
    hass.states.async_set(TEMPO, "RED")

    events = await day_type(hass).poll()

    assert len(events) == 1
    event = events[0]
    assert event.kind is EventKind.DAY_TYPE
    assert event.payload == {"type": "tempo_red"}
    assert event.start.isoformat() == "2026-09-18T22:00:00+00:00"
    assert event.end.isoformat() == "2026-09-19T22:00:00+00:00"
    assert event.valid_until == event.end


async def test_a_day_offset_announces_tomorrow(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Tempo's *prochaine couleur* is published today and applies tomorrow."""
    freezer.move_to("2026-09-19T09:00:00+00:00")
    hass.states.async_set(TEMPO, "WHITE")

    events = await day_type(hass, day_offset=1).poll()

    assert events[0].start.isoformat() == "2026-09-19T22:00:00+00:00"
    assert events[0].end.isoformat() == "2026-09-20T22:00:00+00:00"
    assert events[0].payload == {"type": "tempo_white"}


async def test_an_unmapped_state_is_passed_through_not_dropped(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A type we do not know reaches the modifier, which has its own fallback."""
    freezer.move_to("2026-09-19T09:00:00+00:00")
    hass.states.async_set(TEMPO, "PURPLE")

    events = await day_type(hass).poll()

    assert events[0].payload == {"type": "purple"}


@pytest.mark.parametrize("state", ["unavailable", "unknown", "off", "none"])
async def test_an_entity_that_announces_nothing_yields_nothing(
    hass: HomeAssistant, state: str
) -> None:
    """Silence is not a revocation and not a guess (D1 §5.6)."""
    hass.states.async_set(TEMPO, state)

    assert await day_type(hass).poll() == []


async def test_an_entity_that_does_not_exist_yields_nothing(hass: HomeAssistant) -> None:
    """A deleted event entity degrades; it does not raise."""
    assert await day_type(hass).poll() == []


# --------------------------------------------------------------------------- #
# an event window from attributes
# --------------------------------------------------------------------------- #


async def test_a_window_comes_from_the_named_attributes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A DR window says when it applies; the provider does not assume a day."""
    freezer.move_to("2026-11-12T15:00:00+00:00")
    hass.states.async_set(
        DR_WINDOW,
        "on",
        {
            "start": "2026-11-12T17:30:00+01:00",
            "end": "2026-11-12T18:30:00+01:00",
            "per_kwh": 2.25,
            "baseline": 1.1,
        },
    )

    events = await EntityEventSource(
        hass,
        entity_id=DR_WINDOW,
        kind=EventKind.REWARD,
        tz=OSLO,
        start_attribute="start",
        end_attribute="end",
        payload_attributes=("per_kwh", "baseline"),
    ).poll()

    assert len(events) == 1
    event = events[0]
    assert event.kind is EventKind.REWARD
    assert event.start.isoformat() == "2026-11-12T16:30:00+00:00"
    assert event.end.isoformat() == "2026-11-12T17:30:00+00:00"
    assert event.payload == {"per_kwh": 2.25, "baseline": 1.1}


async def test_a_load_limit_announces_watts_as_a_number(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """D6 reads `max_w` as watts, so it must not arrive as text."""
    freezer.move_to("2026-11-12T15:00:00+00:00")
    hass.states.async_set(
        "sensor.grid_operator_limit",
        "4200",
        {"from": "2026-11-12T17:00:00+01:00", "to": "2026-11-12T20:00:00+01:00"},
    )

    events = await EntityEventSource(
        hass,
        entity_id="sensor.grid_operator_limit",
        kind=EventKind.LOAD_LIMIT,
        tz=OSLO,
        start_attribute="from",
        end_attribute="to",
    ).poll()

    assert events[0].payload == {"max_w": 4200.0}


async def test_a_window_that_ends_before_it_starts_is_refused(hass: HomeAssistant) -> None:
    """A broken announcement is dropped loudly, never stored (D1 §8)."""
    hass.states.async_set(
        DR_WINDOW,
        "on",
        {"start": "2026-11-12T18:30:00+01:00", "end": "2026-11-12T17:30:00+01:00"},
    )

    events = await EntityEventSource(
        hass,
        entity_id=DR_WINDOW,
        kind=EventKind.PRICE_SPIKE,
        tz=OSLO,
        start_attribute="start",
        end_attribute="end",
    ).poll()

    assert events == []


async def test_half_a_window_is_refused(hass: HomeAssistant) -> None:
    """One end of a window is a misconfiguration, not half an announcement."""
    hass.states.async_set(DR_WINDOW, "on", {"start": "2026-11-12T18:30:00+01:00"})

    events = await EntityEventSource(
        hass,
        entity_id=DR_WINDOW,
        kind=EventKind.PRICE_SPIKE,
        tz=OSLO,
        start_attribute="start",
        end_attribute="end",
    ).poll()

    assert events == []


async def test_valid_until_is_read_when_the_entity_gives_one(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """`valid_until` defaults to `end`, but the entity may shorten it (D1 §2)."""
    freezer.move_to("2026-11-12T15:00:00+00:00")
    hass.states.async_set(
        DR_WINDOW,
        "on",
        {
            "start": "2026-11-12T17:00:00+01:00",
            "end": "2026-11-12T20:00:00+01:00",
            "valid_until": "2026-11-12T18:00:00+01:00",
        },
    )

    events = await EntityEventSource(
        hass,
        entity_id=DR_WINDOW,
        kind=EventKind.PRICE_SPIKE,
        tz=OSLO,
        start_attribute="start",
        end_attribute="end",
    ).poll()

    assert events[0].valid_until.isoformat() == "2026-11-12T17:00:00+00:00"


# --------------------------------------------------------------------------- #
# what the store then does with it
# --------------------------------------------------------------------------- #


async def test_a_state_change_replaces_rather_than_duplicates(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A re-announced day keeps one entry: the id is stable, `issued_at` is not."""
    freezer.move_to("2026-09-19T09:00:00+00:00")
    source = day_type(hass)
    hass.states.async_set(TEMPO, "WHITE")
    store = EventStore().upsert(await source.poll())

    freezer.move_to("2026-09-19T11:00:00+00:00")
    hass.states.async_set(TEMPO, "RED")
    store = store.upsert(await source.poll())

    assert len(store.events) == 1
    assert store.day_type_at(datetime.fromisoformat("2026-09-19T11:00:00+00:00").date(), OSLO) == (
        "tempo_red"
    )


async def test_the_announcement_is_dated_when_the_entity_changed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """`issued_at` is when the announcement arrived, not when we polled (D1 §2)."""
    freezer.move_to("2026-09-19T09:00:00+00:00")
    hass.states.async_set(TEMPO, "RED")
    announced = dt_util.utcnow()

    freezer.move_to("2026-09-19T09:05:00+00:00")
    events = await day_type(hass).poll()

    assert events[0].issued_at == announced
