"""D4 §5.11 as completed in WP2.3: the connected set, the calendar deadline, the blocked charger.

What WP0.5 left open on the `ev` type and the plan row names: `de_authorizing`
is a car on the cable; a bound calendar's next event is a departure, and the
earlier of it and the weekday table is the deadline; a charger granted and
enabled that draws nothing for three minutes says why, once per reason.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.base import LoadState
from custom_components.powerplan.core.loads.targets import CalendarEvent
from custom_components.powerplan.core.loads.types.ev import (
    BLOCKED_AFTER_S,
    CONNECTED_STATUSES,
    Ev,
)
from custom_components.powerplan.core.model import Urgency
from tests.core.loads.conftest import EV_PARAMS, NOW, OSLO, ev_load, load_ctx, reads

if TYPE_CHECKING:
    import pytest

TYPE = Ev()


def _charger(
    at: datetime,
    *,
    status: str,
    amps: float,
    power_w: float,
    blocked: str | None = None,
    calendar: tuple[CalendarEvent, ...] = (),
):
    texts = {Role.STATUS: status, Role.ENABLE: "on"}
    if blocked is not None:
        texts[Role.BLOCKED_BY] = blocked
    return load_ctx(
        now=at,
        reads=reads(
            at,
            numbers={Role.CURRENT_SET: amps, Role.POWER: power_w, Role.SOC: 40.0},
            texts=texts,
        ),
        zone=OSLO,
        calendar=calendar,
    )


# --------------------------------------------------------------------------- #
# de_authorizing is a car on the cable
# --------------------------------------------------------------------------- #


def test_22_de_authorizing_is_connected() -> None:
    """The charger is revoking an RFID authorisation with the cable in (D-0281)."""
    assert "de_authorizing" in CONNECTED_STATUSES
    ctx = _charger(NOW, status="de_authorizing", amps=0.0, power_w=0.0)
    assert TYPE.connected(ctx) is True
    demand = TYPE.demand(ev_load(), LoadState(), ctx)
    assert demand.wants is True


# --------------------------------------------------------------------------- #
# the calendar is a departure too
# --------------------------------------------------------------------------- #


def test_22b_the_earlier_of_the_table_and_the_calendar_is_the_deadline() -> None:
    """A 05:30 trip in the calendar beats the table's 07:00; a later one does not."""
    load = ev_load()
    local = NOW.astimezone(OSLO)
    table_next = TYPE.next_departure(load, load_ctx(now=NOW, zone=OSLO))
    assert table_next is not None

    early = table_next - timedelta(hours=1, minutes=30)
    ctx = load_ctx(
        now=NOW,
        zone=OSLO,
        calendar=(CalendarEvent(start=early, end=early + timedelta(hours=8), summary="Trip"),),
    )
    assert TYPE.next_departure(load, ctx) == early

    late = table_next + timedelta(hours=3)
    ctx = load_ctx(
        now=NOW,
        zone=OSLO,
        calendar=(CalendarEvent(start=late, end=late + timedelta(hours=1), summary="Dentist"),),
    )
    assert TYPE.next_departure(load, ctx) == table_next

    # An event already begun is not a departure; one on a table-less load still is.
    begun = CalendarEvent(start=local - timedelta(hours=1), end=local + timedelta(hours=1))
    assert TYPE.next_departure(load, load_ctx(now=NOW, zone=OSLO, calendar=(begun,))) == table_next
    bare = ev_load(params={**EV_PARAMS, "departures": {}})
    assert TYPE.next_departure(bare, load_ctx(now=NOW, zone=OSLO)) is None
    ctx = _charger(
        NOW,
        status="awaiting_start",
        amps=0.0,
        power_w=0.0,
        calendar=(CalendarEvent(start=late, end=late),),
    )
    assert TYPE.next_departure(bare, ctx) == late
    demand = TYPE.demand(bare, LoadState(), ctx)
    assert demand.deadline == late
    assert demand.urgency is Urgency.DEADLINE


# --------------------------------------------------------------------------- #
# a charger granted and drawing nothing says why, once
# --------------------------------------------------------------------------- #


def test_22c_a_blocked_charger_is_logged_once_per_reason(caplog: pytest.LogCaptureFixture) -> None:
    """Three minutes granted, enabled and idle → one WARNING naming `blocked_by`; drawing clears it."""
    load = ev_load()
    state = LoadState()
    caplog.set_level(logging.WARNING, logger="custom_components.powerplan.core.loads.types.ev")

    at = NOW
    for _ in range(int(BLOCKED_AFTER_S / 10.0) + 2):
        ctx = _charger(
            at, status="awaiting_start", amps=16.0, power_w=0.0, blocked="waiting_in_queue"
        )
        state = TYPE.latch(load, state, ctx)
        at += timedelta(seconds=10.0)
    assert state.blocked_since == NOW
    assert state.blocked_reason == "waiting_in_queue"
    warnings = [record for record in caplog.records if "charging blocked by" in record.message]
    assert len(warnings) == 1, [record.message for record in warnings]
    assert "waiting_in_queue" in warnings[0].message
    demand = TYPE.demand(load, state, ctx)
    assert "blocked by waiting_in_queue" in demand.reason

    # The reason changes: one more line. The car starts drawing: the note clears.
    ctx = _charger(at, status="awaiting_start", amps=16.0, power_w=0.0, blocked="limited_by_ev")
    state = TYPE.latch(load, state, ctx)
    assert state.blocked_reason == "limited_by_ev"
    warnings = [record for record in caplog.records if "charging blocked by" in record.message]
    assert len(warnings) == 2
    ctx = _charger(at, status="charging", amps=16.0, power_w=3_600.0)
    state = TYPE.latch(load, state, ctx)
    assert state.blocked_since is None
    assert state.blocked_reason is None


def test_22d_an_unenabled_or_ungranted_charger_is_not_blocked() -> None:
    """Zero amps or the enable off is powerplan's own doing, not a blocked charger."""
    load = ev_load()
    at = NOW
    state = LoadState()
    for _ in range(30):
        ctx = _charger(at, status="awaiting_start", amps=0.0, power_w=0.0)
        state = TYPE.latch(load, state, ctx)
        at += timedelta(seconds=10.0)
    assert state.blocked_since is None


def test_22e_the_observation_carries_the_plug_and_the_charge() -> None:
    """`Load.observe` hands the engine what only the type knows: connected, and SoC."""
    load = ev_load()
    _state, observation = load.observe(
        LoadState(), _charger(NOW, status="charging", amps=16.0, power_w=3_600.0)
    )
    assert observation.connected is True
    assert observation.soc == 40.0
    _state, observation = load.observe(
        LoadState(), _charger(NOW, status="disconnected", amps=0.0, power_w=0.0)
    )
    assert observation.connected is False
    _state, observation = load.observe(
        LoadState(), _charger(NOW, status="offline", amps=0.0, power_w=0.0)
    )
    assert observation.connected is None
