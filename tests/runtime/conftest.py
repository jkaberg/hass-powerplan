"""Fixtures for the site store's tests (D7 §9 10, 11).

D9 §3 draws no `tests/runtime/` directory. `storage.py` - and `runtime.py` after
it - is HA-side but is not a flow, so this is where its tests live (DECISIONS
D-0094).

The two halves of the store are driven differently, on purpose:

* the **read** side is real I/O. `SiteStore` reads the file itself (D-0091), so a
  v0 document, a corrupt file, and the name it is quarantined to, are planted and
  asserted on disk - which is why `hass_config_dir` is a temp directory here.
* the **write** side goes through `Store.async_save`, which the `hass` fixture's
  `hass_storage` mock keeps in memory after a real JSON round-trip. `document`
  reads that, and `saves` counts it.

Time is driven by `advance()`: under the `freezer` the loop's monotonic clock is
frozen with the wall clock, so a tick plus `async_fire_time_changed_exact` runs
exactly the timers that have come due and nothing else.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed_exact,
)

from custom_components.powerplan.const import (
    CONF_ACTIVE,
    CONF_CURRENCY,
    CONF_ELECTRICAL,
    CONF_METER,
    CONF_NAME,
    CONF_PATH,
    CONF_PRESENCE,
    CONF_PRICES,
    CONF_TARIFF,
    CONF_TIMEZONE,
    CONF_TIMEZONE_SOURCE,
    DOMAIN,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
    TIMEZONE_FROM_HASS,
)
from custom_components.powerplan.core.loads.kinds.base import Role
from custom_components.powerplan.core.tariffs.presets import loader
from custom_components.powerplan.storage import SiteStore
from custom_components.powerplan.writegate import DeviceCall
from tests.core.loads.conftest import reads as load_reads

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator, Mapping

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import ServiceCall

    from custom_components.powerplan.core.loads.kinds.base import Write
ENTRY_ID = "01JSITE0STORE0TEST"
STORE_KEY = f"{DOMAIN}.{ENTRY_ID}"

FIXTURES = Path(__file__).parents[1] / "fixtures" / "store"


async def advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Move the clock `seconds` forward and run whatever fell due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed_exact(hass)
    await hass.async_block_till_done()


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Give each test its own config directory: the store file here is real."""
    return hass_tmp_config_dir


@pytest.fixture
def start(freezer: FrozenDateTimeFactory) -> datetime:
    """Freeze the clock at a known instant; `advance()` moves it."""
    moment = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    freezer.move_to(moment)
    return moment


@pytest.fixture
def store_path(hass: HomeAssistant) -> Path:
    """Where this site's store file lives, with `.storage/` guaranteed to exist."""
    path = Path(hass.config.path(".storage", STORE_KEY))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def plant(store_path: Path) -> Callable[[Mapping[str, Any] | str], None]:
    """Write a store file before the site loads: a document, or raw text."""

    def write(content: Mapping[str, Any] | str) -> None:
        if isinstance(content, str):
            store_path.write_text(content, encoding="utf-8")
            return
        envelope = {
            "version": 1,
            "minor_version": 1,
            "key": STORE_KEY,
            "data": content,
        }
        store_path.write_text(json.dumps(envelope), encoding="utf-8")

    return write


@pytest.fixture
def v0_document() -> dict[str, Any]:
    """Return the committed v0 fixture's `data`: no schema, old `meter` shape."""
    envelope = json.loads((FIXTURES / "v0_site.json").read_text(encoding="utf-8"))
    return dict(envelope["data"])


@pytest.fixture
def saves(hass: HomeAssistant) -> Generator[list[dict[str, Any]]]:
    """Every document this site handed to `Store.async_save`, in order."""
    written: list[dict[str, Any]] = []
    original = Store.async_save

    async def counting_save(self: Store[Any], data: Any) -> None:
        if self.key == STORE_KEY:
            written.append(deepcopy(data))
        await original(self, data)

    with patch.object(Store, "async_save", counting_save):
        yield written


@pytest.fixture
def document(hass_storage: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    """Return the last document written, as the mock JSON round-tripped it."""

    def read() -> dict[str, Any]:
        return dict(hass_storage[STORE_KEY]["data"])

    return read


@pytest.fixture
async def store(hass: HomeAssistant) -> AsyncGenerator[SiteStore]:
    """Yield a `SiteStore` for one site, closed after as `async_unload_entry` does."""
    site = SiteStore(hass, ENTRY_ID)
    yield site
    await site.close()


# --------------------------------------------------------------------------- #
# The runtime's tests (D7 §9 4, 6, 7, 8, 14): a site entry, a fake meter, a fake floor
# --------------------------------------------------------------------------- #

GRID_POWER = "sensor.grid_power"
IMPORT_REGISTER = "sensor.import_register"
FLOOR_CLIMATE = "climate.floor_bath"
SITE_ENTRY_ID = "01JSITE0RUNTIME0TEST"


def site_data(
    hass: HomeAssistant, *, meter: bool = True, tariff: str | None = "no/tensio"
) -> dict[str, Any]:
    """Return `entry.data` as the site flow materialises it (D8 §4), for one test site."""
    tariff_data: dict[str, Any] = (
        {
            "preset_id": "no/tensio",
            "preset_file": tariff,
            "version_ids": [version.version_id for version in loader.load(tariff).versions],
            "target": "auto",
            "target_kw": None,
            "risk": 0.0,
            "eps_kwh": 0.3,
            "cap_margin_kw": 0.5,
        }
        if tariff
        else {"preset_id": "no_peak", "preset_file": None, "version_ids": []}
    )
    return {
        CONF_PATH: "full" if meter else "price_only",
        CONF_NAME: "Test site",
        CONF_TIMEZONE: hass.config.time_zone,
        CONF_TIMEZONE_SOURCE: TIMEZONE_FROM_HASS,
        CONF_CURRENCY: "NOK",
        CONF_ELECTRICAL: {"country": "NO", "system": "it_230", "phases": 3, "main_fuse_a": 63.0},
        CONF_METER: (
            {
                "source": "ha_sensors",
                "device_id": None,
                "roles": {ROLE_GRID_POWER: GRID_POWER, ROLE_IMPORT_REGISTER: IMPORT_REGISTER},
            }
            if meter
            else {}
        ),
        CONF_PRICES: {
            "sources": [{"key": "fixed", "options": {"price": "0.50", "currency": "NOK"}}],
            "modifiers": [],
            "export": {"mode": "none"},
            "carriers": [],
        },
        CONF_TARIFF: tariff_data,
        "hard_limits": {"contracted_kw": 25.0},
        CONF_PRESENCE: {"mode": "manual", "persons": [], "away_delay_min": 30},
        "notifications": {},
        "quiet_hours": {},
        CONF_ACTIVE: True,
    }


def site_entry(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    """Return a site entry added to `hass`, not yet set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test site",
        entry_id=SITE_ENTRY_ID,
        data=site_data(hass, **overrides),
    )
    entry.add_to_hass(hass)
    return entry


class FakeMeter:
    """The grid meter as two HA sensors: signed watts and a cumulative register."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Publish the first reading."""
        self.hass = hass
        self.register_kwh = 100_000.0
        self.set_power(1_500.0)
        self.report_register()

    def set_power(self, watts: float) -> None:
        """Publish a new grid power reading."""
        self.hass.states.async_set(
            GRID_POWER,
            f"{watts:.0f}",
            {"unit_of_measurement": "W", "device_class": "power", "state_class": "measurement"},
        )

    def report_register(self, delta_kwh: float = 0.0) -> None:
        """Publish the import register, as the AMS meter does on the hour."""
        self.register_kwh += delta_kwh
        self.hass.states.async_set(
            IMPORT_REGISTER,
            f"{self.register_kwh:.3f}",
            {
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
            },
        )


@dataclass
class FakeFloor:
    """A floor thermostat behind `climate.set_temperature`, as the runtime sees it."""

    hass: HomeAssistant
    temp_c: float = 23.5
    setpoint_c: float = 24.0
    seen: list[tuple[str, float]] = field(default_factory=list)

    def register(self) -> None:
        """Register the service the write goes through and publish the first state."""
        self.hass.services.async_register("climate", "set_temperature", self._set_temperature)
        self._publish()

    def _publish(self) -> None:
        self.hass.states.async_set(
            FLOOR_CLIMATE,
            "heat",
            {"temperature": self.setpoint_c, "current_temperature": self.temp_c},
        )

    async def _set_temperature(self, call: ServiceCall) -> None:
        self.setpoint_c = float(call.data["temperature"])
        self.seen.append((str(call.data["entity_id"]), self.setpoint_c))
        self._publish()

    # -- `LoadDevice` --------------------------------------------------------- #

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """The one climate entity."""
        return (FLOOR_CLIMATE,)

    def reads(self, now: datetime) -> Any:
        """Return what the thermostat says."""
        return load_reads(
            now, numbers={Role.TEMP: self.temp_c, Role.SETPOINT: self.setpoint_c, Role.POWER: 0.0}
        )

    def call_for(self, write: Write) -> DeviceCall | None:
        """Map a setpoint write to `climate.set_temperature`; nothing else is bound."""
        if write.role is not Role.SETPOINT:
            return None
        return DeviceCall(
            "climate", "set_temperature", FLOOR_CLIMATE, {"temperature": float(write.value)}
        )
