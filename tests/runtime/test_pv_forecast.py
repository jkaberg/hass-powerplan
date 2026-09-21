"""D7 §9 20, D10 §9 18 - the runtime reads the PV forecast the Energy dashboard names.

The real site entry, with the energy manager and `async_get_energy_platforms`
replaced by the fakes of `tests/providers/forecasts/test_energy_solar.py`:

- a site whose Energy preferences have no solar source calls no energy platform
  and has no production series, and its plan says `null`, never 0 (D10 §2);
- the forecast is fetched at start, again only after an hour, and at once when
  the Energy preferences change, each time outside the lock (D7 §5.2, §5.3);
- a platform API that is gone raises `pv_forecast_unavailable`, and the tick and
  the plan run on without a PV forecast (D10 §9 18).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components.energy import websocket_api as energy_ws
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir

from custom_components.powerplan.const import CONF_METER, DOMAIN, ROLE_PRODUCTION_POWER
from tests.providers.forecasts.test_energy_solar import FakeManager, entry, install, solar
from tests.runtime.conftest import SITE_ENTRY_ID, FakeMeter, advance, site_entry

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime

NOW = datetime(2026, 6, 21, 8, 0, tzinfo=UTC)
ISSUE = f"{SITE_ENTRY_ID}_pv_forecast_unavailable"


def _sunny(wh: float = 2000.0) -> dict[str, Any]:
    """Return a `wh_hours` payload of `wh` every hour, a day either side of `NOW`.

    The test site's fixed price has day-long slots; each is sampled at its start.
    """
    return {
        "wh_hours": {(NOW + timedelta(hours=offset)).isoformat(): wh for offset in range(-24, 72)}
    }


async def _setup(hass: HomeAssistant) -> Runtime:
    site = site_entry(hass)
    assert await hass.config_entries.async_setup(site.entry_id)
    await hass.async_block_till_done()
    assert site.state is ConfigEntryState.LOADED
    runtime: Runtime = site.runtime_data
    return runtime


async def test_20_a_site_without_solar_sources_calls_no_energy_platform(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A grid-only Energy dashboard: no platform call, no series, `null` in the plan."""
    freezer.move_to(NOW)
    FakeMeter(hass)
    install(monkeypatch, FakeManager([{"type": "grid", "flow_from": []}]), {})
    looked: list[bool] = []
    real = energy_ws.async_get_energy_platforms

    async def counted(hass: HomeAssistant) -> dict[str, Any]:
        looked.append(True)
        return await real(hass)

    monkeypatch.setattr(energy_ws, "async_get_energy_platforms", counted)

    runtime = await _setup(hass)

    assert looked == []
    assert runtime._production_series is None
    assert runtime.plans > 0
    assert runtime.plan_slots
    assert {slot["production_kwh"] for slot in runtime.plan_slots} == {None}
    assert {slot["surplus_kwh"] for slot in runtime.plan_slots} == {None}


async def test_20_the_forecast_refreshes_hourly_and_on_a_preferences_change_outside_the_lock(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fetched at start, not again within the hour, again after it, and at once on a change."""
    freezer.move_to(NOW)
    FakeMeter(hass)
    pv = entry(hass, "forecast_solar", "fs")
    manager = FakeManager([solar(pv)])
    asked = install(monkeypatch, manager, {pv: _sunny()})
    locked: list[bool] = []
    real = energy_ws.async_get_energy_platforms

    async def watched(hass: HomeAssistant) -> dict[str, Any]:
        site = hass.config_entries.async_get_entry(SITE_ENTRY_ID)
        assert site is not None
        locked.append(site.runtime_data.lock.locked())
        return await real(hass)

    monkeypatch.setattr(energy_ws, "async_get_energy_platforms", watched)

    runtime = await _setup(hass)
    assert asked == [pv]
    assert runtime._production_series is not None
    now_slot = runtime.plan_slots[0]
    hours = (
        datetime.fromisoformat(now_slot["end"]) - datetime.fromisoformat(now_slot["start"])
    ).total_seconds() / 3600.0
    assert now_slot["production_kwh"] == pytest.approx(2.0 * hours)
    assert 0.0 <= now_slot["surplus_kwh"] <= now_slot["production_kwh"]

    await advance(hass, freezer, timedelta(minutes=30).total_seconds())
    assert asked == [pv]

    await advance(hass, freezer, timedelta(minutes=31).total_seconds())
    assert asked == [pv, pv]

    (listener,) = manager.listeners
    plans = runtime.plans
    await listener()
    assert asked == [pv, pv, pv]
    assert runtime.plans == plans + 1
    assert locked == [False, False, False]


async def test_18_a_missing_platform_api_raises_the_repair_and_the_site_runs_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `async_get_energy_platforms`: a repair, no series; ticks and plans go on."""
    freezer.move_to(NOW)
    FakeMeter(hass)
    pv = entry(hass, "forecast_solar", "fs")
    manager = FakeManager([solar(pv)])
    install(monkeypatch, manager, {pv: _sunny()})
    real = energy_ws.async_get_energy_platforms
    monkeypatch.delattr(energy_ws, "async_get_energy_platforms")

    runtime = await _setup(hass)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert runtime._production_series is None
    ticks, plans = runtime.ticks, runtime.plans
    await advance(hass, freezer, timedelta(minutes=15).total_seconds())
    assert runtime.ticks > ticks
    assert runtime.plans > plans

    monkeypatch.setattr(energy_ws, "async_get_energy_platforms", real, raising=False)
    (listener,) = manager.listeners
    await listener()

    assert runtime._production_series is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE) is None


PV_POWER = "sensor.pv_power"


async def test_d5_20_production_30_percent_off_its_forecast_for_15_min_replans_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured 1 kW against a 2 kW forecast: one `forecast` replan after 15 min, not two.

    D5 §5.9, §9 20 (Phase 7). The episode ends when the reading agrees again; a
    new miss after that is a new episode and replans once more.
    """
    freezer.move_to(NOW)
    FakeMeter(hass)
    hass.states.async_set(PV_POWER, "1000", {"unit_of_measurement": "W", "device_class": "power"})
    pv = entry(hass, "forecast_solar", "fs")
    install(monkeypatch, FakeManager([solar(pv)]), {pv: _sunny()})
    site = site_entry(hass)
    data = dict(site.data)
    data[CONF_METER] = {
        **data[CONF_METER],
        "roles": {**data[CONF_METER]["roles"], ROLE_PRODUCTION_POWER: PV_POWER},
    }
    hass.config_entries.async_update_entry(site, data=data)
    assert await hass.config_entries.async_setup(site.entry_id)
    await hass.async_block_till_done()
    runtime: Runtime = site.runtime_data
    triggers: list[str] = []
    run_plan = runtime.run_plan

    async def spy(trigger: str) -> None:
        triggers.append(trigger)
        await run_plan(trigger)

    monkeypatch.setattr(runtime, "run_plan", spy)

    async def minutes(count: int) -> None:
        for _ in range(count * 2):
            await advance(hass, freezer, 30.0)

    await minutes(14)
    assert "forecast" not in triggers
    await minutes(2)
    assert triggers.count("forecast") == 1
    await minutes(20)
    assert triggers.count("forecast") == 1

    hass.states.async_set(PV_POWER, "2000", {"unit_of_measurement": "W", "device_class": "power"})
    await minutes(1)
    hass.states.async_set(PV_POWER, "900", {"unit_of_measurement": "W", "device_class": "power"})
    await minutes(16)
    assert triggers.count("forecast") == 2
