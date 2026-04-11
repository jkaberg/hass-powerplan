"""The Nord Pool response action, and what it costs (D1 §9 item 3, INV-6).

Item 3 is "a restart with a complete store performs zero fetches; a hole triggers
exactly one". Here the fetch is a Home Assistant action call, so the count is
literally the number of times the mocked `nordpool.get_prices_for_date` handler
ran - registered the way the core integration registers it,
`SupportsResponse.ONLY`, which is also what proves the provider passes
`return_response=True` (INV-3): Home Assistant refuses a response-only action
called any other way.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError

from custom_components.powerplan.providers.prices import (
    NordpoolActionSource,
    SourceAuthError,
    SourceEmptyError,
    SourceUnavailableError,
    fetch_missing,
)
from custom_components.powerplan.providers.prices.markets import NORDPOOL_MARKETS
from custom_components.powerplan.providers.prices.nordpool_action import (
    NORDPOOL_DOMAIN,
    SERVICE_GET_PRICES_FOR_DATE,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from .conftest import InMemoryRawStore

OSLO = ZoneInfo("Europe/Oslo")
AREA = "NO3"
ENTRY_ID = "nordpool-entry-id"

TODAY = date(2026, 9, 19)
TOMORROW = date(2026, 9, 20)
HOURS_PER_DAY = 24


class FakeAction:
    """The core integration's action, registered exactly as it registers it.

    A stand-in for the integration, not for the provider: it answers from the
    captured-shape fixtures and counts what it was asked, so a test can assert
    the number of calls the way D1 §9 item 3 words it.
    """

    def __init__(self, days: dict[str, Any]) -> None:
        """Answer for the days in `days`, keyed by ISO date."""
        self.days = days
        self.calls: list[ServiceCall] = []

    def register(self, hass: HomeAssistant) -> None:
        """Register `nordpool.get_prices_for_date` as `SupportsResponse.ONLY`."""

        async def handler(call: ServiceCall) -> ServiceResponse:
            return await self.handle(call)

        hass.services.async_register(
            NORDPOOL_DOMAIN,
            SERVICE_GET_PRICES_FOR_DATE,
            handler,
            supports_response=SupportsResponse.ONLY,
        )

    async def handle(self, call: ServiceCall) -> ServiceResponse:
        """Record the call and answer with that day's entries."""
        self.calls.append(call)
        asked = call.data["date"]
        return self.days.get(asked, {AREA: []})

    @property
    def asked_days(self) -> list[str]:
        """The dates asked for, in order."""
        return [call.data["date"] for call in self.calls]


@pytest.fixture
def action(hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]) -> FakeAction:
    """Register a Nord Pool action that knows today and tomorrow."""
    fake = FakeAction(
        {
            TODAY.isoformat(): format_fixture("nordpool_action_no3")["response"],
            TOMORROW.isoformat(): format_fixture("nordpool_action_no3_tomorrow")["response"],
        }
    )
    fake.register(hass)
    return fake


def source(hass: HomeAssistant) -> NordpoolActionSource:
    """Build the NO3 source in the reference house's currency."""
    return NordpoolActionSource(
        hass,
        config_entry_id=ENTRY_ID,
        area=AREA,
        currency="NOK",
        site_currency="NOK",
        tz=OSLO,
    )


def at(local: str) -> datetime:
    """Return a UTC instant from an Oslo wall-clock `YYYY-MM-DD HH:MM`."""
    return datetime.fromisoformat(local).replace(tzinfo=OSLO)


# --------------------------------------------------------------------------- #
# D1 §9 item 3 - what a restart and a hole cost (INV-6)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-6")
async def test_a_restart_with_a_complete_store_performs_zero_action_calls(
    hass: HomeAssistant,
    action: FakeAction,
    raw_store: InMemoryRawStore,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Raw slots are persisted, so a restart asks Nord Pool nothing (INV-6)."""
    freezer.move_to(at("2026-09-19 14:00"))
    nordpool = source(hass)
    raw_store.add(nordpool.key, await nordpool.fetch(TODAY))
    raw_store.add(nordpool.key, await nordpool.fetch(TOMORROW))
    action.calls.clear()

    report = await fetch_missing([nordpool], raw_store, at("2026-09-19 14:00"), tz=OSLO)

    assert len(action.calls) == 0
    assert report.calls == 0


@pytest.mark.inv("INV-6")
async def test_a_hole_triggers_exactly_one_action_call(
    hass: HomeAssistant,
    action: FakeAction,
    raw_store: InMemoryRawStore,
    freezer: FrozenDateTimeFactory,
) -> None:
    """One missing day is one fetch - not two, and not a whole horizon (INV-6)."""
    freezer.move_to(at("2026-09-19 14:00"))
    nordpool = source(hass)
    raw_store.add(nordpool.key, await nordpool.fetch(TOMORROW))
    action.calls.clear()

    report = await fetch_missing([nordpool], raw_store, at("2026-09-19 14:00"), tz=OSLO)

    assert len(action.calls) == 1
    assert action.asked_days == [TODAY.isoformat()]
    assert report.calls == 1
    assert report.slots == HOURS_PER_DAY


@pytest.mark.inv("INV-6")
async def test_tomorrow_is_not_asked_for_before_its_publication(
    hass: HomeAssistant, action: FakeAction, raw_store: InMemoryRawStore
) -> None:
    """Before 13:00 market time there is nothing to ask for (D1 §5.1)."""
    report = await fetch_missing([source(hass)], raw_store, at("2026-09-19 11:30"), tz=OSLO)

    assert action.asked_days == [TODAY.isoformat()]
    assert report.calls == 1


@pytest.mark.inv("INV-6")
async def test_tomorrow_is_asked_for_after_its_publication(
    hass: HomeAssistant, action: FakeAction, raw_store: InMemoryRawStore
) -> None:
    """After 13:00 market time a cold start costs one fetch per day (D1 §5.1)."""
    report = await fetch_missing([source(hass)], raw_store, at("2026-09-19 13:30"), tz=OSLO)

    assert action.asked_days == [TODAY.isoformat(), TOMORROW.isoformat()]
    assert report.calls == 2
    assert raw_store.count("nordpool_action") == 2 * HOURS_PER_DAY


def test_the_publication_is_thirteen_hundred_in_market_time(hass: HomeAssistant) -> None:
    """Nord Pool publishes in CET/CEST, not in the site's zone (HLD §6.1).

    The zone is derived from the configured area, because it is a fact about the
    market and not about the house (`design/DECISIONS.md` D-0100).
    """
    publication = source(hass).publication()

    assert publication.local_time == time(13, 0)
    assert publication.tz == NORDPOOL_MARKETS[AREA].tz


def test_a_user_can_correct_the_publication_clock(hass: HomeAssistant) -> None:
    """A market that moves its auction is Advanced configuration (D1 §6, D-0100)."""
    corrected = NordpoolActionSource(
        hass,
        config_entry_id=ENTRY_ID,
        area=AREA,
        currency="NOK",
        site_currency="NOK",
        tz=OSLO,
        publication_tz="Europe/Riga",
        publication_time="14:30",
    ).publication()

    assert corrected.tz == "Europe/Riga"
    assert corrected.local_time == time(14, 30)


# --------------------------------------------------------------------------- #
# the response → RawSlots
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-3")
async def test_the_response_action_is_called_read_only(
    hass: HomeAssistant, action: FakeAction, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """A `SupportsResponse.ONLY` action answers only a `return_response` call.

    Home Assistant refuses the call otherwise, so this passing is the runtime half
    of INV-3's amendment: the provider invokes a read-only action and nothing else
    (`design/DECISIONS.md` D-0080).
    """
    slots = await source(hass).fetch(TODAY)

    entries = format_fixture("nordpool_action_no3")["response"][AREA]
    assert len(slots) == len(entries) == HOURS_PER_DAY
    assert len(action.calls) == 1
    assert action.calls[0].return_response is True


async def test_the_price_is_per_mwh_and_becomes_per_kwh(
    hass: HomeAssistant, action: FakeAction, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """The action quotes per MWh; the core is handed major units per kWh (D1 §5.2)."""
    slots = await source(hass).fetch(TODAY)

    entries = format_fixture("nordpool_action_no3")["response"][AREA]
    assert slots[0].value == Decimal(str(entries[0]["price"])) / 1000
    assert slots[0].currency == "NOK"
    assert slots[0].source == "nordpool_action"


@pytest.mark.inv("INV-7")
async def test_slot_length_comes_from_the_entries(hass: HomeAssistant, action: FakeAction) -> None:
    """The action gives every entry an explicit end; nothing is assumed (INV-7)."""
    slots = await source(hass).fetch(TODAY)

    assert {slot.end - slot.start for slot in slots} == {timedelta(hours=1)}
    assert slots[0].start == at("2026-09-19 00:00")
    assert slots[-1].end == at("2026-09-20 00:00")


# --------------------------------------------------------------------------- #
# the error taxonomy (D1 §8)
# --------------------------------------------------------------------------- #


async def test_an_empty_area_list_means_nothing_published_yet(
    hass: HomeAssistant, action: FakeAction
) -> None:
    """`{area: []}` is the action's empty answer - retryable, not a parse error."""
    with pytest.raises(SourceEmptyError):
        await source(hass).fetch(date(2026, 9, 21))


async def test_no_nordpool_integration_is_unavailable_not_a_crash(hass: HomeAssistant) -> None:
    """Without the integration the action does not exist (D1 §8)."""
    with pytest.raises(SourceUnavailableError, match="not set up"):
        await source(hass).fetch(TODAY)


async def test_an_authentication_failure_is_not_retryable(hass: HomeAssistant) -> None:
    """The action's own `authentication_error` needs the user, not a backoff."""

    async def refuse(call: ServiceCall) -> ServiceResponse:
        raise ServiceValidationError(
            translation_domain=NORDPOOL_DOMAIN, translation_key="authentication_error"
        )

    hass.services.async_register(
        NORDPOOL_DOMAIN,
        SERVICE_GET_PRICES_FOR_DATE,
        refuse,
        supports_response=SupportsResponse.ONLY,
    )

    with pytest.raises(SourceAuthError) as raised:
        await source(hass).fetch(TODAY)
    assert raised.value.retryable is False


async def test_a_failed_fetch_is_reported_and_stores_nothing(
    hass: HomeAssistant, raw_store: InMemoryRawStore
) -> None:
    """`fetch_missing` records the failure instead of propagating it (D1 §8)."""
    report = await fetch_missing([source(hass)], raw_store, at("2026-09-19 13:30"), tz=OSLO)

    assert report.calls == 2
    assert len(report.failures) == 2
    assert all(isinstance(f.error, SourceUnavailableError) for f in report.failures)
    assert raw_store.count("nordpool_action") == 0
