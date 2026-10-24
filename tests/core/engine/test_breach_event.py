"""D8 §5.6's `breach` for the site window: `excess_w` says by how much it went over.

A window breach is either comfort grants over the allowance (`breach_w`) or a
measured total over it (`deficit_w`); the live site logged five of the second
kind with `excess_w: 0`.
"""

from __future__ import annotations

from custom_components.powerplan.core.allocation.ladder import LadderState
from custom_components.powerplan.core.allocation.report import AllocReport
from custom_components.powerplan.core.engine import EventKind, _domain_events


def _breach(report: AllocReport) -> dict[str, object]:
    events = _domain_events({}, LadderState(), report, {}, {})
    (event,) = (event for event in events if event.kind is EventKind.BREACH)
    return dict(event.data)


def test_a_measured_deficit_is_the_excess() -> None:
    """Nothing granted over, 1.2 kW measured over: the excess is 1.2 kW."""
    data = _breach(AllocReport(p_free_w=0.0, deficit_w=1200.0))
    assert data["excess_w"] == 1200.0
    assert data["breach_w"] == 0.0


def test_comfort_over_the_allowance_is_the_excess() -> None:
    """Comfort granted 800 W over, measured 300 W over: the larger is the excess."""
    data = _breach(AllocReport(p_free_w=0.0, breach_w=800.0, deficit_w=300.0))
    assert data["excess_w"] == 800.0
