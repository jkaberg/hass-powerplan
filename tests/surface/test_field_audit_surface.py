"""D8 §9 44, 45, 47 - what the field audit's changes publish (D-0685, D-0689, D-0692)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.powerplan.core.engine import AccountingStatus
from custom_components.powerplan.core.loads.base import Health
from custom_components.powerplan.load_entities import _health
from custom_components.powerplan.sensor import SENSORS, _energy_since, _partial


def test_44_the_projection_sensor_carries_the_expected_beside_it() -> None:
    """The state is the ladder's projection; `expected_kwh` is D7 §5.4's, never read by the ladder."""
    (row,) = [description for description in SENSORS if description.key == "window_projected"]
    snapshot = SimpleNamespace(budget=SimpleNamespace(projected_kwh=2.5514), expected_kwh=1.1177)

    assert row.value(snapshot, None) == 2.551
    assert row.attributes(snapshot, None) == {"expected_kwh": 1.118}


def _health_of(**kwargs: object) -> Health:
    base = {
        "ok": True,
        "unhealthy": False,
        "failures": 0,
        "transient_since": None,
        "stale_roles": (),
        "last_error": None,
    }
    return Health(**{**base, **kwargs})  # type: ignore[arg-type]


def test_45_a_load_that_does_not_follow_says_so_and_is_not_unhealthy() -> None:
    """Heat pump 1 took 2 of 11 writes with `ok` on its health; now it reads `not_following`."""
    since = datetime(2026, 9, 25, 22, 30, tzinfo=UTC)
    following = SimpleNamespace(health=_health_of())
    drifting = SimpleNamespace(
        health=_health_of(not_following=True, deviations=3, deviating_since=since)
    )
    broken = SimpleNamespace(health=_health_of(ok=False, unhealthy=True, not_following=True))

    assert _health(following, None) == "ok"  # type: ignore[arg-type]
    assert _health(drifting, None) == "not_following"  # type: ignore[arg-type]
    assert _health(broken, None) == "unhealthy"  # type: ignore[arg-type]


def test_47_a_month_opened_after_its_first_says_so() -> None:
    """The reference house's ledger opened 25 Sep 09:00 UTC: partial, energy since then."""
    month = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)
    opened = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)

    partial = AccountingStatus(month_start=month, since=opened)
    whole = AccountingStatus(month_start=month, since=datetime(2026, 7, 1, tzinfo=UTC))

    assert _partial(partial) is True
    assert _energy_since(partial) == opened
    assert _partial(whole) is False
    assert _energy_since(whole) == month
