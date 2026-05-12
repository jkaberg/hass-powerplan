"""D9 §5.3 - D4's row `ble_flaps`: transient, not failure; no 0 A writes; no session dropped.

The winter evening with the charger's Bluetooth link gone twice for fifteen
minutes, on top of the simulator's own seeded drops. A lost link is expressed
as `Reads` nobody can vouch for (D4 §5.11): the load goes stale, the gate calls
the missing read-back a transient, and the first tick after the reconnect
re-arms from what the charger now says. Nothing in that may count as a device
failure, write a zero the car reads as a stop, or end a session.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tests.scenarios import catalogue
from tests.scenarios.runner import run_scenario

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Snapshot
    from tests.scenarios.runner import ScenarioResult

pytestmark = pytest.mark.scenario


class _Trail:
    """Every snapshot, for the health of the charger through the flaps."""

    def __init__(self) -> None:
        self.rows: list[tuple[object, Snapshot]] = []

    def __call__(self, now: object, snapshot: Snapshot) -> None:
        self.rows.append((now, snapshot))


@pytest.fixture(scope="module")
def flapped() -> tuple[ScenarioResult, _Trail]:
    """Run the evening once for the module."""
    trail = _Trail()
    return run_scenario(catalogue.ble_flaps(), trail), trail


#: How long after the link is back the load may still say unhealthy: the next
#: successful write resets the failures (D4 §9 7), and a write is due within a
#: poll or two of the reconnect.
RECOVERY = timedelta(minutes=5)


@pytest.mark.inv("INV-22")
@pytest.mark.inv("INV-39")
def test_a_flap_is_a_transient_first_and_the_charger_recovers(
    flapped: tuple[ScenarioResult, _Trail],
) -> None:
    """The lost link is seen, classified a transient before any failure, and forgotten on reconnect.

    D4 §8 counts a failure once a device is unavailable past `transient_grace_s`
    and calls the load unhealthy at two - a fifteen-minute flap is far past a
    15 s grace, so the flag *does* go up. What the row forbids is a control
    consequence: the health must follow the link (down only while the link is,
    or within `RECOVERY` of its return), the charger must be steered again,
    and nothing may be written that the car reads as a stop (the next test).
    """
    result, trail = flapped
    assert result.engine_failures == 0
    assert result.house is not None
    charger = result.house.charger
    assert charger is not None
    rows = [
        (now, snapshot.loads["ev"].health) for now, snapshot in trail.rows if "ev" in snapshot.loads
    ]
    assert any(health.stale_roles for _now, health in rows), "the flaps never reached the load"

    # Row 4 of the gate matrix: the first ticks of an injected flap are a transient, not a failure.
    for fault in catalogue.FLAPS:
        before = next(health for now, health in reversed(rows) if now < fault.at)  # type: ignore[operator]
        first_stale = next(
            health
            for now, health in rows
            if now >= fault.at and health.stale_roles  # type: ignore[operator]
        )
        assert first_stale.failures == before.failures, (before, first_stale)

    # The health follows the link, seeded drops and injected flaps alike.
    link_up_since = None
    offenders = []
    for now, health in rows:
        down = charger.offline_at(now)  # type: ignore[arg-type]
        if down:
            link_up_since = None
        elif link_up_since is None:
            link_up_since = now
        if (
            health.unhealthy
            and not down
            and link_up_since is not None
            and now - link_up_since >= RECOVERY  # type: ignore[operator]
        ):
            offenders.append((now, health.failures))
    assert offenders == [], offenders[:5]
    # And the link came back at least once with the health following.
    healthy_after = [
        now for now, health in rows if not health.unhealthy and not charger.offline_at(now)
    ]  # type: ignore[arg-type]
    assert healthy_after


@pytest.mark.inv("INV-28")
def test_no_zero_amp_write_and_no_session_dropped(flapped: tuple[ScenarioResult, _Trail]) -> None:
    """D9 §5.3's other two claims: the cliff is never written and the car keeps its session."""
    result, _trail = flapped
    assert result.zero_amp_writes == 0
    assert result.sessions_dropped == 0
    assert result.writes.get("ev", 0) > 0, "the charger was steered through the evening"
