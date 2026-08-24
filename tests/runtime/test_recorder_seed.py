"""The period and the baseline from the recorder, through the runtime.

The reference house's observe audit found a
site created on the 22nd pricing September on two days (F-5: 2–5 kW and 244 NOK
against the recorder's 5–10 kW and 416 NOK) and a baseline that was never seeded
(F-7: the startup guard `not baseline.state.bins` cannot be true). Everything
here runs a real Home Assistant with a real, in-memory recorder: the import
register's hourly statistics are imported as the recorder would have compiled
them, and the site is set up on top of them.

The register is the house's own kind - an AMS meter that reports once an hour,
at the boundary, the value *at* the boundary - so a statistics row filed under
hour S carries the register at S (`providers/meters/recorder.py`).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.powerplan import runtime as runtime_module
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.forecasts import Reconstruction
from custom_components.powerplan.core.loads.kinds.base import Role
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.runtime import Runtime, build_site
from tests.core.loads.conftest import floor_load
from tests.runtime.conftest import (
    IMPORT_REGISTER,
    SITE_ENTRY_ID,
    FakeFloor,
    FakeMeter,
    restart_entry,
    site_entry,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.components.recorder import Recorder
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.typing import RecorderInstanceContextManager

OSLO = ZoneInfo("Europe/Oslo")
#: The site is created on the 15th, mid-morning: two weeks of January already happened.
NOW = datetime(2026, 1, 15, 10, 20, tzinfo=UTC)
#: The recorder's history reaches back eight weeks: December is the previous period,
#: and the baseline's long-term half (older than ten days) holds six weeks of it.
FIRST_HOUR = datetime(2025, 11, 20, 0, 0, tzinfo=UTC)
#: The last hour the recorder has compiled at `NOW`: [09:00, 10:00) UTC.
LAST_HOUR = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
#: January, local: the open period.
PERIOD_START = datetime(2026, 1, 1, tzinfo=OSLO)

NIGHT_KWH = 0.5
DAY_KWH = 1.0
EVENING_KWH = 3.0
EVENING_HOUR = 17
#: January's three highest days (local 18:00–19:00), and one December hour higher
#: than any of them that must stay in December's period.
PEAKS = {
    datetime(2026, 1, 5, 18, tzinfo=OSLO): 9.0,
    datetime(2026, 1, 8, 18, tzinfo=OSLO): 8.5,
    datetime(2026, 1, 12, 18, tzinfo=OSLO): 8.0,
    datetime(2025, 12, 28, 18, tzinfo=OSLO): 12.0,
}


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder: RecorderInstanceContextManager) -> None:
    """Let the recorder's database fixture run before `hass` (the plugin's hook)."""


@pytest.fixture(autouse=True)
def quiet_sql() -> Generator[None]:
    """Keep the plugin's INFO-level SQL echo out of the run's output, teardown included."""
    logger = logging.getLogger("sqlalchemy.engine")
    level = logger.level
    logger.setLevel(logging.WARNING)
    yield
    logger.setLevel(level)


@pytest.fixture
async def oslo(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> datetime:
    """Put the site in Oslo's zone and the clock at `NOW`."""
    freezer.move_to(NOW)
    await hass.config.async_update(time_zone=OSLO.key)
    return NOW


def _hour_kwh(start: datetime, peaks: dict[datetime, float]) -> float:
    """Return what the house drew in the hour starting at `start` (UTC)."""
    for at, kwh in peaks.items():
        if at.astimezone(UTC) == start:
            return kwh
    hour = start.astimezone(OSLO).hour
    if hour == EVENING_HOUR:
        return EVENING_KWH
    return NIGHT_KWH if hour < 6 else DAY_KWH


def _import_register(hass: HomeAssistant, peaks: dict[datetime, float] = PEAKS) -> None:
    """Import the register's hourly `sum` rows as the recorder compiles them for an AMS meter.

    Latched: the row filed under hour S carries the register at S, so its `sum`
    is everything drawn before S.
    """
    rows: list[dict[str, Any]] = []
    total = 50_000.0
    start = FIRST_HOUR
    while start <= LAST_HOUR:
        rows.append({"start": start, "state": total, "sum": total})
        total += _hour_kwh(start, peaks)
        start += timedelta(hours=1)
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": IMPORT_REGISTER,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        rows,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> Runtime:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    runtime: Runtime = entry.runtime_data
    return runtime


def _state(hass: HomeAssistant, platform: str, key: str) -> Any:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, unique_id(SITE_ENTRY_ID, key)
    )
    assert entity_id is not None, key
    return hass.states.get(entity_id)


# --------------------------------------------------------------------------- #
# The period: F-5
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-11")
async def test_h3_a_site_created_mid_month_reports_the_month_s_level_from_the_recorder(
    recorder_mock: Recorder, hass: HomeAssistant, oslo: datetime
) -> None:
    """The level, the fee and the metric are January's own on the first day (D2 §2, §5.12)."""
    _import_register(hass)
    await async_wait_recording_done(hass)
    FakeMeter(hass)

    runtime = await _setup(hass, site_entry(hass))

    level = _state(hass, "sensor", "level")
    assert level.state == "5–10 kW"
    assert level.attributes["metric_kw"] == pytest.approx((9.0 + 8.5 + 8.0) / 3)
    # Tensio TS's H1 2026 sheet: 5–10 kW 371 NOK/month.
    assert level.attributes["fee"].startswith("371")
    history = runtime.build.tariff.history
    assert date(2025, 12, 28) not in history.days, "December is another period"
    # Every closed hour of January: the row filed under 09:00 closes 08:00–09:00.
    assert len(history.windows) == (LAST_HOUR - PERIOD_START).total_seconds() // 3600
    assert {rec.source for rec in history.windows.values()} == {"recorder"}
    assert history.seeded_from["recorder"] == "2026-01-15T08:00:00+00:00"
    # Nothing was steered before the site existed: the seeded month saves nothing.
    period = runtime.build.tariff.period(NOW)
    assert runtime.build.tariff.bill(period, history.counterfactual()).metric_kw == pytest.approx(
        runtime.build.tariff.bill(period).metric_kw
    )


async def test_16_an_override_survives_the_rebuild_button_which_reseeds_the_open_period(
    recorder_mock: Recorder, hass: HomeAssistant, oslo: datetime, hass_storage: dict[str, Any]
) -> None:
    """D2 §9 16 through the runtime: `button.<site>_rebuild_peak_history` (D8 §5.5)."""
    _import_register(hass)
    await async_wait_recording_done(hass)
    FakeMeter(hass)
    entry = site_entry(hass)
    er.async_get(hass).async_get_or_create(
        "button",
        DOMAIN,
        unique_id(SITE_ENTRY_ID, "rebuild_peak_history"),
        config_entry=entry,
        disabled_by=None,
    )
    runtime = await _setup(hass, entry)

    await hass.services.async_call(
        DOMAIN, "set_peak", {"date": "2026-01-08", "kw": 12.0, "note": "the bill"}, blocking=True
    )
    assert runtime.build.tariff.metric() == pytest.approx((12.0 + 9.0 + 8.0) / 3)

    # The recorder learns an evening the first seed did not have (a corrected statistic).
    later = {**PEAKS, datetime(2026, 1, 13, 18, tzinfo=OSLO): 9.6}
    _import_register(hass, later)
    await async_wait_recording_done(hass)
    button = _state(hass, "button", "rebuild_peak_history")
    await hass.services.async_call(
        "button", "press", {"entity_id": button.entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert runtime.build.tariff.metric() == pytest.approx((12.0 + 9.6 + 9.0) / 3)
    assert [item.key for item in runtime.build.tariff.history.overrides] == ["2026-01-08"]
    assert _state(hass, "sensor", "level").state == "10–15 kW"
    await runtime.store.flush()
    stored = hass_storage[f"{DOMAIN}.{SITE_ENTRY_ID}"]["data"]["tariff"]
    assert any(row["key"] == "2026-01-08" for row in stored["history"]["overrides"])


# --------------------------------------------------------------------------- #
# The baseline: F-7, D10 §9 5 through the runtime
# --------------------------------------------------------------------------- #


async def test_05_a_fresh_site_s_first_start_seeds_the_baseline_once(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    oslo: datetime,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A weekly profile from the register's statistics; a restart does not seed again (D10 §5.2)."""
    _import_register(hass)
    await async_wait_recording_done(hass)
    FakeMeter(hass)
    seeds: list[str] = []
    original = runtime_module.async_seed

    async def counting(*args: Any, **kwargs: Any) -> None:
        seeds.append(kwargs["register_entity_id"])
        await original(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "async_seed", counting)
    entry = site_entry(hass)
    runtime = await _setup(hass, entry)

    assert seeds == [IMPORT_REGISTER]
    adapter = runtime.forecasts_adapter
    assert adapter is not None
    baseline = adapter.baseline
    assert baseline.state.reconstruction is Reconstruction.FULL, "a site with no loads is exact"
    assert baseline.state.last_update is not None
    assert baseline.confidence(NOW) > 0.0
    evening = datetime(2026, 1, 13, EVENING_HOUR, 30, tzinfo=OSLO)
    night = datetime(2026, 1, 13, 3, 30, tzinfo=OSLO)
    assert baseline.predict(evening)[0] == pytest.approx(EVENING_KWH * 1000.0)
    assert baseline.predict(night)[0] == pytest.approx(NIGHT_KWH * 1000.0)
    stored = hass_storage[f"{DOMAIN}.{SITE_ENTRY_ID}"]["data"]["forecasts"]
    assert stored["baseline"]["reconstruction"] == "full"

    await restart_entry(hass, entry, hass_storage)
    again = entry.runtime_data.forecasts_adapter
    assert seeds == [IMPORT_REGISTER], "a learned baseline is not seeded twice"
    assert again is not None
    assert again.baseline.state.reconstruction is Reconstruction.FULL


async def test_05b_a_site_whose_loads_the_recorder_cannot_separate_is_seeded_and_says_none(
    recorder_mock: Recorder, hass: HomeAssistant, oslo: datetime
) -> None:
    """Nothing of the floor's own history is read, so nothing is subtracted: `none` (D-0214)."""
    _import_register(hass)
    await async_wait_recording_done(hass)
    FakeMeter(hass)
    floor = FakeFloor(hass)
    floor.register()
    entry = site_entry(hass)
    build = build_site(hass, entry)
    build.loads = (floor_load(strategy="always"),)
    build.devices = {"loop_bath": floor}
    runtime = Runtime(hass, entry, build)
    entry.runtime_data = runtime
    await runtime.start()
    await hass.async_block_till_done()

    adapter = runtime.forecasts_adapter
    assert adapter is not None
    assert adapter.baseline.state.reconstruction is Reconstruction.NONE
    assert adapter.baseline.confidence(NOW) > 0.0
    await runtime.stop("unload")


#: The floor's own power sensor, declared in kW as a charger's often is.
FLOOR_POWER = "sensor.floor_power"
#: What the floor drew in the evening hour, in kW: part of the register's 3 kWh.
FLOOR_EVENING_KW = 2.0


class MeteredFloor(FakeFloor):
    """The floor with its `POWER` role bound to a sensor the recorder keeps."""

    def entity_of(self, role: Role) -> str | None:
        """Return the power sensor for `POWER`, the climate entity otherwise."""
        return FLOOR_POWER if role is Role.POWER else super().entity_of(role)


def _import_floor_power(hass: HomeAssistant) -> None:
    """Import the floor's hourly `mean` rows in kW: 2 kW in the evening hour, else 0."""
    rows: list[dict[str, Any]] = []
    start = FIRST_HOUR
    while start <= LAST_HOUR:
        kw = FLOOR_EVENING_KW if start.astimezone(OSLO).hour == EVENING_HOUR else 0.0
        rows.append({"start": start, "mean": kw, "min": kw, "max": kw})
        start += timedelta(hours=1)
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": FLOOR_POWER,
            "unit_class": "power",
            "unit_of_measurement": "kW",
        },
        rows,
    )


async def test_05c_a_metered_load_s_own_history_is_taken_off_the_seed(
    recorder_mock: Recorder, hass: HomeAssistant, oslo: datetime
) -> None:
    """The house's 22:00 bin held the car it also plans (D-0483): the load's power comes off.

    In kW, converted to W by the recorder; each hourly mean integrated as the
    hour's own energy, not smeared into its neighbours.
    """
    _import_register(hass)
    _import_floor_power(hass)
    await async_wait_recording_done(hass)
    FakeMeter(hass)
    floor = MeteredFloor(hass)
    floor.register()
    entry = site_entry(hass)
    build = build_site(hass, entry)
    build.loads = (floor_load(strategy="always"),)
    build.devices = {"loop_bath": floor}
    runtime = Runtime(hass, entry, build)
    entry.runtime_data = runtime
    await runtime.start()
    await hass.async_block_till_done()

    adapter = runtime.forecasts_adapter
    assert adapter is not None
    baseline = adapter.baseline
    assert baseline.state.reconstruction is Reconstruction.FULL
    evening = datetime(2026, 1, 13, EVENING_HOUR, 30, tzinfo=OSLO)
    before = datetime(2026, 1, 13, EVENING_HOUR - 1, 30, tzinfo=OSLO)
    assert baseline.predict(evening)[0] == pytest.approx((EVENING_KWH - FLOOR_EVENING_KW) * 1000.0)
    assert baseline.predict(before)[0] == pytest.approx(DAY_KWH * 1000.0)
    await runtime.stop("unload")
