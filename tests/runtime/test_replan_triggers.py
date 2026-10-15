"""D5 §9 52 / D7 §5.2 - the runtime's side of the replan rule (D-0851, D-0852).

A demand that moves replans at once, for any load type; a site whose price set-up
changed hashes differently, so no restored plan outlives it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.powerplan.core.model import Demand, Mode, Urgency
from custom_components.powerplan.runtime import Runtime, _setup_digest

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _runtime() -> Runtime:
    runtime = Runtime.__new__(Runtime)
    runtime._asked = {}
    runtime._demand_plan_at = None
    return runtime


def _demand(**kwargs: object) -> Demand:
    base = Demand(
        wants=False,
        required_kwh=11.5,
        deadline=NOW + timedelta(hours=17),
        min_w=1380.0,
        max_w=7360.0,
        urgency=Urgency.NONE,
        comfort=None,
        price_sensitive=True,
        reason="",
    )
    return replace(base, **kwargs)


def _snapshot(demand: Demand, mode: Mode = Mode.AUTO) -> SimpleNamespace:
    return SimpleNamespace(loads={"x": SimpleNamespace(demand=demand, mode=mode)})


def test_52_a_device_attaching_replans_any_type() -> None:
    """A device attaching moves the demand, whatever the type."""
    runtime = _runtime()
    assert not runtime._demand_moved(_snapshot(_demand()), NOW)  # first sight: nothing to compare
    assert not runtime._demand_moved(_snapshot(_demand()), NOW + timedelta(minutes=1))
    attached = _demand(wants=True, urgency=Urgency.DEADLINE)
    assert runtime._demand_moved(_snapshot(attached), NOW + timedelta(minutes=2))


def test_52_a_moved_deadline_or_mode_or_requirement_replans() -> None:
    """A moved deadline, mode or requirement past the tolerance replans."""
    for change in (
        {"deadline": NOW + timedelta(hours=20)},
        {"required_kwh": 14.0},
    ):
        runtime = _runtime()
        runtime._demand_moved(_snapshot(_demand()), NOW)
        assert runtime._demand_moved(_snapshot(_demand(**change)), NOW + timedelta(minutes=2))
    runtime = _runtime()
    runtime._demand_moved(_snapshot(_demand()), NOW)
    assert runtime._demand_moved(_snapshot(_demand(), Mode.FORCE), NOW + timedelta(minutes=2))


def test_52_a_requirement_inside_the_tolerance_does_not() -> None:
    """A requirement inside D5 §5.9's 10 % is not a new question."""
    runtime = _runtime()
    runtime._demand_moved(_snapshot(_demand()), NOW)
    assert not runtime._demand_moved(_snapshot(_demand(required_kwh=12.0)), NOW)


def test_52_replans_are_rate_limited() -> None:
    """One replan per minute (D5 §8)."""
    runtime = _runtime()
    runtime._demand_moved(_snapshot(_demand()), NOW)
    runtime._demand_plan_at = NOW
    assert not runtime._demand_moved(_snapshot(_demand(wants=True)), NOW + timedelta(seconds=30))


def test_52_a_changed_price_set_up_changes_the_digest() -> None:
    """Fixed to spot is a different set-up (D-0851)."""
    fixed = {"prices": {"sources": [{"key": "fixed"}]}, "currency": "NOK"}
    spot = {"prices": {"sources": [{"key": "nordpool_action"}]}, "currency": "NOK"}
    assert _setup_digest(fixed) == _setup_digest(dict(fixed))
    assert _setup_digest(fixed) != _setup_digest(spot)
