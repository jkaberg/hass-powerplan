"""D4 §9 3 - capability detection on the captured Heatit Z-TRM (INV-27, INV-64).

The reference house's main load class, and the file that decides whether the ×10
lesson was learned. The ancestor controller wrote 18.5, 20.0 and 1.5 straight
into three Z-Wave configuration entities whose unit is `0.1 °C` over a range of
50–400: 111 `ServiceValidationError`s, not one of them noticed, six loops still at
the factory eco of 18 °C and a floor minimum of 5 °C - so the eco drop every shed
depended on had never once been provisioned.

The fix under test is that **nothing here is written down**. The scale comes from
`unit_of_measurement`, the quantisation from `step`, the clamp from `min` and `max`,
the option names from the select's own `options`, and what the thermostat regulates
on from its `sensor_mode`. A firmware that renames its unit, widens its range or
reorders its enum changes the *binding*, and this file proves it by changing the
capture and watching the binding follow.

Two paths, both end to end through `WriteGate` against a thermostat that keeps what
it is told and refuses what is out of range:

* the **hot path** of a `MODE` loop is one `select_option` and **zero** setpoint
  writes - a setpoint write is an NVM write on every shed *and* every restore;
* the **cold path** provisions the eco setpoint, the hardware floor minimum
  (INV-64) and the hysteresis once, verifies them, and sends nothing the device
  already holds.
"""

from __future__ import annotations

import ast
import inspect
from typing import TYPE_CHECKING

import pytest
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.loads import Action, Mode, Role
from custom_components.powerplan.core.loads.gate import (
    Command,
    GateConfig,
    GateState,
    TransportBudget,
    Write,
    decide,
)
from custom_components.powerplan.core.loads.kinds.mode import ModeCfg, ModeKind, match_option
from custom_components.powerplan.providers.profiles import RoleBinding, generic_climate
from custom_components.powerplan.providers.profiles.generic_climate import (
    CAPABILITY_BONUS,
    CLIMATE_CONFIDENCE,
)
from custom_components.powerplan.writegate import Actuation
from tests.core.loads.conftest import (
    REFERENCE_PROFILE,
    floor_config,
    grant,
    kind_ctx,
    setpoint_kind,
)
from tests.providers.profiles.conftest import (
    CLIMATE,
    COMFORT_MODE,
    ECO_MODE,
    ECO_SETPOINT,
    FLOOR_MIN,
    HYSTERESIS,
    LOOP_POWER,
    LOOP_SWITCH,
    MODE_SELECT,
    NOW,
    OPERATION_MODES,
    REPORT_INTERVAL,
    SENSOR_MODE,
    advance,
    binding_of,
    binding_reader,
    dump_view,
    entities_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from freezegun.api import FrozenDateTimeFactory

    from custom_components.powerplan.core.loads.kinds.base import KindCtx
    from custom_components.powerplan.providers.profiles import BoundDevice, DeviceView
    from custom_components.powerplan.writegate import Outcome, StateReader, WriteGate
    from tests.providers.profiles.conftest import FakeHeatit, Sent

PROFILE = generic_climate.PROFILE
LOAD = "loop_bath"
NAME = "Bathroom floor"

#: The Z-TRM's air sensors and the two other `sensor_mode` options, for the variants.
AIR_TEMP = "sensor.bad_1_etasje_gulvvarme_air_temperature"
AIR_TEMP_3 = "sensor.bad_1_etasje_gulvvarme_air_temperature_3"
AIR_MODE = "A2-mode, external room sensor mode"
LIMITED_MODE = "A2F-mode, external sensor with floor limitation"


def bound(view: DeviceView) -> BoundDevice:
    """Bind the profile to a device, as the subentry will."""
    return PROFILE.bind(PROFILE.match(view).bindings)


def actuation(device: BoundDevice, cfg: GateConfig, decision: object) -> Actuation:
    """Wrap one decision for the executor."""
    return Actuation(load_id=LOAD, name=NAME, target=device, cfg=cfg, decision=decision)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# What the device offers
# --------------------------------------------------------------------------- #


def test_03_every_capability_the_thermostat_has_is_found(heatit: DeviceView) -> None:
    """Role → entity for all ten bindings the captured loop offers (D4 §5.9).

    The six optional ones §5.9 names - the mode select, the eco setpoint, the floor
    minimum, the hysteresis, the floor temperature and the power sensor - plus the
    climate entity's own target and reading, the accumulated energy and the relay.
    """
    match = PROFILE.match(heatit)

    assert entities_of(match) == {
        str(Role.SETPOINT): CLIMATE,
        str(Role.TEMP): CLIMATE,
        str(Role.MODE_SELECT): MODE_SELECT,
        str(Role.ECO_SETPOINT): ECO_SETPOINT,
        str(Role.FLOOR_MIN): FLOOR_MIN,
        str(Role.HYSTERESIS): HYSTERESIS,
        str(Role.TEMP_FLOOR): CLIMATE,
        str(Role.POWER): LOOP_POWER,
        str(Role.ENERGY): "sensor.bad_1_etasje_gulvvarme_energy",
        str(Role.SWITCH): LOOP_SWITCH,
    }
    assert not match.missing
    assert match.suggested_kind == "mode"
    assert match.confidence == pytest.approx(CLIMATE_CONFIDENCE + 3 * CAPABILITY_BONUS)


def test_03b_the_target_and_the_reading_are_attributes_not_states(heatit: DeviceView) -> None:
    """A climate entity's state is `heat`; its numbers live in its attributes (D-0180).

    Without this the setpoint could be written and never read back, and INV-22 says
    decisions are made against the entity's own reading rather than against what we
    remember writing.
    """
    match = PROFILE.match(heatit)
    setpoint = binding_of(match, Role.SETPOINT)
    temp = binding_of(match, Role.TEMP)

    assert setpoint is not None
    assert (setpoint.attribute, setpoint.writable) == ("temperature", True)
    assert (setpoint.min_value, setpoint.max_value) == (5.0, 35.0), "the entity's own limits"
    assert temp is not None
    assert (temp.attribute, temp.writable) == ("current_temperature", False)

    reads = bound(heatit).reads(heatit, NOW)
    assert reads.value(Role.SETPOINT) == 24.0
    assert reads.value(Role.TEMP) == 23.6


def test_03c_the_switch_is_bound_read_only(heatit: DeviceView) -> None:
    """A thermostat's relay is not powerplan's switch (INV-64, INV-29).

    Cutting it takes the device's own regulation away with it, and a shed has to be
    a state the household can live in indefinitely. The role is bound so the flow
    can *show* the relay, and `call_for` refuses to write it.
    """
    binding = binding_of(PROFILE.match(heatit), Role.SWITCH)

    assert binding is not None
    assert not binding.writable
    assert bound(heatit).call_for(Write(Role.SWITCH, False)) is None


# --------------------------------------------------------------------------- #
# The ×10 scaling: derived, never written down
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("role", "entity_id", "low", "high"),
    [
        (Role.ECO_SETPOINT, ECO_SETPOINT, 50.0, 400.0),
        (Role.FLOOR_MIN, FLOOR_MIN, 50.0, 400.0),
        (Role.HYSTERESIS, HYSTERESIS, 3.0, 30.0),
    ],
)
def test_03d_the_scaled_numbers_carry_the_entitys_own_unit_range_and_step(
    heatit: DeviceView, role: Role, entity_id: str, low: float, high: float
) -> None:
    """`0.1 °C` over 50–400 step 1 - read off the attributes (D4 §2, §5.9)."""
    binding = binding_of(PROFILE.match(heatit), role)

    assert binding is not None
    assert binding.entity_id == entity_id
    assert binding.unit == "0.1 °C"
    assert binding.scale == 0.1, "the multiplier is the unit's own, not a constant"
    assert (binding.min_value, binding.max_value, binding.step) == (low, high, 1.0)
    assert binding.writable, "all three are provisionable (D4 §5.9)"


def test_03e_a_degree_is_written_as_a_tenth_and_read_back_as_a_degree(heatit: DeviceView) -> None:
    """21.0 °C → 210, 18.5 °C → 185, and 210 → 21.0 °C on the way back."""
    device = bound(heatit)

    floor = device.call_for(Write(Role.FLOOR_MIN, 21.0))
    eco = device.call_for(Write(Role.ECO_SETPOINT, 18.5))

    assert floor is not None
    assert (floor.domain, floor.service, floor.entity_id) == ("number", "set_value", FLOOR_MIN)
    assert floor.data == {"value": 210}
    assert eco is not None
    assert eco.data == {"value": 185}

    reads = device.reads(heatit, NOW)
    assert reads.value(Role.FLOOR_MIN) == pytest.approx(21.0), "state 210 × scale 0.1"
    assert reads.value(Role.ECO_SETPOINT) == pytest.approx(22.0)
    assert reads.value(Role.HYSTERESIS) == pytest.approx(1.0), "state 10 is 1.0 K of swing"


def test_03f_the_profile_contains_no_constant_that_could_be_a_temperature_scale() -> None:
    """The ×10 is nowhere in the module - it is arithmetic, not a product table.

    Asserted over the module's own syntax tree, because this is the one claim a
    reading of the code makes that a behavioural test cannot: a profile that happened
    to hard-code 10 for this thermostat would pass every assertion above and then get
    the next firmware wrong (D4 §2, §11).
    """
    tree = ast.parse(inspect.getsource(generic_climate))
    numbers = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float)
    }

    assert not numbers & {10, 0.1}, f"a scale-shaped literal in the profile: {numbers}"


def test_03g_a_renamed_unit_and_a_widened_range_change_the_binding(heatit: DeviceView) -> None:
    """The firmware this profile exists for: plain `°C`, 5–40, step 0.5 (D4 §2).

    The same role, the same entity, a scale of 1.0 - and 21.0 °C is then written as
    21.0. Nothing in the profile changed.
    """
    next_firmware = dump_view(
        "heatit_z_trm2fx_floor",
        states={FLOOR_MIN: "21.0"},
        attributes={FLOOR_MIN: {"unit_of_measurement": "°C", "min": 5, "max": 40, "step": 0.5}},
    )
    device = bound(next_firmware)
    binding = binding_of(PROFILE.match(next_firmware), Role.FLOOR_MIN)

    assert binding is not None
    assert (binding.unit, binding.scale, binding.step) == ("°C", 1.0, 0.5)
    call = device.call_for(Write(Role.FLOOR_MIN, 21.0))
    assert call is not None
    assert call.data == {"value": 21}
    assert device.reads(next_firmware, NOW).value(Role.FLOOR_MIN) == pytest.approx(21.0)


def test_03h_a_number_in_seconds_is_not_a_temperature(heatit: DeviceView) -> None:
    """The meter-report interval stays unbound, whatever it is named (INV-53).

    Read as a temperature it would be 60 °C, which is how a report interval ends up
    in a setpoint.
    """
    assert REPORT_INTERVAL not in set(entities_of(PROFILE.match(heatit)).values())


# --------------------------------------------------------------------------- #
# Option names: matched against what the device offers, never by index
# --------------------------------------------------------------------------- #


def test_03i_the_four_options_the_device_offers_are_matched_by_name(heatit: DeviceView) -> None:
    """`Off | Heating mode | Cooling mode (Not implemented) | Energy saving heating mode`.

    The eco option's name *contains* "heating", which is why a substring test for
    heat picks it and why the fallback is asymmetric (D4 §5.5). Getting this wrong
    cost the reference house every stage-3 shed it ever decided: the driver compared
    against the literals "Heat" and "Eco", found nothing, and `apply()` returned
    False silently.
    """
    mode = PROFILE.mode_options(heatit)

    assert mode is not None
    assert mode.entity_id == MODE_SELECT
    assert mode.options == OPERATION_MODES
    assert mode.comfort == COMFORT_MODE
    assert mode.shed == ECO_MODE
    assert mode.off == "Off"
    assert match_option("heat", OPERATION_MODES) == COMFORT_MODE, "not the eco option"


def test_03j_reordered_options_are_matched_the_same_way(heatit: DeviceView) -> None:
    """A firmware that reorders its enum must not swap comfort for eco (D4 §5.5)."""
    reordered = dump_view(
        "heatit_z_trm2fx_floor",
        attributes={MODE_SELECT: {"options": list(reversed(OPERATION_MODES))}},
    )

    mode = PROFILE.mode_options(reordered)

    assert mode is not None
    assert mode.comfort == COMFORT_MODE
    assert mode.shed == ECO_MODE
    assert mode.options == tuple(reversed(OPERATION_MODES)), "the device's order, as offered"


def test_03k_a_select_with_no_eco_option_falls_back_to_setpoint(heatit: DeviceView) -> None:
    """Without an eco option there is nothing to shed *to*, so the kind changes (§5.5).

    The loop is then a `SETPOINT` loop on the climate entity, and the confidence drops
    by exactly the one capability that disappeared.
    """
    no_eco = dump_view(
        "heatit_z_trm2fx_floor",
        attributes={MODE_SELECT: {"options": ["Off", "Heating mode"]}},
    )

    match = PROFILE.match(no_eco)

    assert match.suggested_kind == "setpoint"
    assert Role.MODE_SELECT not in {binding.role for binding in match.bindings}
    assert match.confidence == pytest.approx(CLIMATE_CONFIDENCE + 2 * CAPABILITY_BONUS)
    assert "mode_select" not in match.capabilities


def test_03l_the_sensor_mode_says_what_the_thermostat_regulates_on(heatit: DeviceView) -> None:
    """F · A2 · A2F → floor · air · both, by name (D4 §6.1's "Sensor" row).

    In F-mode the climate entity's `current_temperature` *is* the slab, and this
    device has no separate floor sensor - so `temp_floor` binds to that attribute. In
    A2-mode it is the room, and nothing then claims to know the floor.
    """
    air = dump_view("heatit_z_trm2fx_floor", states={SENSOR_MODE: AIR_MODE})
    limited = dump_view("heatit_z_trm2fx_floor", states={SENSOR_MODE: LIMITED_MODE})

    assert PROFILE.placement(heatit) == "floor"
    assert PROFILE.placement(air) == "air"
    assert PROFILE.placement(limited) == "both"

    assert entities_of(PROFILE.match(heatit))[str(Role.TEMP_FLOOR)] == CLIMATE
    assert str(Role.TEMP_FLOOR) not in entities_of(PROFILE.match(air))


def test_03m_two_air_temperature_sensors_bind_neither(heatit: DeviceView) -> None:
    """One of them reads 0.0 °C; ambiguity binds nothing and the flow asks (§5.9)."""
    bound_entities = set(entities_of(PROFILE.match(heatit)).values())

    assert AIR_TEMP not in bound_entities
    assert AIR_TEMP_3 not in bound_entities


def test_03n_the_capabilities_are_what_the_questionnaire_reads(heatit: DeviceView) -> None:
    """`QCtx.capabilities` verbatim, which is how D4 §6.1 chooses mode over setpoint.

    `floor_heating.derive()` asks exactly one question of the hardware -
    `"mode_select" in ctx.capabilities` - and this is the answer travelling to it.
    `readable` is the other half: a measured 599 W replaces the W/m² estimate.
    """
    match = PROFILE.match(heatit)
    caps = PROFILE.detect(heatit)

    assert "mode_select" in match.capabilities
    assert {"eco_setpoint", "floor_min_limit", "hysteresis", "sensor_mode"} <= match.capabilities
    assert "cool" in match.capabilities, "hvac_modes include cool: the store is bidirectional"
    assert caps is not None
    assert caps.direction == "both"
    assert caps.readable == {"power_w": 599.43, "temp_c": 23.6}


# --------------------------------------------------------------------------- #
# The hot path: one `select_option`, zero setpoint writes
# --------------------------------------------------------------------------- #


def mode_loop(view: DeviceView) -> tuple[ModeKind, BoundDevice, GateConfig]:
    """Build the `MODE` kind from what the profile detected, as the flow will.

    The option names are the *matched* ones - D4 §6.1 materialises them into the
    subentry - so the kind never has to guess how this firmware spells eco. The gate
    config is the kind's own numbers (exact, 600 s, 90 s), because a generic profile
    adds no floors of its own.
    """
    caps = PROFILE.detect(view)
    assert caps is not None
    assert caps.mode is not None
    assert caps.kind == "mode"
    kind = ModeKind(
        ModeCfg(
            comfort_option=caps.mode.comfort,
            shed_option=caps.mode.shed,
            min_interval_s=float(floor_config().params["command_interval_s"]),
        )
    )
    return kind, PROFILE.bind(caps.bindings), PROFILE.quirks().gate_config(kind)


def loop_tick(
    device: BoundDevice, view: DeviceView, *, now: datetime, shed: bool, stage: int
) -> KindCtx:
    """Return the `KindCtx` for one tick, with the device's own reads in it."""
    return kind_ctx(
        now=now,
        reads=device.reads(view, now),
        electrical=REFERENCE_PROFILE,
        mode=Mode.AUTO,
        shed=shed,
        stage=stage,
        target=24.0,
        floor=21.0,
        ceiling=27.0,
    )


@pytest.mark.inv("INV-27")
async def test_03o_the_hot_path_writes_one_option_and_no_setpoint(
    gate_factory: Callable[[StateReader], WriteGate],
    thermostat: FakeHeatit,
    calls: list[Sent],
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    """Comfort → shed → comfort: two `select_option`s and zero setpoint writes (§5.5).

    A setpoint write is an NVM write on every shed *and* every restore; the mode
    toggle is neither, and it is atomic. The eco value the loop drops to was
    provisioned on the cold path, which is why the tick never touches it - and the
    comfort it comes back to is configuration's 24.0 °C, never the device's own
    reading (INV-27).

    The ticks are eleven minutes apart because the `MODE` row of D4 §5.10 allows one
    command per device per ten minutes: a restore inside that window is `held_interval`
    and the loop waits, which is the Z-Wave mesh's budget and not a shortcut here.
    """
    kind, device, cfg = mode_loop(thermostat.view())
    gate = gate_factory(binding_reader(thermostat.hass, device))
    state = GateState()

    for shed, stage in ((False, 0), (True, 2), (False, 0)):
        await advance(thermostat.hass, freezer, cfg.min_interval_s + 1.0)
        view = thermostat.view()
        ctx = loop_tick(device, view, now=dt_util.utcnow(), shed=shed, stage=stage)
        command = kind.command(
            kind.quantise(0.0 if shed else 960.0, ctx),
            grant(w=0.0 if shed else 960.0, shed=shed, stage=stage),
            ctx,
        )
        assert isinstance(command, Command), command
        decision = decide(
            command,
            current=kind.current(ctx.reads),
            mode=Mode.AUTO,
            cfg=cfg,
            state=state,
            budget=TransportBudget.empty(),
            now=dt_util.utcnow(),
        )
        outcomes = await gate.async_apply([actuation(device, cfg, decision)])
        state = outcomes[0].gate

    assert [(sent.domain, sent.service) for sent in calls] == [
        ("select", "select_option"),
        ("select", "select_option"),
    ]
    assert [sent.data["option"] for sent in calls] == [ECO_MODE, COMFORT_MODE]
    assert all(sent.blocking for sent in calls), "INV-24"
    assert thermostat.setpoint_writes == 0, "zero setpoint writes on the hot path (D4 §5.5)"
    assert thermostat.setpoint == 24.0
    assert thermostat.number(ECO_SETPOINT) == 220.0
    assert thermostat.number(FLOOR_MIN) == 210.0
    assert thermostat.option() == COMFORT_MODE


@pytest.mark.inv("INV-27")
async def test_03p_a_comfort_tick_on_a_loop_already_in_comfort_sends_nothing(
    gate: WriteGate, thermostat: FakeHeatit, calls: list[Sent], now: datetime
) -> None:
    """Row 3 of the gate matrix, through this profile: the device holds it already."""
    kind, device, cfg = mode_loop(thermostat.view())
    ctx = loop_tick(device, thermostat.view(), now=now, shed=False, stage=0)
    command = kind.command(kind.quantise(960.0, ctx), grant(w=960.0), ctx)
    assert isinstance(command, Command), command

    decision = decide(
        command,
        current=kind.current(ctx.reads),
        mode=Mode.AUTO,
        cfg=cfg,
        state=GateState(),
        budget=TransportBudget.empty(),
        now=now,
    )
    outcomes = await gate.async_apply([actuation(device, cfg, decision)])

    assert decision.action is Action.SAME
    assert [outcome.action for outcome in outcomes] == [Action.SAME]
    assert calls == [], "nothing at all, not even a confirming write"
    assert thermostat.seen == []


# --------------------------------------------------------------------------- #
# The cold path: provisioned once, verified, never re-sent
# --------------------------------------------------------------------------- #


def test_03q_the_provisions_are_the_floor_the_eco_target_and_the_deadband(
    heatit: DeviceView,
) -> None:
    """Three, in degrees, from the materialised subentry - never from the device."""
    provisions = PROFILE.provisions(heatit, floor_config())

    assert [provision.entity_id for provision in provisions] == [
        FLOOR_MIN,
        ECO_SETPOINT,
        HYSTERESIS,
    ]
    assert [provision.role for provision in provisions] == [
        Role.FLOOR_MIN,
        Role.ECO_SETPOINT,
        Role.HYSTERESIS,
    ]
    assert [provision.value for provision in provisions] == [21.0, 22.0, 1.0]
    assert not any(provision.scaled for provision in provisions)
    assert "INV-64" in provisions[0].reason


def test_03r_a_load_with_no_configuration_yet_is_provisioned_with_nothing(
    heatit: DeviceView,
) -> None:
    """The numbers are the questionnaire's (INV-27, INV-66): no config, no provision."""
    assert PROFILE.provisions(heatit) == ()


@pytest.mark.inv("INV-64")
async def test_03s_provisioning_writes_the_floor_limit_and_the_eco_once_then_verifies(
    gate_factory: Callable[[StateReader], WriteGate],
    thermostat: FakeHeatit,
    calls: list[Sent],
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    """A factory-fresh thermostat: floor 5.0 °C, eco 20.0 °C, hysteresis already 1.0 K.

    Exactly two writes go out - 210 and 220, scaled - and the third is row 3 of the
    matrix, because the device already holds 1.0 K. The hardware floor is the point:
    once it is 21.0 °C the thermostat refuses to go below it whatever powerplan asks,
    so a shed that outlives Home Assistant is still a room somebody can live in
    (INV-64, HLD §7.8).
    """
    _move(thermostat, FLOOR_MIN, "50.0")
    _move(thermostat, ECO_SETPOINT, "200.0")
    device = bound(thermostat.view())
    cfg = PROFILE.quirks().gate_config(setpoint_kind())
    gate = gate_factory(binding_reader(thermostat.hass, device))

    outcomes = await _provision(gate, device, thermostat, cfg)

    assert [outcome.action for outcome in outcomes] == [Action.WRITTEN, Action.WRITTEN, Action.SAME]
    assert [(sent.target["entity_id"], sent.data["value"]) for sent in calls] == [
        (FLOOR_MIN, 210),
        (ECO_SETPOINT, 220),
    ]
    assert all(sent.blocking for sent in calls), "INV-24"
    assert thermostat.refused == [], "a scaled write is accepted where an unscaled one is not"
    assert thermostat.number(FLOOR_MIN) == 210.0
    assert thermostat.number(ECO_SETPOINT) == 220.0

    # The read-back reads the entity in powerplan's units and finds what was asked
    # for: no deviation, and the step stays landed (INV-22, D-0182).
    await advance(thermostat.hass, freezer, cfg.verify_after_s + 1.0)
    assert thermostat.number(FLOOR_MIN) == 210.0
    assert [outcome.gate.deviations for outcome in outcomes] == [0, 0, 0]

    calls.clear()
    again = await _provision(gate, device, thermostat, cfg)
    assert [outcome.action for outcome in again] == [Action.SAME, Action.SAME, Action.SAME]
    assert calls == [], "idempotent: a provision that landed is never re-sent"


@pytest.mark.inv("INV-64")
async def test_03t_the_provisions_the_captured_loop_already_holds_are_not_sent(
    gate_factory: Callable[[StateReader], WriteGate],
    thermostat: FakeHeatit,
    calls: list[Sent],
    now: datetime,
) -> None:
    """The reference house's loop is already provisioned: 210, 220, 10 (D4 §2)."""
    device = bound(thermostat.view())
    cfg = PROFILE.quirks().gate_config(setpoint_kind())
    gate = gate_factory(binding_reader(thermostat.hass, device))

    outcomes = await _provision(gate, device, thermostat, cfg)

    assert [outcome.action for outcome in outcomes] == [Action.SAME] * 3
    assert calls == []


async def test_03u_an_unscaled_write_is_refused_by_the_device_and_counted(
    gate: WriteGate, thermostat: FakeHeatit, calls: list[Sent], now: datetime
) -> None:
    """The ×10 lesson, reproduced: 21.0 into a `0.1 °C` entity of range 50–400 is refused.

    The binding here is the one a driver that *assumed* degrees would produce -
    scale 1.0 on an entity that counts tenths - and the device does what Z-Wave JS
    did: `ServiceValidationError`, the value unchanged, the thermostat still at its
    factory floor of 5.0 °C. The only difference between then and now is that the
    failure is **counted** (D4 §8) instead of being swallowed 111 times.
    """
    _move(thermostat, FLOOR_MIN, "50.0")
    unscaled = PROFILE.bind(
        [
            RoleBinding(
                role=Role.FLOOR_MIN,
                entity_id=FLOOR_MIN,
                unit="0.1 °C",
                scale=1.0,  # the old assumption: "it says °C, so write °C"
                writable=True,
            )
        ]
    )
    cfg = PROFILE.quirks().gate_config(setpoint_kind())
    decision = decide(
        Command(writes=(Write(Role.FLOOR_MIN, 21.0),), reason="the 2026-09-07 write"),
        current=5.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=GateState(),
        budget=TransportBudget.empty(),
        now=now,
        release=True,
    )

    outcomes = await gate.async_apply([actuation(unscaled, cfg, decision)])

    assert [outcome.action for outcome in outcomes] == [Action.FAILED]
    assert outcomes[0].error is not None
    assert "refused" in outcomes[0].error
    assert outcomes[0].gate.failures == 1, "a refusal is a real failure (D4 §5.10)"
    assert thermostat.refused == [(FLOOR_MIN, "21.0")]
    assert thermostat.number(FLOOR_MIN) == 50.0, "the thermostat kept its factory value"

    landed = bound(thermostat.view()).call_for(Write(Role.FLOOR_MIN, 21.0))
    assert landed is not None
    assert landed.data == {"value": 210}, "and the scaled write is the one that lands"


def _move(thermostat: FakeHeatit, entity_id: str, state: str) -> None:
    """Move one entity, keeping the attributes the capture gave it."""
    held = thermostat.hass.states.get(entity_id)
    assert held is not None
    thermostat.hass.states.async_set(entity_id, state, dict(held.attributes))


async def _provision(
    gate: WriteGate, device: BoundDevice, thermostat: FakeHeatit, cfg: GateConfig
) -> list[Outcome]:
    """Run every `Provision` of the captured loop through the gate, in order.

    A provision is not a control action: it goes through `decide(release=True)`, so
    it ignores the interval and the dwell clocks - but never row 3, because a device
    is never sent a value it already holds, not even on the cold path (D4 §2, §5.10).
    """
    outcomes: list[Outcome] = []
    for provision in PROFILE.provisions(thermostat.view(), floor_config()):
        assert provision.role is not None
        reads = device.reads(thermostat.view(), dt_util.utcnow())
        decision = decide(
            Command(
                writes=(Write(provision.role, provision.value),),
                reason=provision.reason,
                urgent=True,
            ),
            current=reads.current_of(provision.role),
            mode=Mode.AUTO,
            cfg=cfg,
            state=GateState(),
            budget=TransportBudget.empty(),
            now=dt_util.utcnow(),
            release=True,
        )
        outcomes.extend(await gate.async_apply([actuation(device, cfg, decision)]))
    return outcomes


# --------------------------------------------------------------------------- #
# A plain `climate` entity: SETPOINT, and nothing to provision
# --------------------------------------------------------------------------- #


def test_03v_a_plain_climate_is_a_setpoint_loop_with_no_provisions(
    plain_climate: DeviceView,
) -> None:
    """The same detection on a `generic_thermostat` over a relay (D4 §9 3).

    One climate entity, no eco setpoint, no floor minimum, no operation-mode select -
    so the kind is `SETPOINT`, there is nothing to provision, and the shed is a
    setpoint the thermostat goes on regulating against if powerplan dies (INV-64).
    """
    match = PROFILE.match(plain_climate)

    assert match.suggested_kind == "setpoint"
    assert set(entities_of(match)) == {str(Role.SETPOINT), str(Role.TEMP)}
    assert match.confidence == CLIMATE_CONFIDENCE
    assert PROFILE.provisions(plain_climate, floor_config()) == ()
    assert PROFILE.mode_options(plain_climate) is None
    assert PROFILE.placement(plain_climate) is None


def test_03w_a_plain_climates_own_step_and_range_bound_the_write(
    plain_climate: DeviceView,
) -> None:
    """`target_temp_step` 0.1 quantises and `min_temp`/`max_temp` clamp (D4 §5.4).

    Rounding is down, always: a setpoint rounded up is heat nobody granted.
    """
    match = PROFILE.match(plain_climate)
    binding = binding_of(match, Role.SETPOINT)
    device = PROFILE.bind(match.bindings)

    assert binding is not None
    assert binding.step == 0.1
    low, high = binding.min_value, binding.max_value
    assert low is not None
    assert high is not None

    middle = device.call_for(Write(Role.SETPOINT, low + 1.37))
    above = device.call_for(Write(Role.SETPOINT, high + 5.0))

    assert middle is not None
    assert (middle.domain, middle.service) == ("climate", "set_temperature")
    assert middle.data["temperature"] == pytest.approx(low + 1.3)
    assert above is not None
    assert above.data["temperature"] == pytest.approx(high)
