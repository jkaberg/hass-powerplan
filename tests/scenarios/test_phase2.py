"""D9 §5.3 - D4's row `ble_flaps` and D6's row `circuit_garage_32a`.

`ble_flaps`: transient, not failure; no 0 A writes; no session dropped.

The winter evening with the charger's Bluetooth link gone twice for fifteen
minutes, on top of the simulator's own seeded drops. A lost link is expressed
as `Reads` nobody can vouch for (D4 §5.11): the load goes stale, the gate calls
the missing read-back a transient, and the first tick after the reconnect
re-arms from what the charger now says. Nothing in that may count as a device
failure, write a zero the car reads as a stop, or end a session.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tests.scenarios import catalogue
from tests.scenarios.cache import cached
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

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(catalogue.ble_flaps(), trail), trail

    return cached(__file__, "flapped", run)


#: How long after the link is back the load may still say unhealthy: the next
#: successful write resets the failures (D4 §9 7), and a write is due within a
#: poll or two of the reconnect.
RECOVERY = timedelta(minutes=5)


@pytest.mark.xdist_group(name="phase2_flapped")
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


@pytest.mark.xdist_group(name="phase2_flapped")
@pytest.mark.inv("INV-28")
def test_no_zero_amp_write_and_no_session_dropped(flapped: tuple[ScenarioResult, _Trail]) -> None:
    """D9 §5.3's other two claims: the cliff is never written and the car keeps its session."""
    result, _trail = flapped
    assert result.zero_amp_writes == 0
    assert result.sessions_dropped == 0
    assert result.writes.get("ev", 0) > 0, "the charger was steered through the evening"


# --------------------------------------------------------------------------- #
# D6's row `circuit_garage_32a`: the garage fuse, the sauna and the guest
# --------------------------------------------------------------------------- #

#: The site's three-phase 230 V: watts per amp of a three-phase load (D3 §5.1).
W_PER_AMP_3P = 230.0 * 3**0.5
GARAGE_W = 32.0 * W_PER_AMP_3P
#: The charger's 6 A floor on three phases (D4 §5.11).
EV_FLOOR_W = 6.0 * W_PER_AMP_3P
#: How long the charger may take to yield once the household lights the sauna:
#: the Bluetooth poll and one write (D4 §5.11), three ticks.
YIELD = timedelta(seconds=30)
SAUNA_LIT = catalogue.GARAGE_START.replace(hour=19, minute=0, second=0)
SAUNA_OFF = SAUNA_LIT + timedelta(minutes=90)
GUEST_FROM = catalogue.GARAGE_GUEST_AT
GUEST_TO = GUEST_FROM + timedelta(seconds=catalogue.GARAGE_GUEST_S)


@pytest.fixture(scope="module")
def garage() -> tuple[ScenarioResult, _Trail]:
    """Run the sauna evening with the garage circuit once for the module."""

    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(catalogue.circuit_garage_32a(), trail), trail

    return cached(__file__, "garage", run)


def _garage_rows(trail: _Trail) -> list[tuple[Any, Snapshot, Any]]:
    return [
        (now, snapshot, snapshot.alloc.circuits["garage"])
        for now, snapshot in trail.rows
        if "garage" in snapshot.alloc.circuits
    ]


@pytest.mark.xdist_group(name="phase2_garage")
@pytest.mark.inv("INV-60")
def test_the_charger_and_the_sauna_never_exceed_the_garage_fuse(
    garage: tuple[ScenarioResult, _Trail],
) -> None:
    """The circuit binds the charger before the site does, and the sauna keeps its evening.

    Before the sauna the charger draws the whole 32 A; when the household
    lights the sauna the charger yields within a poll, and the two share the
    fuse for the rest of the session - the sauna is never shed for it.
    """
    result, trail = garage
    rows = _garage_rows(trail)
    assert rows, "the garage circuit never reached the report"
    assert result.engine_failures == 0
    assert result.over_target == 0
    assert result.sessions_dropped == 0
    assert result.zero_amp_writes == 0
    assert all(row.sub_meter for _now, _snapshot, row in rows), "the clamp is what is read"
    assert all(row.limit_w == pytest.approx(GARAGE_W) for _now, _snapshot, row in rows)

    # The circuit, not the site, holds the charger: capped by the garage all evening.
    capped = [
        snapshot.loads["ev"].capped_by
        for now, snapshot, _row in rows
        if now < SAUNA_LIT and snapshot.loads["ev"].granted_w > 0.0
    ]
    assert capped
    assert all("garage" in keys for keys in capped)

    # Over the fuse only while the charger yields to the sauna the household lit.
    over = [
        now
        for now, _snapshot, row in rows
        if row.measured_w is not None
        and row.measured_w > row.limit_w + 1.0
        and not (GUEST_FROM <= now < GUEST_TO)
    ]
    assert all(SAUNA_LIT <= now <= SAUNA_LIT + YIELD for now in over), over
    assert len(over) <= 3, over
    # And the session is the sauna's: on, never shed, from the heat-up on.
    session = [
        (now, snapshot.loads["sauna"])
        for now, snapshot, _row in rows
        if SAUNA_LIT + YIELD < now < SAUNA_OFF
    ]
    assert session
    assert all(not sauna.shed for _now, sauna in session), [
        now for now, sauna in session if sauna.shed
    ][:5]
    assert all((sauna.measured_w or 0.0) > 0.0 for _now, sauna in session)


@pytest.mark.xdist_group(name="phase2_garage")
@pytest.mark.inv("INV-60")
def test_a_circuit_breach_sheds_the_charger_only(garage: tuple[ScenarioResult, _Trail]) -> None:
    """The guest's car and the heater breach the garage: the charger goes, the house does not.

    A fuse breach scoped to the circuit (D6 §5.8): the charger is taken to its
    floor at stage 4 - its stop vetoed by the plan horizon, it may not be cut
    to zero (D6 §9 23) - the site's ladder stays where it was, the tank and the
    loops keep their grants, and one `breach` event with `breach = "circuit"`
    is fired for the edge. When the guest leaves the charger is back at the fuse.
    """
    result, trail = garage
    rows = _garage_rows(trail)
    before = next((snapshot, row) for now, snapshot, row in reversed(rows) if now < GUEST_FROM)
    guest = [(now, snapshot, row) for now, snapshot, row in rows if GUEST_FROM <= now < GUEST_TO]
    assert guest
    first_now, first, _first_row = guest[0]
    assert first_now - GUEST_FROM < timedelta(seconds=15)

    # The breach is seen, once, and on the tick it appears.
    assert result.circuit_breaches == 1
    assert _first_row.breach is True

    # The charger: stage 4, at its floor, capped by the garage - the whole time.
    for _now, snapshot, row in guest[2:]:
        ev = snapshot.loads["ev"]
        assert ev.stage == 4, (_now, ev)
        assert ev.granted_w <= EV_FLOOR_W + 1.0, (_now, ev.granted_w)
        assert "garage" in ev.capped_by
        # What the members could not give back is the floor, and nothing more.
        assert row.measured_w is not None
        assert row.measured_w - row.limit_w <= EV_FLOOR_W + 1.0
    # The sauna is off (the household switched it off at 20:30) and stays off.
    assert all((snapshot.loads["sauna"].measured_w or 0.0) == 0.0 for _now, snapshot, _row in guest)

    # The house: the site's ladder unmoved by the circuit, the other loads' grants
    # on the breach tick what they were the tick before.
    assert before[0].ladder.stage == first.ladder.stage
    assert all(snapshot.ladder.stage == before[0].ladder.stage for _now, snapshot, _row in guest)
    others = [load_id for load_id in first.loads if load_id not in ("ev", "sauna")]
    assert others
    for load_id in others:
        assert first.loads[load_id].granted_w == pytest.approx(before[0].loads[load_id].granted_w)
        assert first.loads[load_id].stage == before[0].loads[load_id].stage

    # The guest leaves: the charger is back above its floor within five minutes.
    after = [
        snapshot.loads["ev"].granted_w
        for now, snapshot, _row in rows
        if GUEST_TO <= now < GUEST_TO + timedelta(minutes=5)
    ]
    assert after
    assert max(after) > EV_FLOOR_W + 1.0
