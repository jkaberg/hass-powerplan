"""D4 §9 9 (pure part) - `release()`, and the §5.2 mode machinery it is the edge of.

INV-26: letting go undoes the shed. `release()` runs on mode → off, site → off,
unload and startup, and it ignores the dwell clocks because it is not a control
action. What it must never do is send a value the device already holds.

The site switch is the same thing one level up (PLAN §7 dec. 20): `active = off`
releases every load on the edge, then behaves as every load in `observe` -
decisions computed and published, would-be writes logged. The load's own mode is
untouched, which is why the *effective* mode is a function of both.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.loads import (
    Action,
    Mode,
    Role,
    effective_mode,
    expire_force,
    transition,
)
from tests.core.loads.conftest import (
    NOW,
    FakeCharger,
    FakeThermostat,
    ev_load,
    floor_load,
    grant,
    load_ctx,
    load_state,
)


@pytest.mark.inv("INV-26")
def test_09_release_undoes_the_shed_and_ignores_the_dwell(thermostat: FakeThermostat) -> None:
    """A shed at stage 2, then a release one minute later - inside every clock."""
    load = floor_load()
    state, shed = load.apply(
        grant(w=0.0, shed=True, shed_reason="stage 2", stage=2),
        load_state(),
        load_ctx(reads=thermostat.reads_at()),
    )
    thermostat.step(60.0, shed.command)
    assert thermostat.setpoint == pytest.approx(21.5), "the floor plus half the swing (D-0259)"
    assert state.shed_active

    soon = NOW + timedelta(seconds=60)  # min_on 900 s and the interval 600 s both unelapsed
    state, released = load.release(state, load_ctx(now=soon, reads=thermostat.reads_at(soon)))
    assert released.action is Action.WRITTEN
    thermostat.step(1.0, released.command, at=soon)
    assert thermostat.setpoint == pytest.approx(24.0)
    assert not state.shed_active
    assert state.shed_since is None


@pytest.mark.inv("INV-26")
def test_09b_release_refuses_to_send_a_value_already_held(thermostat: FakeThermostat) -> None:
    """Row 3 of the matrix binds a release too: idempotent, not merely tidy."""
    thermostat.setpoint = 24.0
    load = floor_load()
    state, released = load.release(
        load_state(shed_active=True, shed_since=NOW), load_ctx(reads=thermostat.reads_at())
    )
    assert released.action is Action.SAME
    assert released.command is None
    assert thermostat.writes == []
    assert not state.shed_active, "the shed is undone whether or not a write was needed"


@pytest.mark.inv("INV-26")
def test_09c_a_load_that_never_wrote_has_nothing_to_release(thermostat: FakeThermostat) -> None:
    """Nothing was shed, so nothing is undone - and no write is sent to find out."""
    _, released = floor_load().release(load_state(), load_ctx(reads=thermostat.reads_at()))
    assert released.action is Action.SAME
    assert released.command is None
    assert thermostat.writes == []


@pytest.mark.inv("INV-26")
def test_09d_release_hands_the_charger_back_at_its_own_maximum(charger: FakeCharger) -> None:
    """A charger handed back is never handed back at 0 A (the ancestor's README)."""
    charger.enabled = False
    charger.limit_a = 0.0
    charger.status = "awaiting_start"
    state, released = ev_load().release(
        load_state(shed_active=True, shed_since=NOW), load_ctx(reads=charger.reads_at())
    )
    assert released.action is Action.WRITTEN
    charger.step(30.0, released.command)
    assert charger.limit_a == pytest.approx(32.0)
    assert charger.enabled
    assert not state.shed_active


@pytest.mark.inv("INV-26")
@pytest.mark.parametrize("target", [Mode.OFF, Mode.OBSERVE, Mode.DELEGATED])
def test_09e_every_mode_edge_that_lets_go_releases_first(target: Mode) -> None:
    """§5.2: `off`, `observe` and `delegated` all release on the way in (INV-26)."""
    edge = transition(load_state(mode=Mode.AUTO, shed_active=True), target, NOW)
    assert edge.release
    assert edge.state.mode is target


def test_09f_coming_back_restores_and_re_provisions() -> None:
    """§5.2: `off` → `auto` re-runs provisions and restores the comfort target."""
    edge = transition(load_state(mode=Mode.OFF), Mode.AUTO, NOW)
    assert edge.restore
    assert edge.reprovision
    assert not edge.release


def test_09g_force_records_when_it_started() -> None:
    """§5.2: `auto` → `force` stamps `force_since`, which is what expires it."""
    edge = transition(load_state(), Mode.FORCE, NOW)
    assert edge.state.force_since == NOW
    assert not edge.release


@pytest.mark.inv("INV-57")
def test_09h_a_force_clears_itself_at_its_maximum_duration() -> None:
    """INV-57: a force always carries a maximum duration and clears itself."""
    state = transition(load_state(force_max_h=6.0), Mode.FORCE, NOW).state
    inside = expire_force(state, NOW + timedelta(hours=5, minutes=59))
    assert inside.mode is Mode.FORCE
    assert inside.force_since == NOW

    expired = expire_force(state, NOW + timedelta(hours=6, seconds=1))
    assert expired.mode is Mode.AUTO
    assert expired.force_since is None


@pytest.mark.inv("INV-57")
def test_09i_a_force_without_a_maximum_is_not_representable() -> None:
    """`force_max_h` is a property of the mode, not an automation that may be missing."""
    with pytest.raises(ValueError, match="force_max_h"):
        load_state(force_max_h=0.0)


def test_09j_the_effective_mode_folds_the_site_switch_in() -> None:
    """PLAN §7 dec. 20: site off ⇒ `observe`; load off stays `off`."""
    assert effective_mode(Mode.AUTO, site_active=True) is Mode.AUTO
    assert effective_mode(Mode.AUTO, site_active=False) is Mode.OBSERVE
    assert effective_mode(Mode.FORCE, site_active=False) is Mode.OBSERVE
    assert effective_mode(Mode.DELEGATED, site_active=False) is Mode.OBSERVE
    assert effective_mode(Mode.OFF, site_active=False) is Mode.OFF
    assert effective_mode(Mode.OFF, site_active=True) is Mode.OFF
    assert effective_mode(Mode.OBSERVE, site_active=True) is Mode.OBSERVE


def test_09k_an_inactive_site_decides_and_publishes_but_does_not_write(
    thermostat: FakeThermostat,
) -> None:
    """INV-44 in miniature: the decision exists, the write does not."""
    load = floor_load()
    state, observation = load.observe(
        load_state(), load_ctx(reads=thermostat.reads_at(), site_active=False)
    )
    assert observation.demand.wants, "the decision is computed whatever the switch says"

    _, result = load.apply(
        grant(w=960.0), state, load_ctx(reads=thermostat.reads_at(), site_active=False)
    )
    assert result.action is Action.OBSERVE
    assert result.command is None
    assert result.value == pytest.approx(24.0), "the would-be write is logged"
    assert thermostat.writes == []


def test_09l_a_delegated_load_never_writes_but_reserves_its_nameplate(
    thermostat: FakeThermostat,
) -> None:
    """§2: read like any other load, reserve the nameplate, never write."""
    load = floor_load()
    state = load_state(mode=Mode.DELEGATED)
    _, result = load.apply(grant(w=960.0), state, load_ctx(reads=thermostat.reads_at()))
    assert result.action is Action.DELEGATED
    assert result.command is None
    assert thermostat.writes == []

    view = load.view_for_meter(state, load_ctx(reads=thermostat.reads_at()))
    assert view.load_id == "loop_bath"
    assert view.measured_w == pytest.approx(0.0)


def test_09m_release_is_the_only_writer_in_mode_off(thermostat: FakeThermostat) -> None:
    """Row 2 of the matrix: `off` writes only through `release()` (§5.10)."""
    load = floor_load()
    off = load_state(mode=Mode.OFF, shed_active=True, shed_since=NOW)
    _, held = load.apply(grant(w=960.0), off, load_ctx(reads=thermostat.reads_at()))
    assert held.action is Action.SAME
    assert thermostat.writes == []

    _, released = load.release(off, load_ctx(reads=thermostat.reads_at()))
    assert released.action is Action.WRITTEN
    assert released.command is not None
    assert released.command.writes[0].role is Role.SETPOINT
