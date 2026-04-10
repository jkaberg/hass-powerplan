"""D4 §9 19 (the receiving half) - the plan's decision reaches the device.

A `heat_capacitor` slot carries two levers (D5 §5.7): a **setpoint delta** for a
`SETPOINT` load and a **desired state** for a `MODE` one. The producing side is
D5's and arrives with WP0.6; what is pinned here is that the levers land - and
that neither of them can push a device past a limit, because a plan paces and
never overrides safety (INV-1, INV-30, INV-56).

The `MODE` kind is also where the reference house's nastiest naming trap lives:
its thermostat offers `Off | Heating mode | Cooling mode (Not implemented) |
Energy saving heating mode`, and the eco option contains the word "heating", so a
substring test for heat picks the eco option (D4 §5.5).

The `SWITCH` kind has no type of its own until WP3.3–3.7, so its two rows - on
when granted, off when shed - are asserted here with the other two kinds.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads import (
    Action,
    ConstantSchedule,
    Desired,
    Hold,
    Reads,
    Role,
    TargetProfile,
)
from custom_components.powerplan.core.loads.kinds import Switch, SwitchCfg, match_option
from tests.core.loads.conftest import (
    FakeThermostat,
    bathroom_target,
    floor_load,
    grant,
    kind_ctx,
    load_ctx,
    load_state,
    mode_kind,
    reads,
)

ZTRM_OPTIONS = (
    "Off",
    "Heating mode",
    "Cooling mode (Not implemented)",
    "Energy saving heating mode",
)


def test_19_a_minus_one_delta_lowers_a_setpoint_load(thermostat: FakeThermostat) -> None:
    """A cheap-slot −1 K reaches the thermostat as `target − 1`."""
    load = floor_load()
    _, result = load.apply(
        grant(w=960.0),
        load_state(),
        load_ctx(reads=thermostat.reads_at(), setpoint_delta=-1.0),
    )
    assert result.action is Action.WRITTEN
    assert result.command is not None
    assert result.command.value == pytest.approx(23.0)


@pytest.mark.inv("INV-56")
def test_19b_a_plus_one_delta_never_exceeds_the_ceiling(thermostat: FakeThermostat) -> None:
    """The covering's cap bounds the plan's fill, whatever it asks for (INV-56).

    The loop is configured with its comfort target already at the 27 °C cap -
    the case a `heat_capacitor` slot would walk straight through.
    """
    load = floor_load(target=_target_at(27.0))
    _, result = load.apply(
        grant(w=960.0),
        load_state(),
        load_ctx(reads=thermostat.reads_at(), setpoint_delta=3.0),
    )
    assert result.command is not None
    assert result.command.value <= 27.0


def test_19c_a_comfort_violation_overrides_the_plan(thermostat: FakeThermostat) -> None:
    """Below the floor the load is served at `target`, whatever the slot says."""
    thermostat.temp_c = 20.0  # under the 21 °C floor
    load = floor_load()
    _, result = load.apply(
        grant(w=960.0),
        load_state(),
        load_ctx(reads=thermostat.reads_at(), setpoint_delta=-1.0, desired=Desired.SHED),
    )
    assert result.command is not None
    assert result.command.value == pytest.approx(24.0)


def test_19d_a_shed_puts_a_mode_thermostat_in_the_eco_option() -> None:
    """One `select_option` per change, and no setpoint write on the hot path (§5.5)."""
    kind = mode_kind()
    ctx = kind_ctx(
        reads=_select_reads("Heating mode"),
        shed=True,
        stage=3,
        target=24.0,
        floor=21.0,
    )
    command = kind.command(kind.quantise(0.0, ctx), grant(w=0.0, shed=True, stage=3), ctx)
    assert not isinstance(command, Hold)
    assert [write.role for write in command.writes] == [Role.MODE_SELECT]
    assert command.value == "Energy saving heating mode"
    assert command.urgent, "a stage-3 shed on a slab must land now (§5.10)"


def test_19e_comfort_comes_back_as_the_heating_option() -> None:
    """`release()` and a cleared shed both restore the comfort option (§5.5)."""
    kind = mode_kind()
    ctx = kind_ctx(reads=_select_reads("Energy saving heating mode"), target=24.0)
    command = kind.command(kind.quantise(960.0, ctx), grant(w=960.0), ctx)
    assert not isinstance(command, Hold)
    assert command.value == "Heating mode"

    restored = kind.restore_command(ctx)
    assert not isinstance(restored, Hold)
    assert restored.value == "Heating mode"
    assert restored.urgent


def test_19f_a_violated_floor_beats_the_plans_shed_on_a_mode_load() -> None:
    """A comfort violation always yields the comfort option (§5.5)."""
    kind = mode_kind()
    ctx = kind_ctx(
        reads=_select_reads("Energy saving heating mode"),
        shed=True,
        comfort_violated=True,
        target=24.0,
    )
    command = kind.command(kind.quantise(0.0, ctx), grant(w=0.0, shed=True, stage=3), ctx)
    assert not isinstance(command, Hold)
    assert command.value == "Heating mode"


def test_19g_the_eco_option_contains_the_word_heating() -> None:
    """The asymmetric fallback, which is the whole point of `match_option` (§5.5)."""
    assert match_option("eco", ZTRM_OPTIONS) == "Energy saving heating mode"
    assert match_option("heat", ZTRM_OPTIONS) == "Heating mode"
    assert match_option("Heating mode", ZTRM_OPTIONS) == "Heating mode"
    assert match_option("eco", ("Off", "Comfort"), fuzzy=True) is None
    assert match_option("heat", ("Off", "Heating mode"), fuzzy=False) is None


def test_19h_a_select_without_the_option_holds_and_says_what_it_offers() -> None:
    """Firmware renamed the options: `unhealthy` with the options seen (D4 §8)."""
    kind = mode_kind()
    ctx = kind_ctx(reads=_select_reads("Kalt", options=("Kalt", "Varmt")), target=24.0)
    outcome = kind.command(kind.quantise(960.0, ctx), grant(w=960.0), ctx)
    assert isinstance(outcome, Hold)
    assert "Kalt" in outcome.reason


def test_19i_a_switch_load_is_on_when_granted_and_off_when_shed() -> None:
    """§5.6: on if granted at least the nameplate and not shed, off if shed."""
    kind = Switch(SwitchCfg(on_at_w=1000.0, min_on_s=600.0, min_off_s=600.0))
    off_now = kind_ctx(reads=_switch_reads("off"), on_at_w=1000.0)

    on = kind.quantise(1200.0, off_now)
    assert on.value is True
    assert on.effective_w == pytest.approx(1000.0)

    short = kind.quantise(400.0, off_now)
    assert short.value is False
    assert short.effective_w == pytest.approx(0.0)

    shed = kind.quantise(1200.0, kind_ctx(reads=_switch_reads("on"), shed=True, on_at_w=1000.0))
    assert shed.value is False

    command = kind.command(on, grant(w=1200.0), off_now)
    assert not isinstance(command, Hold)
    assert command.want_on is True
    assert kind.current(_switch_reads("on")) == "on"
    assert kind.dwell_s() == (600.0, 600.0)


def test_19j_an_inverted_relay_writes_the_other_way_round() -> None:
    """`inverted` for a normally-closed relay (§5.6)."""
    kind = Switch(SwitchCfg(on_at_w=1000.0, inverted=True))
    ctx = kind_ctx(reads=_switch_reads("on"), on_at_w=1000.0)
    assert kind.quantise(1200.0, ctx).value is False
    assert kind.quantise(0.0, ctx).value is True
    restored = kind.restore_command(ctx)
    assert not isinstance(restored, Hold)
    assert restored.value is False


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _select_reads(current: str, options: tuple[str, ...] = ZTRM_OPTIONS) -> Reads:
    """Return the reads of a thermostat whose select is on `current`."""
    return reads(texts={Role.MODE_SELECT: current}, options={Role.MODE_SELECT: options})


def _switch_reads(state: str) -> Reads:
    """Return the reads of a relay in `state`."""
    return reads(texts={Role.SWITCH: state}, numbers={Role.POWER: 0.0})


def _target_at(comfort: float) -> TargetProfile:
    """Return a target profile whose comfort already sits at its ceiling."""
    return bathroom_target(schedule=ConstantSchedule(comfort), comfort_default=comfort)
