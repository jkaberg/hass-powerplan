"""A charger with no car on it is parked at 0 A (D4 §5.11, D-0444).

With nothing on the cable there is no session to drop, so the type parks the
charger without the allocator's stop: the next car meets 0 A and waits for the
plan instead of drawing the limit the last session left standing. A link loss
is not "no car" and parks nothing (INV-15).
"""

from __future__ import annotations

from custom_components.powerplan.core.loads import Action, Role
from tests.core.loads.conftest import EV_PARAMS, ev_load, grant, load_ctx, load_state, reads


def _charger(status: str, *, amps: float, enable: str = "on") -> object:
    return load_ctx(
        reads=reads(
            numbers={Role.CURRENT_SET: amps, Role.POWER: 0.0},
            texts={Role.STATUS: status, Role.ENABLE: enable},
        )
    )


def test_no_car_parks_the_switch_off_and_the_limit_at_zero() -> None:
    """16 A standing and the switch on, nobody plugged in: switch off, 0 A."""
    load = ev_load()
    ctx = _charger("disconnected", amps=16.0)
    state, observation = load.observe(load_state(), ctx)
    assert observation.demand.wants is False

    _, result = load.apply(grant(0.0), state, ctx)

    assert result.action is Action.WRITTEN
    assert result.command is not None
    assert {(write.role, write.value) for write in result.command.writes} == {
        (Role.ENABLE, False),
        (Role.CURRENT_SET, 0.0),
    }


def test_a_limit_only_charger_parks_on_the_limit_alone() -> None:
    """Zaptec and the Easee cloud: one write, 0 A, urgent past the 900 s interval."""
    load = ev_load(params={**EV_PARAMS, "limit_pauses": True})
    ctx = _charger("disconnected", amps=32.0)
    state, _ = load.observe(load_state(), ctx)

    _, result = load.apply(grant(0.0), state, ctx)

    assert result.action is Action.WRITTEN
    assert result.command is not None
    assert [(write.role, write.value) for write in result.command.writes] == [
        (Role.CURRENT_SET, 0.0)
    ]
    assert result.command.urgent


def test_already_parked_writes_nothing() -> None:
    """0 A and the switch off: the gate sends nothing the device already holds."""
    load = ev_load(params={**EV_PARAMS, "limit_pauses": True})
    ctx = _charger("disconnected", amps=0.0)
    state, _ = load.observe(load_state(), ctx)

    _, result = load.apply(grant(0.0), state, ctx)

    assert result.action is not Action.WRITTEN


def test_a_link_loss_parks_nothing() -> None:
    """`offline` is not `disconnected`: blindness never writes a stop (INV-15)."""
    load = ev_load()
    ctx = _charger("offline", amps=16.0)
    state, _ = load.observe(load_state(), ctx)

    _, result = load.apply(grant(0.0), state, ctx)

    assert result.action is not Action.WRITTEN
