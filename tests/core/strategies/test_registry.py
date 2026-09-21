"""The registry, `always`, the replan triggers and the view builder (D5 §3, §6).

Extension is by registry, not by conditional: what is pinned here is that a
strategy is discoverable by key with its own schema, that the config flow can
render one without knowing which strategies exist, and that a load reaches the
planner through one call (`LoadView.of`) rather than a hand-assembled projection.

`always` is the whole of D5's answer for a load with no price elasticity: the
plan says nothing and the allocator controls it against the ceiling (HLD §3).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.loads import CalendarEvent, Mode, device_types
from custom_components.powerplan.core.loads.stores import SlabStore
from custom_components.powerplan.core.loads.stores.base import StoreCtx
from custom_components.powerplan.core.model import PlanMode
from custom_components.powerplan.core.strategies import (
    COMMON_SCHEMA,
    MIN_REPLAN_INTERVAL_S,
    Headroom,
    LoadView,
    ReplanTrigger,
    entry,
    first,
    get,
    keys,
    needs,
    params_of,
    plan_all,
    replan_due,
    supports,
)
from tests.core.strategies.conftest import (
    DEPARTURE,
    NOW,
    bathroom_target,
    curves_of,
    demand,
    ev_load,
    ev_view,
    floor_view,
    site_ctx,
    slab_store,
    volatile_curve,
)


def test_the_registered_roster_is_what_the_flow_offers() -> None:
    """`keys()` is the roster; each entry carries its own fields (D5 §3, §6).

    `arbitrage`/`peak_shave` land with WP5.4, `surplus` with WP7.2; the combinators are not rows at all - they are extras on
    any load (D5 §5.11, `base.py`'s roster comment).
    """
    assert keys() == (
        "always",
        "arbitrage",
        "best_save",
        "cheapest_hours",
        "deadline_fill",
        "heat_capacitor",
        "peak_shave",
        "run_once",
        "schedule",
        "surplus",
    )
    assert supports("ev") == ("always", "cheapest_hours", "deadline_fill", "surplus")

    fields = {field.key for field in entry("deadline_fill").schema}
    assert {"min_block_min", "flat_policy", "prefer_late"} <= fields
    assert {field.key for field in COMMON_SCHEMA} <= fields
    assert all(field.advanced for field in entry("deadline_fill").schema)


#: What every strategy's schema carries: D5 §6's two common knobs and the three
#: combinators, which are extras on any load rather than strategies (§5.11).
COMMON_DEFAULTS = {
    "participate_in_events": False,
    "horizon_h": 48,
    "threshold_off_above": None,
    "threshold_on_below": None,
    "merge_with": None,
    "merge_op": "or",
    "opportunistic": False,
    "opportunistic_below_price": 0,
}


def test_params_of_fills_in_every_default() -> None:
    """A strategy reads its parameters; the defaults live in the schema (D5 §6)."""
    assert params_of("deadline_fill", {}) == {
        "min_block_min": 0,
        "flat_policy": "fill",
        "prefer_late": False,
        **COMMON_DEFAULTS,
    }
    assert params_of("deadline_fill", {"min_block_min": 30})["min_block_min"] == 30
    assert params_of("always", {}) == COMMON_DEFAULTS


def test_every_strategy_takes_the_combinators() -> None:
    """A combinator is an extra on any load, so it is in every schema (§5.11)."""
    for key in keys():
        assert set(COMMON_DEFAULTS) <= {field.key for field in entry(key).schema}, key


def test_every_device_type_offers_only_registered_strategies() -> None:
    """A name in `device_type.strategies` `get()` cannot find crashes the planner.

    The moment a household picks it - `flow/load.py`'s own review-step select
    renders `device_type.strategies` directly (D8 §5.2), with nothing between
    the choice and `plan_all`'s `get(view.strategy)` (D-0308).
    `"observe"` (a mode, `select.<load>_mode`'s own - D4 §5.2) and
    `"opportunistic"` (a combinator toggle, `COMBINATOR_SCHEMA` - §5.11) are
    not, and never were, registered strategies.
    """
    roster = set(keys())
    type_keys = device_types.keys()
    for type_key in type_keys:
        offered = set(device_types.get(type_key).strategies)
        assert offered <= roster, (type_key, offered - roster)


def test_always_defers_to_the_allocator_in_every_slot() -> None:
    """`always` plans nothing at all: `cap_w` is `None`, never `0` (INV-30)."""
    source = volatile_curve()
    view = ev_view(strategy="always")
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    assert plan.mode is PlanMode.NONE
    assert plan.slots == ()
    assert plan.cap_w(NOW) is None
    assert plan.desired_state_at(NOW) is None
    assert plan.reason == "no price steering"
    assert get("always").key == "always"


def test_an_off_load_is_not_planned_and_a_delegated_one_reserves_its_nameplate() -> None:
    """§5.1: `off` reserves nothing; `delegated` reserves its nameplate."""
    source = volatile_curve()
    slots = source.slots_between(NOW, source.slots[-1].end)
    room = Headroom(by_slot={slot.start: 10_000.0 for slot in slots})

    off = plan_all([ev_view(mode=Mode.OFF)], curves_of(source), site_ctx(), NOW, headroom=room)
    assert off.plans == {}
    assert off.headroom_left.w_at(slots[0].start) == 10_000.0

    handed = plan_all(
        [ev_view(mode=Mode.DELEGATED)], curves_of(source), site_ctx(), NOW, headroom=room
    )
    assert handed.plans == {}
    assert handed.headroom_left.w_at(slots[0].start) == 10_000.0 - 32.0 * 230.0


def test_the_site_plan_summarises_what_the_loads_asked_for() -> None:
    """§5.12's site summary: total planned kWh, and who is uncovered."""
    source = volatile_curve()
    tight = demand(required_kwh=400.0, deadline=NOW + timedelta(hours=2))
    site = plan_all([ev_view(demand=tight), floor_view()], curves_of(source), site_ctx(), NOW)

    assert site.uncovered == ("ev",)
    assert site.planned_kwh == pytest.approx(sum(plan.planned_kwh for plan in site.plans.values()))
    assert site.adopted == frozenset({"ev", "loop_bath"})
    assert site.built_at == NOW


def test_a_load_view_is_built_from_the_load_and_its_demand() -> None:
    """D7 hands the planner `LoadView.of(load, demand, mode=…)` (D7 §5.2)."""
    load = ev_load()
    view = LoadView.of(load, demand(), mode=Mode.FORCE, level_now=42.0)

    assert view.load_id == "ev"
    assert view.priority == 10
    assert view.strategy == "deadline_fill"
    assert view.kind == "modulate"
    assert view.nameplate_w == 32.0 * 230.0
    assert view.max_w == demand().max_w
    assert view.min_w == demand().min_w
    assert view.forced
    assert view.plans
    assert view.level_now == 42.0
    assert view.store is load.store


def test_the_replan_rate_limit_lets_a_person_through() -> None:
    """One replan per load per minute, except what somebody is waiting for (§8)."""
    last = NOW

    assert not replan_due(ReplanTrigger.TICK, last, NOW + timedelta(seconds=30))
    assert replan_due(ReplanTrigger.TICK, last, NOW + timedelta(seconds=MIN_REPLAN_INTERVAL_S))
    assert replan_due(ReplanTrigger.FORCE, last, NOW + timedelta(seconds=1))
    assert replan_due(ReplanTrigger.SERVICE, last, NOW + timedelta(seconds=1))
    assert replan_due(ReplanTrigger.CURVE, None, NOW)


def test_deadlines_need_a_level_and_return_the_earliest_first() -> None:
    """An unknown temperature is not a zero: no level, no deadline (D4 §4.3)."""
    store = slab_store()
    profile = bathroom_target()

    assert (
        needs(
            profile,
            store,
            level_now=None,
            from_=NOW,
            until=DEPARTURE,
            ctx=StoreCtx(now=NOW),
        )
        == ()
    )
    assert first(()) is None


def test_a_fitted_slab_is_predicted_to_have_cooled_by_the_deadline() -> None:
    """The requirement is computed from where the store *will* be (D5 §2).

    A slab with a fitted loss coefficient coasts towards its floor, so an arrival
    six hours out needs more than the deficit measured now. Without a fit the
    current level is used and the plan understates the need - conservative, and
    the same rule as the store's own loss term (D4 `stores/thermal.py`).
    """
    fitted = SlabStore(area_m2=12.0, screed_mm=40.0, loss_coeff_w_per_k=120.0, max_c=27.0)
    unfitted = slab_store()
    arrival = NOW + timedelta(hours=6)
    calendar = (CalendarEvent(start=arrival, end=arrival + timedelta(hours=2)),)
    ctx = StoreCtx(now=NOW, outdoor_c=-8.0, indoor_c=21.0)

    with_fit = needs(
        bathroom_target(),
        fitted,
        level_now=22.0,
        from_=NOW,
        until=DEPARTURE,
        ctx=ctx,
        calendar=calendar,
    )
    without = needs(
        bathroom_target(),
        unfitted,
        level_now=22.0,
        from_=NOW,
        until=DEPARTURE,
        ctx=ctx,
        calendar=calendar,
    )

    assert with_fit[0].required_kwh > without[0].required_kwh


def test_an_arrival_is_a_deadline_with_the_energy_it_needs() -> None:
    """A calendar arrival is a step-up nobody scheduled (D4 §5.8, D5 §2)."""
    store = slab_store()
    arrival = NOW + timedelta(hours=6)
    found = needs(
        bathroom_target(),
        store,
        level_now=20.0,
        from_=NOW,
        until=DEPARTURE,
        ctx=StoreCtx(now=NOW),
        calendar=(CalendarEvent(start=arrival, end=arrival + timedelta(hours=2), summary="home"),),
    )

    assert [need.source for need in found] == ["arrival"]
    assert first(found) is not None
    assert first(found).at == arrival  # type: ignore[union-attr]
    assert found[0].required_kwh == pytest.approx(store.capacity_kwh_per_unit() * (24.0 - 20.0))
    assert "by" in found[0].reason
