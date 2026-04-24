"""D5 §9 15 - a reward event raises the price, for the loads that are enrolled.

D5 §2's answer to "how do events enter": a demand-response reward is not a special
case in the planner, it is a **price**. Turning down during the window earns
`per_kwh`, so consuming during it costs that much more, and every strategy that
already avoids expensive slots avoids the window for free.

The half that has to be tested is the "only" in "only for participating loads": a
household enrols its charger in a flexibility scheme, not its bathroom floor, and a
load that is not enrolled earns nothing by turning down - so its price must not
move, or the planner would be paying it a reward that does not exist.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.pricing import Event, EventKind
from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.strategies.context import with_rewards
from tests.builders.curves import ORDINARY, OSLO, day_bounds
from tests.core.strategies.conftest import (
    NOW,
    curves_of,
    ev_view,
    site_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.powerplan.core.model import Plan, PriceCurve

#: 01:00–03:00 local the next morning - the cheap hours the charger would take.
WINDOW_FROM = day_bounds(ORDINARY + timedelta(days=1))[0] + timedelta(hours=1)
WINDOW_TO = WINDOW_FROM + timedelta(hours=2)

#: A krone a kilowatt-hour for turning down - well above the night's spread.
PER_KWH = Decimal("1.00")


def reward(**kwargs: Any) -> Event:
    """Return a `reward` event over the two cheap night hours (D1 §4)."""
    options: dict[str, Any] = {
        "id": "dr-1",
        "source": "aggregator",
        "kind": EventKind.REWARD,
        "start": WINDOW_FROM,
        "end": WINDOW_TO,
        "issued_at": NOW - timedelta(hours=3),
        "valid_until": WINDOW_TO,
        "payload": {"per_kwh": PER_KWH, "baseline": 0.0},
    }
    options.update(kwargs)
    return Event(**options)


def planned(*, enrolled: bool, events: tuple[Event, ...] = ()) -> Plan:
    """Return the charger's plan, enrolled in the scheme or not."""
    source = volatile_curve()
    view = ev_view(participates_in_events=enrolled)
    return plan_all([view], curves_of(source), site_ctx(events=events), NOW).plans["ev"]


def in_window(plan: Plan) -> list[Any]:
    """Return the plan's slots that fall inside the reward window."""
    return [slot for slot in plan.slots if WINDOW_FROM <= slot.start < WINDOW_TO]


def base(source: PriceCurve, at: datetime) -> Decimal:
    """Return the unmodified composed price at `at`."""
    slot = source.price_at(at)
    assert slot is not None
    return slot.total


def test_15_a_participating_load_sees_the_window_at_a_raised_price() -> None:
    """The reward is added to the price the strategy plans against (D5 §2)."""
    source = volatile_curve()
    plan = planned(enrolled=True, events=(reward(),))

    for slot in in_window(plan):
        assert slot.price == base(source, slot.start) + PER_KWH


def test_15_a_load_that_is_not_enrolled_sees_the_ordinary_price() -> None:
    """Nobody pays it to turn down, so nothing raises its price (§9 15)."""
    source = volatile_curve()
    plan = planned(enrolled=False, events=(reward(),))

    for slot in in_window(plan):
        assert slot.price == base(source, slot.start)


def test_15_the_raised_price_moves_the_plan_out_of_the_window() -> None:
    """A price signal is all it takes: the charger goes elsewhere (D5 §2)."""
    plain = planned(enrolled=True)
    enrolled = planned(enrolled=True, events=(reward(),))

    assert sum(slot.kwh for slot in in_window(plain)) > 0.0
    assert sum(slot.kwh for slot in in_window(enrolled)) == 0.0
    assert plain.planned_kwh == pytest.approx(enrolled.planned_kwh, rel=0.05)


def test_15_the_reward_shows_up_as_its_own_component() -> None:
    """A raised price says why it is raised, for the review sensor and D11."""
    source = volatile_curve()
    raised = with_rewards(source, (reward(),), participates=True)
    inside = next(slot for slot in raised.slots if WINDOW_FROM <= slot.start < WINDOW_TO)
    outside = next(slot for slot in raised.slots if slot.start >= WINDOW_TO)

    assert inside.components["reward"] == PER_KWH
    assert inside.total == sum(inside.components.values())
    assert "reward" not in outside.components


def test_15_a_revoked_or_expired_announcement_changes_nothing() -> None:
    """An event is an announcement, not a fact (D1 §2)."""
    source = volatile_curve()
    revoked = with_rewards(source, (reward(revoked=True),), participates=True)
    expired = with_rewards(
        source, (reward(valid_until=NOW - timedelta(hours=1)),), participates=True
    )

    assert revoked.slots == source.slots
    assert expired.slots == source.slots


def test_15_an_event_of_another_kind_is_not_a_price() -> None:
    """`load_limit` is a constraint for D6 and `day_type` is already in the curve."""
    source = volatile_curve()
    limit = with_rewards(
        source,
        (reward(kind=EventKind.LOAD_LIMIT, payload={"loads": ["ev"], "max_w": 3000}),),
        participates=True,
    )

    assert limit.slots == source.slots


def test_15_a_reward_without_a_rate_is_ignored() -> None:
    """A payload that says nothing about money cannot raise a price (D1 §4)."""
    source = volatile_curve()
    empty = with_rewards(source, (reward(payload={"baseline": 0.0}),), participates=True)

    assert empty.slots == source.slots


def test_15_two_overlapping_rewards_both_count() -> None:
    """Two schemes over one hour are two signals, not the louder of the two."""
    source = volatile_curve()
    both = with_rewards(
        source,
        (reward(), reward(id="dr-2", payload={"per_kwh": Decimal("0.25")})),
        participates=True,
    )
    inside = next(slot for slot in both.slots if WINDOW_FROM <= slot.start < WINDOW_TO)

    assert inside.components["reward"] == PER_KWH + Decimal("0.25")


def test_15_the_window_is_read_in_the_slots_own_time() -> None:
    """A slot is priced by what holds at its start, as every price is (D-0198)."""
    source = volatile_curve()
    raised = with_rewards(source, (reward(),), participates=True)
    edges = [slot for slot in raised.slots if slot.start.astimezone(OSLO).hour in {0, 3}]

    assert edges
    assert all("reward" not in slot.components for slot in edges)
