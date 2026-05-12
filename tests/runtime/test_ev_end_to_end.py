"""WP2.3 - the first controlled load, end to end in Home Assistant: `deadline_fill` on an Easee.

No subentry flow yet: the load is put on the `SiteBuild` by hand, the
way the runtime tests do, but everything else is real - the Easee-shaped
charger of `tests/e2e/fake_house.py` behind its entities, the `easee_ble`
profile matching and binding them off the registries, `LiveDevice` reading them
every tick, the household plugging the car in, the engine's `ev_connected`
edge, the plan the runtime cuts on it, and the WriteGate's `number.set_value`
and `switch.turn_on` arriving on the charger with `blocking=True`.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceRegistry, callback
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import events
from custom_components.powerplan.const import (
    CONF_METER,
    DOMAIN,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
)
from custom_components.powerplan.core.engine import EventKind
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.providers.profiles import easee_ble
from custom_components.powerplan.providers.profiles.base import DeviceView, LiveDevice
from custom_components.powerplan.runtime import Runtime, build_site
from tests.builders.houses import OSLO, nordic_detached
from tests.e2e.fake_house import GRID_POWER, IMPORT_REGISTER, FakeHouse
from tests.flows.conftest import ams_meter  # noqa: F401 - the captured AMS meter's entities
from tests.runtime.conftest import SITE_ENTRY_ID, site_data
from tests.scenarios.runner import TICK_S

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import Event

#: A winter weekday afternoon of the benchmark year; the household comes home
#: around 16:30 and plugs the car in (D9 §5.9).
START = datetime.combine(datetime(2027, 1, 12).date(), time(15, 40, 17), tzinfo=OSLO)
SPAN = timedelta(hours=1, minutes=40)
SEED = 20260919
#: The car comes home under the 20 % floor: the first minutes charge whatever the
#: price says (D4 §5.11 `MIN_SOC`) and the plan governs what follows.
ARRIVAL_SOC = 0.10


async def _site(hass: HomeAssistant) -> MockConfigEntry:
    """Return a metered site on the captured AMS entities, the fixed price, Tensio at 10 kW.

    A kW target rather than `auto`: with no month history the automatic step is
    the lowest one, under which a January house alone is over the ceiling and the
    car would only ever be shed.
    """
    data = site_data(hass, target_kw=10.0)
    data[CONF_METER]["roles"] = {ROLE_GRID_POWER: GRID_POWER, ROLE_IMPORT_REGISTER: IMPORT_REGISTER}
    entry = MockConfigEntry(domain=DOMAIN, title="Test site", entry_id=SITE_ENTRY_ID, data=data)
    entry.add_to_hass(hass)
    return entry


@pytest.mark.inv("INV-20")
@pytest.mark.inv("INV-24")
@pytest.mark.usefixtures("ams_meter")
async def test_the_car_is_planned_and_steered_through_the_charger(  # noqa: PLR0915 - one afternoon, its assertions in order
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plug-in → `ev_connected` → a `deadline_fill` plan → amps written to the Easee, blocking."""
    freezer.move_to(START)
    hass.loop.slow_callback_duration = 86_400.0
    await hass.config.async_update(time_zone=OSLO.key)
    house = nordic_detached(seed=SEED, controlled=frozenset({"ev"}), ev_soc=ARRIVAL_SOC)
    assert house.ev is not None
    house.ev.plugged = False  # the car is out until the household comes home
    fake = FakeHouse(hass, house, START)
    fake.install()
    await hass.async_block_till_done()

    # The profile finds the charger on the registries and binds its roles.
    charger = fake.loads["ev"]
    assert charger.device_id is not None
    view = DeviceView.from_hass(hass, charger.device_id)
    match = easee_ble.EaseeBle().match(view)
    assert match.claimed, match.reasons
    assert not match.missing, match.missing
    device = LiveDevice(hass, charger.device_id, easee_ble.EaseeBle().bind(match.bindings))
    assert {Role.CURRENT_SET, Role.ENABLE, Role.STATUS, Role.POWER} <= set(device.bound.bindings)

    # Every service call the gate makes is recorded with its `blocking` flag.
    # `ServiceRegistry` has slots, so the class method is wrapped, not the instance.
    calls: list[tuple[str, str, dict[str, Any], bool]] = []
    original = ServiceRegistry.async_call

    async def spy(
        registry: ServiceRegistry,
        domain: str,
        service: str,
        service_data: Any = None,
        blocking: bool = False,
        **kwargs: Any,
    ) -> Any:
        if domain in ("number", "switch"):
            calls.append((domain, service, dict(service_data or {}), blocking))
        return await original(registry, domain, service, service_data, blocking=blocking, **kwargs)

    monkeypatch.setattr(ServiceRegistry, "async_call", spy)

    seen: list[Event[Any]] = []

    @callback
    def record(event: Event[Any]) -> None:
        seen.append(event)

    hass.bus.async_listen(events.event_name(EventKind.EV_CONNECTED), record)

    entry = await _site(hass)
    build = build_site(hass, entry)
    build.loads = (house.load("ev"),)
    build.devices = {"ev": device}
    runtime = Runtime(hass, entry, build)
    entry.runtime_data = runtime
    await runtime.start()
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED or runtime.snapshot is not None

    for _ in range(int(SPAN.total_seconds() / TICK_S)):
        await fake.advance(freezer)
    await hass.async_block_till_done()

    # The household plugged the car in and the engine said so, once.
    plugged = [event for event in seen if event.data["connected"] is True]
    assert len(plugged) == 1, [dict(event.data) for event in seen]
    assert plugged[0].data["load"] == "ev"
    assert house.ev.plugged is True

    # The plan came on the edge and is deadline_fill's.
    plan = runtime.state.plans.plans.get("ev")
    assert plan is not None, runtime.snapshot.reasons if runtime.snapshot else None
    assert plan.strategy == "deadline_fill"
    assert runtime.snapshot is not None
    ev = runtime.snapshot.loads["ev"]
    assert ev.health.failures == 0
    assert ev.demand.wants is True

    # Writes reached the charger through the gate, every one of them blocking (INV-20, INV-24).
    assert calls, "nothing was written to the charger"
    assert all(blocking for _d, _s, _data, blocking in calls), calls
    arrived = list(fake.calls)
    amps = [(at, data["value"]) for at, domain, _s, _e, data in arrived if domain == "number"]
    assert amps, arrived
    assert any(6.0 <= value <= 32.0 for _at, value in amps), amps
    # A zero is only ever the deliberate pause - switch off in the same breath -
    # never a bare 0 A the car would read as a stop (D4 §5.11, INV-28).
    paused_at = {
        at
        for at, domain, service, _e, _d in arrived
        if domain == "switch" and service == "turn_off"
    }
    bare_zeros = [at for at, value in amps if value <= 0.0 and at not in paused_at]
    assert bare_zeros == [], bare_zeros
    # The car charged: the fake charger followed the writes and the battery filled.
    assert house.ev.soc > ARRIVAL_SOC + 0.05, house.ev.soc

    await runtime.stop("unload")
