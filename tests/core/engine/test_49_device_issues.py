"""D8 §9 49 - retried until it answers, a repair meanwhile (D8 §2, §5.9, D-0711).

The reference house's charger drops its BLE link for 20-60 s every few hours
and heat pump 1 ignores a setpoint now and then; each flap pushed a notification,
nine of thirty at night (`design/reviews/field-audit-2026-10.md` §4). The gate
keeps retrying (D4 §8); the household hears after 30 min unbroken, once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.powerplan.core.engine import DEVICE_ISSUE_AFTER, _device_issues
from custom_components.powerplan.core.loads import Health

T0 = datetime(2026, 10, 1, 20, 2, 21, tzinfo=UTC)

OK = Health(
    ok=True, unhealthy=False, failures=0, transient_since=None, stale_roles=(), last_error=None
)
UNHEALTHY = Health(
    ok=False, unhealthy=True, failures=2, transient_since=None, stale_roles=(), last_error="timeout"
)
NOT_FOLLOWING = Health(
    ok=False,
    unhealthy=False,
    failures=0,
    transient_since=None,
    stale_roles=(),
    last_error=None,
    not_following=True,
    deviations=3,
)


def _obs(**health: Health) -> dict[str, SimpleNamespace]:
    return {load_id: SimpleNamespace(health=state) for load_id, state in health.items()}


def test_49_a_flap_raises_nothing() -> None:
    """Unhealthy for 29 min and back: no repair, no notification."""
    edges: dict[str, str] = {}
    assert _device_issues(edges, _obs(ev=UNHEALTHY), T0) == ([], [])
    assert _device_issues(edges, _obs(ev=UNHEALTHY), T0 + timedelta(minutes=29)) == ([], [])
    assert _device_issues(edges, _obs(ev=OK), T0 + timedelta(minutes=29, seconds=10)) == ([], [])
    assert edges == {}


def test_49b_thirty_minutes_raise_one_repair_and_one_notification() -> None:
    """Not following for 31 min: the repair and the notification, once; back: both cleared."""
    edges: dict[str, str] = {}
    _device_issues(edges, _obs(hp1=NOT_FOLLOWING), T0)
    (repair,), (note,) = _device_issues(edges, _obs(hp1=NOT_FOLLOWING), T0 + timedelta(minutes=31))
    assert repair.issue_id == "device_not_following_hp1"
    assert repair.translation_key == "device_not_following"
    assert repair.active
    assert repair.params == {"load": "hp1"}
    assert note.category == "device_unhealthy"
    assert note.key == "not_following:hp1"
    assert note.params["not_following"] is True
    assert note.params["since"] == T0.isoformat()

    later = T0 + timedelta(hours=18)
    assert _device_issues(edges, _obs(hp1=NOT_FOLLOWING), later) == ([], []), "once per raise"

    (cleared,), (dismissed,) = _device_issues(edges, _obs(hp1=OK), later + timedelta(seconds=10))
    assert cleared.issue_id == "device_not_following_hp1"
    assert not cleared.active
    assert dismissed.params["cleared"] is True
    assert edges == {}


def test_49c_the_hold_is_thirty_minutes_unbroken() -> None:
    """A device back for one tick starts the clock again."""
    edges: dict[str, str] = {}
    assert timedelta(minutes=30) == DEVICE_ISSUE_AFTER
    _device_issues(edges, _obs(ev=UNHEALTHY), T0)
    _device_issues(edges, _obs(ev=OK), T0 + timedelta(minutes=20))
    _device_issues(edges, _obs(ev=UNHEALTHY), T0 + timedelta(minutes=21))
    assert _device_issues(edges, _obs(ev=UNHEALTHY), T0 + timedelta(minutes=40)) == ([], [])
    (repair,), (note,) = _device_issues(edges, _obs(ev=UNHEALTHY), T0 + timedelta(minutes=51))
    assert repair.issue_id == "device_unhealthy_ev"
    assert note.key == "unhealthy:ev"


def test_49d_the_other_condition_swaps_the_repair_and_a_removed_load_clears_it() -> None:
    """Unhealthy raised, then not following: one goes, the clock restarts; gone: cleared."""
    edges: dict[str, str] = {}
    _device_issues(edges, _obs(ev=UNHEALTHY), T0)
    _device_issues(edges, _obs(ev=UNHEALTHY), T0 + timedelta(minutes=30))
    (cleared,), _notes = _device_issues(edges, _obs(ev=NOT_FOLLOWING), T0 + timedelta(minutes=31))
    assert cleared.issue_id == "device_unhealthy_ev"
    assert not cleared.active
    assert "device_issue:ev" not in edges

    _device_issues(edges, _obs(hp=UNHEALTHY), T0)
    _device_issues(edges, _obs(hp=UNHEALTHY), T0 + timedelta(minutes=30))
    (gone,), _notes = _device_issues(edges, {}, T0 + timedelta(minutes=31))
    assert gone.issue_id == "device_unhealthy_hp"
    assert not gone.active
    assert edges == {}
