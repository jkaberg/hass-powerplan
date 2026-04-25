"""D4 §9 18 - the heat pump: defrost, `never_switch`, and the band (INV-29).

INV-29 is five statements about one device, and every one of them is a defect the
ancestor controller produced:

* the setpoint is bounded to `comfort ± band` and the **band is rejected, not
  clipped** - a configuration file and the behaviour must never disagree;
* start-up **restores** the comfort value instead of adopting what it finds, and
  makes no upward move within one dwell of that restore (the 22 → 26 °C walk over
  eight restarts);
* it is **never shed during defrost** - a unit reversing its cycle to melt its
  own coil is drawing 1.5 kW and heating nothing, and shedding it there buys no
  comfort back and repeats forty-five minutes later;
* the **mains switch is never actuated**, whatever the stage.

The physical thing is `tests/sim/heatpump.py`: a Carnot-based COP with an exergy
term (so it disagrees with D4 §6.4's straight lines between the anchors) and a
defrost cycle with its full signature - a valve swing at standby power, then
minutes at full power with the indoor outlet air *below* room temperature. That
signature is the detector, and a static mock would have let a detector that keys
on nothing at all pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import (
    Action,
    AnswerError,
    Desired,
    LoadState,
    Mode,
    QCtx,
    Role,
    device_types,
)
from custom_components.powerplan.core.loads.kinds.setpoint import BAND_MAX_K, Setpoint, SetpointCfg
from custom_components.powerplan.core.loads.types.heat_pump import (
    DEFROST_MAX_S,
    RESERVE_MARGIN_FRACTION,
    HeatPump,
)
from tests.core.loads.conftest import (
    OSLO,
    grant,
    heatpump_reads,
    load_ctx,
    load_from,
    materialised,
    sim_command,
    sim_env,
)
from tests.sim.heatpump import RATED_W, HeatPumpSim, cop_at

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Command, Load, LoadCtx

#: A February morning at 06:23 local, cold enough to frost the outdoor coil.
START = datetime(2026, 2, 3, 6, 23, 41, tzinfo=OSLO)

TICK_S = 60.0
OUTDOOR_C = -2.0

#: What the flow stores for the reference house's air-to-air unit: 1.5 kW rated,
#: the open-plan 60 m², built 2000–2010 (D9 §5.9).
REFERENCE = {"hp_type": "a2a", "rated_kw": 1.5, "area_m2": 60.0, "building": "2000_2010"}


def pump(**answers: Any) -> Load:
    """Return the reference house's heat pump, answers overridable."""
    return load_from("heat_pump", {**REFERENCE, **answers})


def pump_sim(**kwargs: Any) -> HeatPumpSim:
    """Return the unit the load is in front of, cold room, cold outside."""
    options: dict[str, Any] = {"area_m2": 60.0, "room_c": 19.0, "setpoint_c": 21.0}
    options.update(kwargs)
    return HeatPumpSim(**options)


def tick(
    load: Load,
    state: LoadState,
    sim: HeatPumpSim,
    at: datetime,
    command: Command | None,
    **ctx_kwargs: Any,
) -> tuple[LoadState, LoadCtx, Any]:
    """Advance the simulator one tick under `command` and observe it."""
    step = sim.step(TICK_S, sim_command(command), sim_env(at, outdoor_c=OUTDOOR_C))
    ctx = load_ctx(
        now=at,
        reads=heatpump_reads(step, at, outdoor_c=OUTDOOR_C),
        outdoor_c=OUTDOOR_C,
        zone=OSLO,
        **ctx_kwargs,
    )
    state, observation = load.observe(state, ctx)
    return state, ctx, observation


def run_to_defrost(load: Load, sim: HeatPumpSim) -> tuple[LoadState, LoadCtx, int]:
    """Tick until the unit is defrosting **and drawing** for it (§5.14).

    The signature arrives a tick late on purpose: the cycle opens with the
    four-way valve swinging at standby power, which looks like an idle unit. What
    identifies a defrost is the minute after that - full power, cold outlet.
    """
    state = LoadState()
    command: Command | None = None
    for index in range(120):
        at = START + timedelta(seconds=TICK_S * index)
        state, ctx, _ = tick(load, state, sim, at, command)
        state, result = load.apply(grant(RATED_W), state, ctx)
        command = result.command
        if state.defrost_since is not None:
            return state, ctx, index
    raise AssertionError("no defrost in two hours below +3 °C — check the simulator")


@pytest.mark.inv("INV-29")
def test_18_a_defrosting_heat_pump_is_never_shed() -> None:
    """INV-29: no shed during defrost - the unit is already not heating (§5.14)."""
    load = pump()
    sim = pump_sim()

    state, ctx, index = run_to_defrost(load, sim)

    assert sim.defrosting, "the simulator agrees it is melting its coil"
    assert load.device_type.defrosting(load, state, ctx)
    target = load.config.target
    assert target is not None

    state, result = load.apply(
        grant(0.0, shed=True, shed_reason="stage 2 capacity", stage=2), state, ctx
    )

    assert result.value == pytest.approx(target.comfort_default), (
        "the setpoint stays at the comfort target: a defrost window is not a shed"
    )
    assert result.action in {Action.SAME, Action.WRITTEN, Action.HELD_INTERVAL}
    assert index > 30, "and it took a real three quarters of an hour of running to get there"


@pytest.mark.inv("INV-29")
def test_18b_the_defrost_latch_lets_go_again() -> None:
    """A latch that stuck would suppress every shed for ever, which is worse.

    So it clears when the outlet is back above the room - the unit is heating
    again - and in any case after `DEFROST_MAX_S`.
    """
    load = pump()
    sim = pump_sim()
    state, _, index = run_to_defrost(load, sim)
    started = state.defrost_since
    assert started is not None

    command: Command | None = None
    for step in range(1, 40):
        at = START + timedelta(seconds=TICK_S * (index + step))
        state, ctx, _ = tick(load, state, sim, at, command)
        state, result = load.apply(grant(RATED_W), state, ctx)
        command = result.command
        if state.defrost_since is None:
            assert not sim.defrosting, "we let go after the unit did, never before"
            assert (at - started).total_seconds() <= DEFROST_MAX_S
            return
    raise AssertionError("the defrost latch never cleared")


@pytest.mark.inv("INV-29")
@pytest.mark.parametrize("stage", [0, 1, 2, 3, 4])
def test_18c_the_mains_switch_is_never_actuated_at_any_stage(stage: int) -> None:
    """INV-29's last line: powerplan does not switch a heat pump off. Ever."""
    load = pump()
    sim = pump_sim()
    state = LoadState()
    command: Command | None = None
    written: list[Role] = []

    for index in range(20):
        at = START + timedelta(seconds=TICK_S * index)
        state, ctx, _ = tick(load, state, sim, at, command)
        state, result = load.apply(
            grant(
                0.0 if stage else RATED_W,
                shed=stage > 0,
                shed_reason=f"stage {stage}" if stage else None,
                stage=stage,
                blunt=stage >= 4,
                stop_ok=stage >= 4,
            ),
            state,
            ctx,
        )
        command = result.command
        if command is not None:
            written.extend(write.role for write in command.writes)

    assert set(written) <= {Role.SETPOINT}, "a heat pump is steered by one number"
    assert Role.SWITCH not in written
    assert Role.ENABLE not in written
    assert "switch" not in HeatPump.kinds, "there is no switch kind to reach for"
    assert sim.hvac_on, "and the unit is still running"


def test_18d_never_switch_entities_are_carried_into_the_subentry() -> None:
    """The listed entities are data, so the executor can refuse them (§5.14)."""
    data = materialised(
        "heat_pump",
        {**REFERENCE, "never_switch": ["switch.heat_pump_mains", "climate.varmepumpe_power"]},
    )
    assert data["params"]["never_switch"] == [
        "switch.heat_pump_mains",
        "climate.varmepumpe_power",
    ]


@pytest.mark.inv("INV-29")
def test_18e_a_band_wider_than_two_kelvin_is_rejected_not_clipped() -> None:
    """Rejected at the boundary **and** at the kind: two locks, one key."""
    questionnaire = device_types.get("heat_pump").questionnaire
    with pytest.raises(AnswerError, match="band_k") as answer:
        questionnaire.validate({**REFERENCE, "band_k": 2.5}, QCtx())
    assert answer.value.code == "too_large"

    assert questionnaire.get("band_k").max == pytest.approx(BAND_MAX_K)
    with pytest.raises(ValueError, match="rejected, not clipped"):
        Setpoint(SetpointCfg(shed_setpoint=20.0, band_up=2.5, band_down=1.0))

    at_the_limit = pump(band_k=BAND_MAX_K)
    assert at_the_limit.config.params["band_k"] == pytest.approx(BAND_MAX_K)


@pytest.mark.inv("INV-29")
def test_18f_a_heat_pump_has_no_urgent_path() -> None:
    """§5.10: compressor protection - no stage buys past the command interval."""
    load = pump()
    sim = pump_sim()
    state = LoadState()
    state, ctx, _ = tick(load, state, sim, START, None)

    for stage in (2, 3, 4):
        _, result = load.apply(
            grant(0.0, shed=True, shed_reason=f"stage {stage}", stage=stage), state, ctx
        )
        assert result.command is None or not result.command.urgent, (
            "a compressor is not hurried; the shed lands on the next interval"
        )


def test_18g_the_coast_offset_arrives_at_stage_three() -> None:
    """§5.4: the heat pump coasts one kelvin under target from stage 3."""
    load = pump()
    sim = pump_sim(room_c=21.0)
    state = LoadState()
    state, ctx, _ = tick(load, state, sim, START, None)
    target = load.config.target
    assert target is not None
    comfort = target.comfort_default

    _, at_stage_one = load.apply(grant(RATED_W, stage=1), state, ctx)
    _, at_stage_three = load.apply(
        grant(0.0, shed=False, stage=3, capped_by=("circuit",)), state, ctx
    )

    assert at_stage_one.value == pytest.approx(comfort)
    assert at_stage_three.value == pytest.approx(comfort - 1.0)


def test_18h_a_shed_stays_inside_the_band() -> None:
    """INV-29: the setpoint never leaves `comfort ± band`, whoever asks."""
    load = pump()
    sim = pump_sim(room_c=21.0)
    state = LoadState()
    state, ctx, _ = tick(load, state, sim, START, None)
    target = load.config.target
    assert target is not None
    band = float(load.config.params["band_k"])

    _, shed = load.apply(grant(0.0, shed=True, shed_reason="stage 2", stage=2), state, ctx)
    _, banked = load.apply(
        grant(RATED_W, stage=0),
        state,
        ctx,
    )

    assert shed.value == pytest.approx(target.comfort_default - band)
    assert banked.value == pytest.approx(target.comfort_default)

    state, ctx, _ = tick(
        load, state, sim, START + timedelta(seconds=TICK_S), None, setpoint_delta=3.0
    )
    _, over = load.apply(grant(RATED_W, stage=0), state, ctx)
    assert over.value == pytest.approx(target.comfort_default + band), (
        "a plan asking for three kelvin gets one: the band is the hard cap"
    )


def test_18i_an_inverter_at_twenty_watts_does_not_reserve_three_kilowatts() -> None:
    """§5.14: the reservation is measured + margin, never the nameplate."""
    load = pump()
    sim = pump_sim(room_c=21.4)
    state = LoadState()
    command: Command | None = None
    for index in range(3):
        at = START + timedelta(seconds=TICK_S * index)
        state, ctx, observation = tick(load, state, sim, at, command)
        state, result = load.apply(grant(RATED_W), state, ctx)
        command = result.command

    measured = ctx.reads.value(Role.POWER)
    assert measured is not None
    assert measured < 100.0, "a satisfied inverter idles at its electronics"
    assert observation.demand.max_w < RATED_W, "so it does not reserve the whole unit"
    assert observation.demand.max_w >= measured * (1.0 + RESERVE_MARGIN_FRACTION)


def test_18j_the_cop_curve_is_shown_editable_and_used() -> None:
    """§6.4: defaulted by type, editable, and the thing expected draw comes from."""
    default = pump()
    curve = default.config.params["cop_curve"]
    assert curve["-15.0"] == pytest.approx(1.8), "D4 §6.4's A2A anchor"
    assert curve["7.0"] == pytest.approx(3.8)

    ground = pump(hp_type="gshp")
    assert min(ground.config.params["cop_curve"].values()) >= 3.0, "a borehole is warmer than air"

    edited = pump(cop_curve={-15: 1.9, 0: 3.1, 15: 4.6})
    assert edited.config.params["cop_curve"] == {"-15.0": 1.9, "0.0": 3.1, "15.0": 4.6}

    with pytest.raises(AnswerError, match="cop_curve"):
        device_types.get("heat_pump").questionnaire.validate({"cop_curve": {0: 0.0}}, QCtx())

    sim = pump_sim()
    state = LoadState()
    state, ctx, observation = tick(load := pump(), state, sim, START, None)
    expected = load.device_type.expected_draw_w(load, ctx)
    assert expected is not None
    demand_w = float(load.config.params["heat_loss_w_per_k"]) * (21.0 - OUTDOOR_C)
    assert expected == pytest.approx(min(RATED_W, demand_w / cop_at(OUTDOOR_C)), rel=0.2)
    assert observation.demand.wants, "19 °C in a 21 °C room wants heat"


def test_18k_the_building_age_table_becomes_a_heat_loss() -> None:
    """§6.4: 1.6 / 1.0 / 0.7 / 0.5 W/m²K by period, over the heated area."""
    for building, u_value in (
        ("before_1980", 1.6),
        ("1980_2000", 1.0),
        ("2000_2010", 0.7),
        ("after_2010", 0.5),
    ):
        params = materialised("heat_pump", {**REFERENCE, "building": building})["params"]
        assert params["heat_loss_w_per_k"] == pytest.approx(u_value * 60.0)
        assert params["u_envelope_w_per_m2k"] == pytest.approx(u_value)


@pytest.mark.inv("INV-29")
def test_18l_the_first_write_after_a_restart_is_a_correction() -> None:
    """Restore the configured comfort value, then no upward move for one dwell.

    The heat pump's dwell is 1 800 s (§6.4), which is what stops the setpoint
    walking a band per restart while still letting a shed through immediately.
    """
    load = pump()
    sim = pump_sim(room_c=19.0, setpoint_c=24.0)
    state = LoadState(mode=Mode.AUTO)
    state, ctx, _ = tick(load, state, sim, START, None)

    state, restored = load.apply(grant(RATED_W), state, ctx)
    assert restored.value == pytest.approx(21.0), "the configured target, not the 24 it found"

    state, ctx, _ = tick(load, state, sim, START + timedelta(seconds=TICK_S), restored.command)
    state, _ = load.restore(state, ctx, "startup")
    assert state.last_target_restore_at is not None

    later = START + timedelta(seconds=TICK_S * 2)
    state, ctx, _ = tick(load, state, sim, later, None, desired=Desired.COMFORT, setpoint_delta=1.0)
    _, up = load.apply(grant(RATED_W), state, ctx)
    assert up.action is Action.HELD_DWELL, "no upward move within one dwell of a restore (INV-29)"
