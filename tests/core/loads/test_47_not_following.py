"""D4 §9 47 - three read-back deviations in a row: `not_following`, never a failure (D-0689).

The reference house's heat pump 1 took 2 of 11 setpoint writes over five hours with
its health at `ok`. A run of three on one role is a device that isn't listening; the
first read-back that matches ends it. Nothing about allocation changes (INV-22).
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.powerplan.core.engine import DEVICE_ISSUE_AFTER, _device_issues
from custom_components.powerplan.core.loads import Health, Mode
from custom_components.powerplan.core.loads.gate import NOT_FOLLOWING_AFTER, decide, verify
from tests.core.loads.conftest import NOW, budget, gate_config, gate_state
from tests.core.loads.test_07_write_gate_matrix import SETPOINT_22


def _write_and_read(state, *, at, current):
    """Write 22 °C and read back `current` one verify later: the gate's state after both."""
    cfg = gate_config(min_interval_s=0.0, verify_after_s=30.0)
    written = decide(
        SETPOINT_22, current=21.0, mode=Mode.AUTO, cfg=cfg, state=state, budget=budget(), now=at
    )
    after, _ = verify(
        written.gate, current=current, tolerance=cfg.tolerance, now=at + timedelta(seconds=31)
    )
    return after


@pytest.mark.inv("INV-22")
def test_47_a_run_of_deviations_counts_and_one_match_ends_it() -> None:
    """Three misses make a run whose start stays put; one match ends it."""
    state = gate_state()
    starts = []
    for i in range(NOT_FOLLOWING_AFTER):
        state = _write_and_read(state, at=NOW + timedelta(minutes=15 * i), current=21.0)
        starts.append(state.deviating_since)
    assert NOT_FOLLOWING_AFTER == 3
    assert state.deviations == 3
    assert state.failures == 0, "not following is not a failure"
    assert len(set(starts)) == 1, "the run's start stays put while it lasts"

    state = _write_and_read(state, at=NOW + timedelta(hours=1), current=22.0)
    assert state.deviations == 0
    assert state.deviating_since is None


@pytest.mark.inv("INV-22")
def test_47_not_following_raises_and_clears_its_own_notification() -> None:
    """`device_unhealthy` carries it under its own key, and a recovery clears it (D8 §2).

    Since D-0711 the notification waits until the condition has held 30 min.
    """
    edges: dict[str, str] = {}
    away = {"hp1": SimpleNamespace(health=Health(False, False, 0, None, (), None, True, 3))}
    back = {"hp1": SimpleNamespace(health=Health(True, False, 0, None, (), None))}
    assert _device_issues(edges, away, NOW) == ([], [])
    _repairs, (raised,) = _device_issues(edges, away, NOW + DEVICE_ISSUE_AFTER)
    _repairs, (cleared,) = _device_issues(edges, back, NOW + DEVICE_ISSUE_AFTER * 2)
    assert raised.category == "device_unhealthy"
    assert raised.key == "not_following:hp1"
    assert not raised.params.get("cleared")
    assert cleared.key == "not_following:hp1"
    assert cleared.params["cleared"] is True
