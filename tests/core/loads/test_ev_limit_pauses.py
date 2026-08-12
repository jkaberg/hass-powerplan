"""A charger whose limit is its switch - Zaptec and the Easee cloud (D4 §5.9, D-0372).

Neither cloud charger offers powerplan a switch it may use: the Zaptec *Charging*
switch leaves the charger in `connected_finished` when turned off, and the Easee
cloud's *Charger enabled* is a flash setting. Both pause below 6 A and resume at
6 A or more on the limit alone ("Set it to less than 6A when you want charging to
pause and set it to 6A or more when you want it to charge", easee_hass
ChargingControl wiki; Zaptec README, "Prevent charging auto start").

So a profile that says so (`MatchResult.capabilities` ∋ `limit_pauses`) has the
`ev` type materialise `limit_pauses = True`, and the type then:

* builds its `MODULATE` kind with no enable role - a stop is one write, 0 A;
* reads "enabled" off the limit - at or above the floor is a charger that may
  draw, so a running session is still floored at 6 A rather than held where it
  was, and a paused one resumes with a limit and nothing else.

A charger with a switch (the Bluetooth Easee) is unchanged: the parameter
defaults to off and is absent from every load materialised before it existed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from custom_components.powerplan.core.loads import Action, Role, device_types
from custom_components.powerplan.core.loads.questionnaire import QCtx
from tests.core.loads.conftest import (
    EV_PARAMS,
    NOW,
    ev_load,
    grant,
    load_ctx,
    load_state,
    reads,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import Load, Reads

W_PER_AMP = 230.0


def limit_only(**params: object) -> Load:
    """Build the `ev` load a limit-only charger materialises."""
    return ev_load(params={**EV_PARAMS, "limit_pauses": True, **params})


def charger_reads(limit_a: float, status: str = "charging", power_w: float = 0.0) -> Reads:
    """Return what a limit-only charger reports: no enable role at all."""
    return reads(
        numbers={
            Role.CURRENT_SET: limit_a,
            Role.POWER: power_w,
            Role.SOC: 40.0,
        },
        texts={Role.STATUS: status},
    )


def test_the_capability_materialises_the_parameter() -> None:
    """`limit_pauses` comes from the match's capabilities, off by default (INV-66)."""
    ev = device_types.get("ev")
    answers = ev.questionnaire.validate({}, QCtx())

    assert ev.derive(answers, QCtx()).params["limit_pauses"] is False
    with_it = QCtx(capabilities=frozenset({"limit_pauses"}))
    assert ev.derive(ev.questionnaire.validate({}, with_it), with_it).params["limit_pauses"] is True


def test_the_kind_has_no_enable_role() -> None:
    """A charger with a switch keeps it; a limit-only charger has none to write."""
    assert limit_only().kind.cfg.enable_role is None  # type: ignore[attr-defined]
    assert ev_load().kind.cfg.enable_role is Role.ENABLE  # type: ignore[attr-defined]


def test_a_stop_is_one_write_at_zero_amps() -> None:
    """An authorised stop parks the limit at 0 A and names no switch."""
    load = limit_only()
    ctx = load_ctx(reads=charger_reads(16.0, power_w=3680.0))
    state, _ = load.observe(load_state(), ctx)

    _, result = load.apply(grant(0.0, stop_ok=True), state, ctx)

    assert result.action is Action.WRITTEN
    assert result.command is not None
    assert [(write.role, write.value) for write in result.command.writes] == [
        (Role.CURRENT_SET, 0.0)
    ]


def test_a_paused_charger_resumes_on_the_limit_alone() -> None:
    """0 A with a car on the cable: a 10 A grant is one write, and it is not urgent."""
    load = limit_only()
    ctx = load_ctx(reads=charger_reads(0.0, status="car_connected"))
    state, observation = load.observe(load_state(), ctx)
    assert observation.demand.wants

    _, result = load.apply(grant(10.0 * W_PER_AMP), state, ctx)

    assert result.action is Action.WRITTEN
    assert result.command is not None
    assert [(write.role, write.value) for write in result.command.writes] == [
        (Role.CURRENT_SET, 10.0)
    ]
    assert not result.command.urgent, "a resume is a raise: row 6 holds it (D4 §5.10)"


def test_a_running_session_is_floored_not_held() -> None:
    """Under 6 A without a stop: 6 A, session kept - never the old 16 A standing."""
    load = limit_only()
    ctx = load_ctx(reads=charger_reads(16.0, power_w=3680.0))
    state, _ = load.observe(load_state(), ctx)

    _, result = load.apply(grant(2.0 * W_PER_AMP), state, ctx)

    assert result.action is Action.WRITTEN
    assert result.command is not None
    assert result.command.value == 6.0
    assert result.command.urgent


def test_a_paused_charger_under_a_small_grant_stays_paused() -> None:
    """0 A and a 2 A grant: a stopped charger stays stopped, no write."""
    load = limit_only()
    ctx = load_ctx(reads=charger_reads(0.0, status="car_connected"))
    state, _ = load.observe(load_state(), ctx)

    _, result = load.apply(grant(2.0 * W_PER_AMP), state, ctx)

    assert result.action is Action.SAME


def test_the_blocked_notice_reads_the_limit_as_the_switch() -> None:
    """Granted at 10 A and drawing nothing for three minutes: blocked, with no switch to ask."""
    load = limit_only()
    first = load_ctx(reads=charger_reads(10.0, status="car_connected"))
    state, _ = load.observe(load_state(), first)
    later = load_ctx(
        now=NOW + timedelta(minutes=4), reads=charger_reads(10.0, status="car_connected")
    )

    state, _ = load.observe(state, later)

    assert state.blocked_since is not None
    assert state.blocked_reason is not None
