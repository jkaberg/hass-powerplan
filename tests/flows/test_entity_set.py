"""D8 §5.16, §9 24 and A.2's exit - an appliance's entity set, by type.

Four appliances of the reference house are added through the flow: the
charger, the tank, the sauna and the heat pump. Each gets the set D8 §5.16
draws - daily use uncategorised, tuning under Configuration, the rest
Diagnostic and off - on its own hardware device, its names free of "PowerPlan"
and its ids Home Assistant's own. `plan_status` has exactly one state for every
mode, shed, override and health a load can reach.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import (
    DOMAIN,
    LOAD_PRIORITY,
    LOAD_STRATEGY,
    SUBENTRY_LOAD,
)
from custom_components.powerplan.core.model import Mode
from custom_components.powerplan.load_entities import PLAN_STATES, plan_state
from tests.flows.test_heat_pump_flow import _add_heat_pump
from tests.flows.test_load_flow import _add_charger, _answer
from tests.flows.test_subentry_hot_paths import _add_sauna
from tests.flows.test_water_heater_flow import _add_tank

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.e2e.fake_house import FakeHouse

CONFIG = EntityCategory.CONFIG
DIAGNOSTIC = EntityCategory.DIAGNOSTIC


async def _named(
    hass: HomeAssistant, site: MockConfigEntry, result: dict[str, Any], name: str
) -> str:
    await _answer(hass, result, **{**result["data_schema"]({}), "name": name})
    await hass.async_block_till_done()
    return next(s.subentry_id for s in site.subentries.values() if s.title == name)


async def _add_floor(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> dict[str, Any]:
    """Type → the Heatit floor thermostat → match → questions, on the defaults."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": "user"}
        )
    )
    result = await _answer(hass, result, type="floor_heating")
    result = await _answer(hass, result, device=charger.loads["loop_bath_1"].device_id)
    assert result["step_id"] == "match", result
    result = await _answer(hass, result, **result["data_schema"]({}))
    assert result["step_id"] == "questions", result
    return await _answer(hass, result, **result["data_schema"]({}))


async def _house(hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse) -> dict[str, str]:
    """Add the five appliances; return `{type: subentry id}`."""
    return {
        "floor_heating": await _named(hass, site, await _add_floor(hass, site, charger), "Bath"),
        "ev": await _named(hass, site, await _add_charger(hass, site, charger), "Charger"),
        "water_heater": await _named(hass, site, await _add_tank(hass, site, charger), "Tank"),
        "generic_switch": await _add_sauna(hass, site, charger),
        "heat_pump": await _named(hass, site, await _add_heat_pump(hass, site, charger), "Pump"),
    }


def _rows(hass: HomeAssistant, site: MockConfigEntry, load_id: str) -> dict[str, er.RegistryEntry]:
    prefix = f"{DOMAIN}_{site.entry_id}_{load_id}_"
    return {
        entry.unique_id.removeprefix(prefix): entry
        for entry in er.async_get(hass).entities.get_entries_for_config_entry_id(site.entry_id)
        if entry.config_subentry_id == load_id
    }


#: Each appliance's rows: `{key: (category, enabled by default)}` (D8 §5.16).
COMMON = {
    "control": (None, True),
    "plan_status": (None, True),
    "cost_month": (None, True),
    "priority": (CONFIG, True),
    "health": (DIAGNOSTIC, True),
    "energy": (DIAGNOSTIC, True),
    "energy_month": (DIAGNOSTIC, False),
    "granted_power": (DIAGNOSTIC, False),
    "reserved_power": (DIAGNOSTIC, False),
    "planned_energy": (DIAGNOSTIC, False),
}
#: D10 §5.6's fits, published per load and disabled by default.
LEARNED_THERMAL = {
    "learned_loss_coeff_w_per_k": (DIAGNOSTIC, False),
    "learned_heatup_k_per_h": (DIAGNOSTIC, False),
}
LEARNED_NAMEPLATE = {"learned_nameplate_w": (DIAGNOSTIC, False)}
EXPECTED: dict[str, dict[str, tuple[EntityCategory | None, bool]]] = {
    "ev": {
        **COMMON,
        "savings_month": (None, True),
        "charge_target": (None, True),
        "ready_by": (None, True),
        "learned_charge_efficiency": (DIAGNOSTIC, False),
        "strategy": (CONFIG, True),
        "run_now_max": (CONFIG, True),
        "charge_min": (CONFIG, True),
    },
    "water_heater": {
        **COMMON,
        "savings_month": (None, True),
        "ready_by": (None, True),
        "next_legionella": (None, True),
        "learned_standby_loss_w": (DIAGNOSTIC, False),
        **LEARNED_NAMEPLATE,
        "strategy": (CONFIG, True),
        "run_now_max": (CONFIG, True),
        "follow_presence": (CONFIG, False),
        "temp_min": (CONFIG, False),
        "temp_max": (CONFIG, False),
    },
    # The sauna has no shadow, so no savings; its only strategy is `always`,
    # so no strategy select (D-0412).
    "generic_switch": {**COMMON, "run_now_max": (CONFIG, True)},
    # Steered by its heat/eco mode; its own setpoint is the comfort target, so
    # no comfort number of ours (D-0435).
    "floor_heating": {
        **COMMON,
        **LEARNED_THERMAL,
        **LEARNED_NAMEPLATE,
        "savings_month": (None, True),
        "strategy": (CONFIG, True),
        "follow_presence": (CONFIG, True),
        "temp_min": (CONFIG, False),
        "temp_max": (CONFIG, False),
    },
    # A heat pump modulates: no nameplate to learn (D10 §9 9).
    "heat_pump": {
        **COMMON,
        **LEARNED_THERMAL,
        "savings_month": (None, True),
        "strategy": (CONFIG, True),
        "follow_presence": (CONFIG, True),
        "temp_min": (CONFIG, False),
        "temp_max": (CONFIG, False),
    },
}


@pytest.mark.inv("INV-50")
async def test_every_type_gets_its_set_small_and_by_level(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """A.2's per-type count: 3–6 rows a household sees, the rest filed away.

    No comfort number on these five: the thermostats' own setpoints are their
    knobs, and no `measured_power` where the device page shows its own meter.
    """
    loads = await _house(hass, site, charger)
    for type_key, load_id in loads.items():
        rows = _rows(hass, site, load_id)
        got = {
            key: (entry.entity_category, entry.disabled_by is None) for key, entry in rows.items()
        }
        assert got == EXPECTED[type_key], type_key
        shown = [key for key, (category, enabled) in got.items() if category is None and enabled]
        assert 3 <= len(shown) <= 6, (type_key, shown)


async def test_24_names_carry_no_prefix_and_ids_are_home_assistants_own(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """§9 24 as amended (dec. 36): `<domain>.<device>_<translated name>`, no "powerplan"."""
    loads = await _house(hass, site, charger)
    rows = _rows(hass, site, loads["ev"])
    assert rows["control"].entity_id == "select.ev_control"
    assert rows["plan_status"].entity_id == "sensor.ev_plan_status"
    assert rows["charge_target"].entity_id == "number.ev_charge_to"
    for load_id in loads.values():
        for entry in _rows(hass, site, load_id).values():
            assert "powerplan" not in entry.entity_id, entry.entity_id
            state = hass.states.get(entry.entity_id)
            if state is not None:
                assert "PowerPlan" not in str(state.attributes.get("friendly_name")), state


async def test_strategy_and_priority_are_written_to_the_appliance(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Level 2 lives in the subentry: a select changes it in place, and the load follows."""
    loads = await _house(hass, site, charger)
    ev_id = loads["ev"]
    rows = _rows(hass, site, ev_id)
    strategy = hass.states.get(rows["strategy"].entity_id)
    assert strategy is not None
    assert strategy.attributes["options"] == ["deadline_fill", "cheapest_hours"], "no `always`"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": rows["strategy"].entity_id, "option": "cheapest_hours"},
        blocking=True,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": rows["priority"].entity_id, "option": "high"},
        blocking=True,
    )
    await hass.async_block_till_done()

    subentry = site.subentries[ev_id]
    assert subentry.data[LOAD_STRATEGY] == "cheapest_hours"
    assert subentry.data[LOAD_PRIORITY] == 45
    load = next(load for load in site.runtime_data.build.loads if load.load_id == ev_id)
    assert (load.config.strategy, load.config.priority) == ("cheapest_hours", 45)
    state = hass.states.get(rows["priority"].entity_id)
    assert state is not None
    assert state.state == "high"


def _status(**changes: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "load_id": "loop",
        "type_key": "floor_heating",
        "mode": Mode.AUTO,
        "shed": False,
        "granted_w": 0.0,
        "measured_w": None,
        "health": SimpleNamespace(unhealthy=False),
        "demand": SimpleNamespace(wants=True),
        "latches": SimpleNamespace(session_done=False),
    }
    return SimpleNamespace(**{**base, **changes})


def _runtime(*, overridden: bool, wrote_since: bool) -> SimpleNamespace:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415 - test-local

    at = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    gate = SimpleNamespace(last_write_at=at + timedelta(minutes=1) if wrote_since else None)
    return SimpleNamespace(
        overridden_at={"loop": at} if overridden else {},
        state=SimpleNamespace(
            loads={"loop": SimpleNamespace(gate=gate)},
            events=SimpleNamespace(edges={}),
        ),
    )


def test_plan_status_has_one_state_for_every_combination() -> None:
    """D8 §5.16: every (mode, shed, override, health, demand, draw) a load reaches → one state.

    The function returns one value, so exclusivity is by construction; what is
    tested is that each lands in the closed set and that the precedence reads
    the device first, then a hand on it, then the mode, then the plan.
    """
    for mode, shed, overridden, wrote_since, unhealthy, wants, granted in itertools.product(
        Mode,
        (False, True),
        (False, True),
        (False, True),
        (False, True),
        (False, True),
        (0.0, 900.0),
    ):
        status = _status(
            mode=mode,
            shed=shed,
            granted_w=granted,
            health=SimpleNamespace(unhealthy=unhealthy),
            demand=SimpleNamespace(wants=wants),
        )
        runtime = _runtime(overridden=overridden, wrote_since=wrote_since)
        state = plan_state(status, runtime)  # type: ignore[arg-type]
        assert state in PLAN_STATES
        if unhealthy:
            assert state == "device_unavailable"
        elif overridden and not wrote_since:
            assert state == "manual_override"
        elif mode is Mode.FORCE:
            assert state == "run_now"
        elif mode in (Mode.OFF, Mode.DELEGATED):
            assert state == "not_controlled"
        elif mode is Mode.OBSERVE:
            assert state == "observing"
        elif shed:
            assert state == "paused_peak"
        elif not wants:
            assert state == "idle"
        else:
            assert state == ("running_plan" if granted else "waiting")


def test_plan_status_reads_the_cars_session_first() -> None:
    """The car: no car, charging and done before the plan's own states."""
    runtime = _runtime(overridden=False, wrote_since=False)
    car = _status(type_key="ev", load_id="loop")
    assert plan_state(car, runtime) == "no_car"  # type: ignore[arg-type]
    runtime.state.events.edges["connected:loop"] = "1"
    assert plan_state(_status(type_key="ev", granted_w=7000.0), runtime) == "charging"  # type: ignore[arg-type]
    assert plan_state(_status(type_key="ev", shed=True), runtime) == "paused_peak"  # type: ignore[arg-type]
    done = _status(type_key="ev", latches=SimpleNamespace(session_done=True))
    assert plan_state(done, runtime) == "done"  # type: ignore[arg-type]
