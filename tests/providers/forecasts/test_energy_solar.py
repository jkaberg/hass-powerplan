"""D10 §9 17–18 - `energy_solar`, the PV forecast the Energy dashboard already draws.

The three payloads under `tests/fixtures/energy_solar/` are written from each
integration's `energy.py` (the `source` key says which): Forecast.Solar and
Open-Meteo Solar hourly in the site's zone, Solcast half-hourly in UTC. The
energy manager and `async_get_energy_platforms` are Home Assistant's internal
API, so they are replaced here by fakes of the same shape; the config entries
are real.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.energy import data as energy_data
from homeassistant.components.energy import websocket_api as energy_ws
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.core.forecasts.model import ForecastKind
from custom_components.powerplan.providers.forecasts.base import ForecastUnavailableError
from custom_components.powerplan.providers.forecasts.energy_solar import (
    FAR_CONFIDENCE,
    NEAR_CONFIDENCE,
    EnergySolarSource,
    SolarForecastUnavailableError,
    async_solar_forecast_entries,
)
from tests.providers.conftest import load_json

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

NOW = datetime(2026, 6, 21, 2, 0, tzinfo=UTC)
HORIZON = timedelta(hours=48)


class FakeManager:
    """The energy manager: its preferences, and the listeners it keeps."""

    def __init__(self, sources: list[dict[str, Any]]) -> None:
        """Hold `energy_sources` as the Energy preferences store them."""
        self.data: dict[str, Any] | None = {"energy_sources": sources}
        self.listeners: list[Callable[[], Awaitable[None]]] = []

    def async_listen_updates(self, listener: Callable[[], Awaitable[None]]) -> None:
        """Keep `listener`, as the manager does, with no way to remove it."""
        self.listeners.append(listener)


def install(
    monkeypatch: pytest.MonkeyPatch,
    manager: FakeManager,
    payloads: dict[str, dict[str, Any] | None],
) -> list[str]:
    """Replace the energy manager and the platforms; return the entries each platform was asked."""
    asked: list[str] = []

    async def get_manager(hass: HomeAssistant) -> FakeManager:
        del hass
        return manager

    def platform(domain: str) -> Callable[[HomeAssistant, str], Awaitable[dict[str, Any] | None]]:
        async def forecast(hass: HomeAssistant, entry_id: str) -> dict[str, Any] | None:
            del hass
            asked.append(entry_id)
            return payloads.get(entry_id)

        del domain
        return forecast

    async def get_platforms(hass: HomeAssistant) -> dict[str, Any]:
        del hass
        return {domain: platform(domain) for domain in DOMAINS}

    monkeypatch.setattr(energy_data, "async_get_manager", get_manager)
    monkeypatch.setattr(energy_ws, "async_get_energy_platforms", get_platforms)
    return asked


DOMAINS = ("forecast_solar", "solcast_solar", "open_meteo_solar_forecast")


def solar(*entries: str) -> dict[str, Any]:
    """Return one solar source of the Energy preferences, with its forecast entries."""
    return {
        "type": "solar",
        "stat_energy_from": "sensor.pv_energy",
        "config_entry_solar_forecast": list(entries),
    }


def entry(hass: HomeAssistant, domain: str, entry_id: str) -> str:
    """Add a config entry of a forecast integration and return its id."""
    MockConfigEntry(domain=domain, entry_id=entry_id, title=domain).add_to_hass(hass)
    return entry_id


def payload(domain: str) -> dict[str, Any]:
    """Return the fixture's `wh_hours` payload, without its `source` note."""
    return {"wh_hours": load_json("energy_solar", f"{domain}.json")["wh_hours"]}


def watts_at(points: Any, at: datetime) -> float | None:
    """Return the series' average W for the period `at` falls in, or `None` for a hole."""
    for point in points:
        if point.start <= at < point.end:
            return float(point.value)
    return None


@pytest.mark.parametrize(
    ("domain", "at", "watts"),
    [
        # 12 Wh from sunrise 04:12 to 05:00 local: 48 minutes, 15 W.
        ("forecast_solar", datetime(2026, 6, 21, 2, 30, tzinfo=UTC), 15.0),
        # 1 830 Wh over 08:00–09:00 local.
        ("forecast_solar", datetime(2026, 6, 21, 6, 30, tzinfo=UTC), 1830.0),
        # 0.9 kW × 500 = 450 Wh over half an hour: 900 W.
        ("solcast_solar", datetime(2026, 6, 21, 4, 45, tzinfo=UTC), 900.0),
        # 1 750 Wh over 08:00–09:00 local.
        ("open_meteo_solar_forecast", datetime(2026, 6, 21, 6, 0, tzinfo=UTC), 1750.0),
    ],
)
async def test_17_each_integration_s_payload_becomes_average_watts(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    domain: str,
    at: datetime,
    watts: float,
) -> None:
    """Hourly and half-hourly periods, local or UTC keys: Wh over the period's hours."""
    entry_id = entry(hass, domain, f"{domain}_1")
    install(monkeypatch, FakeManager([solar(entry_id)]), {entry_id: payload(domain)})

    entries = await async_solar_forecast_entries(hass)
    series = await EnergySolarSource(hass, entries=entries).fetch(HORIZON, NOW)

    assert series.kind is ForecastKind.PRODUCTION
    assert series.unit == "W"
    assert watts_at(series.points, at) == pytest.approx(watts)


async def test_17_the_last_period_takes_the_gap_before_it(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Solcast's last key opens a half hour like the rest; Forecast.Solar's an hour."""
    ids = [entry(hass, domain, f"{domain}_1") for domain in ("solcast_solar", "forecast_solar")]
    for entry_id, domain, length in zip(
        ids, ("solcast_solar", "forecast_solar"), (30, 60), strict=True
    ):
        install(monkeypatch, FakeManager([solar(entry_id)]), {entry_id: payload(domain)})
        series = await EnergySolarSource(hass, entries=(entry_id,)).fetch(HORIZON, NOW)
        last = series.points[-1]
        assert last.end - last.start == timedelta(minutes=length)


async def test_17_two_entries_on_one_source_sum_and_an_uncovered_stretch_is_a_hole(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """East and west planes add up; where no entry says anything there is no point, never 0."""
    east = entry(hass, "forecast_solar", "east")
    west = entry(hass, "forecast_solar", "west")
    shed = entry(hass, "open_meteo_solar_forecast", "shed")
    install(
        monkeypatch,
        FakeManager([solar(east, west), solar(shed)]),
        {
            east: {
                "wh_hours": {"2026-06-21T05:00:00+00:00": 1000, "2026-06-21T06:00:00+00:00": 1200}
            },
            west: {
                "wh_hours": {"2026-06-21T05:00:00+00:00": 300, "2026-06-21T06:00:00+00:00": 400}
            },
            shed: {"wh_hours": {"2026-06-21T09:00:00+00:00": 90, "2026-06-21T10:00:00+00:00": 80}},
        },
    )

    series = await EnergySolarSource(hass, entries=await async_solar_forecast_entries(hass)).fetch(
        HORIZON, NOW
    )

    assert watts_at(series.points, datetime(2026, 6, 21, 5, 30, tzinfo=UTC)) == 1300.0
    assert watts_at(series.points, datetime(2026, 6, 21, 6, 30, tzinfo=UTC)) == 1600.0
    # 07:00–09:00: no entry covers it.
    assert watts_at(series.points, datetime(2026, 6, 21, 8, 0, tzinfo=UTC)) is None
    assert watts_at(series.points, datetime(2026, 6, 21, 9, 30, tzinfo=UTC)) == 90.0
    assert watts_at(series.points, datetime(2026, 6, 21, 12, 0, tzinfo=UTC)) is None


async def test_17_a_forecast_within_a_day_is_more_certain_than_one_beyond(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The platform publishes no confidence: 0.7 for the first 24 h, 0.5 after (D10 §5.5)."""
    entry_id = entry(hass, "forecast_solar", "fs")
    install(
        monkeypatch,
        FakeManager([solar(entry_id)]),
        {
            entry_id: {
                "wh_hours": {
                    (NOW + timedelta(hours=10)).isoformat(): 500,
                    (NOW + timedelta(hours=30)).isoformat(): 500,
                }
            }
        },
    )

    series = await EnergySolarSource(hass, entries=(entry_id,)).fetch(HORIZON, NOW)

    assert [point.confidence for point in series.points] == [NEAR_CONFIDENCE, FAR_CONFIDENCE]


async def test_17_no_solar_source_in_the_energy_preferences_yields_no_entries(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grid-only dashboard, or a solar source without a forecast, names nothing to ask."""
    asked = install(
        monkeypatch,
        FakeManager([{"type": "grid", "flow_from": []}, solar()]),
        {},
    )

    assert await async_solar_forecast_entries(hass) == ()
    assert asked == []


async def test_17_a_forecast_entry_that_answers_nothing_is_no_series(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An integration still loading answers `None`: unavailable, not a zero forecast."""
    entry_id = entry(hass, "solcast_solar", "sc")
    install(monkeypatch, FakeManager([solar(entry_id)]), {entry_id: None})

    with pytest.raises(ForecastUnavailableError):
        await EnergySolarSource(hass, entries=(entry_id,)).fetch(HORIZON, NOW)


async def test_18_a_missing_platform_api_is_solar_forecast_unavailable(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`async_get_energy_platforms` gone from Home Assistant: a typed error, never a crash."""
    entry_id = entry(hass, "forecast_solar", "fs")
    install(monkeypatch, FakeManager([solar(entry_id)]), {entry_id: payload("forecast_solar")})
    monkeypatch.delattr(energy_ws, "async_get_energy_platforms")

    with pytest.raises(SolarForecastUnavailableError):
        await EnergySolarSource(hass, entries=(entry_id,)).fetch(HORIZON, NOW)


async def test_18_a_changed_platform_signature_is_solar_forecast_unavailable(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform function that takes other arguments now: the same typed error."""
    entry_id = entry(hass, "forecast_solar", "fs")
    install(monkeypatch, FakeManager([solar(entry_id)]), {})

    async def changed(hass: HomeAssistant) -> dict[str, Any]:
        del hass

        async def forecast(hass: HomeAssistant, entry_id: str, horizon: int) -> None:
            del hass, entry_id, horizon

        return {"forecast_solar": forecast}

    monkeypatch.setattr(energy_ws, "async_get_energy_platforms", changed)

    with pytest.raises(SolarForecastUnavailableError):
        await EnergySolarSource(hass, entries=(entry_id,)).fetch(HORIZON, NOW)
