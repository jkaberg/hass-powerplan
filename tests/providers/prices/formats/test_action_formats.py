"""The two action-backed rows of D1 §2's table (D1 §9 item 1).

`tibber_action` and `energyzero_action` / `easyenergy_action` are rows whose
prices are not on an entity at all: the integration keeps them behind a response
action, registered `SupportsResponse.ONLY`. Registering the fake action the same
way is what proves the adapter passes `return_response=True` - Home Assistant
refuses a response-only action called any other way - which is the second half of
the INV-3 exemption these rows inherit (`design/DECISIONS.md` D-0080, D-0100).

The day asked for is a *local* day, so each test asserts on the 24 slots of
2026-09-19 out of the two days the fixture answers with, and on the slot lengths:
`energyzero` publishes `start` and `end`, `easyenergy` publishes neither, so its
slot length is the next start and nothing else (INV-7).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import ServiceCall, ServiceResponse, SupportsResponse

from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude, gaps
from custom_components.powerplan.providers.prices import (
    SourceDataError,
    SourceEmptyError,
    SourceParseError,
    SourceUnavailableError,
    formats,
)
from custom_components.powerplan.providers.prices.action import ActionSource

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

OSLO = ZoneInfo("Europe/Oslo")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

DAY = date_type(2026, 9, 19)
HOURS_PER_DAY = 24


@dataclass(frozen=True)
class Row:
    """One action-backed row of D1 §2's table."""

    key: str
    fixture: str
    options: Mapping[str, Any]
    site_currency: str
    tz: ZoneInfo
    market_tz: str
    first_start: str
    minutes: int


ROWS: tuple[Row, ...] = (
    Row(
        key="tibber_action",
        fixture="tibber_action",
        options={"currency": "NOK"},
        site_currency="NOK",
        tz=OSLO,
        market_tz="Europe/Oslo",
        first_start="2026-09-18T22:00:00+00:00",
        minutes=60,
    ),
    Row(
        key="energyzero_action",
        fixture="energyzero_action",
        options={"config_entry": "energyzero-entry-id"},
        site_currency="EUR",
        tz=AMSTERDAM,
        market_tz="Europe/Amsterdam",
        first_start="2026-09-18T22:00:00+00:00",
        minutes=60,
    ),
    Row(
        key="easyenergy_action",
        fixture="easyenergy_action",
        options={"config_entry": "easyenergy-entry-id"},
        site_currency="EUR",
        tz=AMSTERDAM,
        market_tz="Europe/Amsterdam",
        first_start="2026-09-18T22:00:00+00:00",
        minutes=60,
    ),
)

IDS = [row.key for row in ROWS]


class FakeAction:
    """One integration's price action, registered the way it registers it."""

    def __init__(self, service: str, response: dict[str, Any]) -> None:
        """Answer every call with `response`, and count the calls."""
        self.domain, self.service = service.split(".")
        self.response = response
        self.calls: list[ServiceCall] = []

    def register(self, hass: HomeAssistant) -> None:
        """Register the action as `SupportsResponse.ONLY`, as upstream does."""

        async def handler(call: ServiceCall) -> ServiceResponse:
            self.calls.append(call)
            return dict(self.response)

        hass.services.async_register(
            self.domain, self.service, handler, supports_response=SupportsResponse.ONLY
        )


def build(hass: HomeAssistant, row: Row, *, site_currency: str | None = None) -> ActionSource:
    """Build the row's adapter and wrap it in an `ActionSource` (D-0100)."""
    return ActionSource(
        hass,
        adapter=formats.build(row.key, dict(row.options)),
        site_currency=site_currency or row.site_currency,
        tz=row.tz,
    )


@pytest.fixture
def action(
    hass: HomeAssistant, request: pytest.FixtureRequest, format_fixture: Callable[[str], dict]
) -> FakeAction:
    """Register the fake action of the row under test."""
    row: Row = request.getfixturevalue("row")
    fixture = format_fixture(row.fixture)
    fake = FakeAction(fixture["service"], fixture["response"])
    fake.register(hass)
    return fake


# --------------------------------------------------------------------------- #
# what every action row must do
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("row", ROWS, ids=IDS)
async def test_an_action_row_returns_the_local_day_normalised(
    hass: HomeAssistant, row: Row, action: FakeAction
) -> None:
    """D1 §9 item 1, for the rows whose prices live behind an action."""
    slots = await build(hass, row).fetch(DAY)

    assert len(slots) == HOURS_PER_DAY
    assert slots[0].start.isoformat() == row.first_start
    assert {slot.currency for slot in slots} == {row.site_currency}
    assert {slot.source for slot in slots} == {row.key}
    assert gaps(slots) == ()
    assert action.calls, "the action was never called"


@pytest.mark.inv("INV-7")
@pytest.mark.parametrize("row", ROWS, ids=IDS)
async def test_an_action_row_takes_its_slot_length_from_the_response(
    hass: HomeAssistant, row: Row, action: FakeAction
) -> None:
    """A response without interval bounds still gets real slot lengths (INV-7)."""
    slots = await build(hass, row).fetch(DAY)

    assert {int((slot.end - slot.start).total_seconds() // 60) for slot in slots} == {row.minutes}
    assert action.calls[0].return_response is True


@pytest.mark.parametrize("row", ROWS, ids=IDS)
async def test_an_action_row_refuses_a_currency_that_is_not_the_site_s(
    hass: HomeAssistant, row: Row, action: FakeAction
) -> None:
    """Silently converting prices is worse than refusing them (D1 §5.2)."""
    with pytest.raises(SourceDataError):
        await build(hass, row, site_currency="XTS").fetch(DAY)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
async def test_an_action_row_publishes_in_its_market_s_timezone(
    hass: HomeAssistant, row: Row, action: FakeAction
) -> None:
    """Tomorrow is not asked for before the day-ahead auction (INV-6, D1 §5.1)."""
    source = build(hass, row)

    publication = source.publication()

    assert publication is not None
    assert publication.tz == row.market_tz
    assert publication.local_time.hour == 13
    assert source.native_unit() == (row.site_currency, EnergyUnit.KWH, Magnitude.MAJOR)
    assert source.entity_ids() == frozenset()


@pytest.mark.parametrize("row", ROWS, ids=IDS)
async def test_an_action_row_without_its_integration_is_unavailable(
    hass: HomeAssistant, row: Row
) -> None:
    """A missing integration is `SourceUnavailableError`, not a crash (D1 §8)."""
    with pytest.raises(SourceUnavailableError):
        await build(hass, row).fetch(DAY)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
async def test_an_action_row_with_nothing_published_is_empty(
    hass: HomeAssistant, row: Row, action: FakeAction, format_fixture: Callable[[str], dict]
) -> None:
    """Published nothing yet is retryable; a changed shape is not (D1 §8)."""
    empty = format_fixture(row.fixture)["response"]
    prices = empty["prices"]
    empty["prices"] = {home: [] for home in prices} if isinstance(prices, dict) else []
    action.response = empty

    with pytest.raises(SourceEmptyError):
        await build(hass, row).fetch(DAY)


@pytest.mark.parametrize("row", ROWS, ids=IDS)
async def test_an_action_row_fails_loudly_on_a_response_it_cannot_read(
    hass: HomeAssistant, row: Row, action: FakeAction
) -> None:
    """The payload's shape is asserted, never assumed (D1 §8)."""
    action.response = {"prices": "not a list of prices"}

    with pytest.raises(SourceParseError):
        await build(hass, row).fetch(DAY)


# --------------------------------------------------------------------------- #
# row-specific
# --------------------------------------------------------------------------- #


async def test_tibber_takes_the_home_it_was_configured_with(
    hass: HomeAssistant, format_fixture: Callable[[str], dict]
) -> None:
    """One Tibber account can hold two homes; the flow names which (D1 §2)."""
    fixture = format_fixture("tibber_action")
    homes = fixture["response"]["prices"]
    (only,) = homes
    fixture["response"]["prices"] = {only: homes[only], "Cabin": homes[only][:4]}
    FakeAction(fixture["service"], fixture["response"]).register(hass)

    source = ActionSource(
        hass,
        adapter=formats.build("tibber_action", {"currency": "NOK", "home": "Cabin"}),
        site_currency="NOK",
        tz=OSLO,
    )

    slots = await source.fetch(DAY)

    assert len(slots) == 4


async def test_tibber_refuses_to_guess_between_two_homes(
    hass: HomeAssistant, format_fixture: Callable[[str], dict]
) -> None:
    """Guessing which house's prices to plan on is worse than failing (D1 §8)."""
    fixture = format_fixture("tibber_action")
    homes = fixture["response"]["prices"]
    (only,) = homes
    fixture["response"]["prices"] = {only: homes[only], "Cabin": homes[only]}
    FakeAction(fixture["service"], fixture["response"]).register(hass)

    source = ActionSource(
        hass,
        adapter=formats.build("tibber_action", {"currency": "NOK"}),
        site_currency="NOK",
        tz=OSLO,
    )

    with pytest.raises(SourceParseError, match="Cabin"):
        await source.fetch(DAY)


async def test_energyzero_asks_only_for_the_day_it_needs(
    hass: HomeAssistant, format_fixture: Callable[[str], dict]
) -> None:
    """The action takes a window, and the window is the local day (D1 §5.1)."""
    fixture = format_fixture("energyzero_action")
    fake = FakeAction(fixture["service"], fixture["response"])
    fake.register(hass)
    source = ActionSource(
        hass,
        adapter=formats.build("energyzero_action", {"config_entry": "energyzero-entry-id"}),
        site_currency="EUR",
        tz=AMSTERDAM,
    )

    await source.fetch(DAY)

    call = fake.calls[0]
    assert call.data["config_entry"] == "energyzero-entry-id"
    assert call.data["start"] == "2026-09-19T00:00:00+02:00"
    assert call.data["end"] == "2026-09-20T00:00:00+02:00"
    assert call.data["incl_vat"] is True


async def test_easyenergy_prices_are_euro_per_kwh(
    hass: HomeAssistant, format_fixture: Callable[[str], dict]
) -> None:
    """The response is already major units per kWh, so nothing scales it."""
    fixture = format_fixture("easyenergy_action")
    FakeAction(fixture["service"], fixture["response"]).register(hass)
    source = ActionSource(
        hass,
        adapter=formats.build("easyenergy_action", {"config_entry": "easyenergy-entry-id"}),
        site_currency="EUR",
        tz=AMSTERDAM,
    )

    slots = await source.fetch(DAY)

    published = fixture["response"]["prices"][0]["price"]
    assert slots[0].value == Decimal(str(published))
