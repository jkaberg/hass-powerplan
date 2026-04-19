"""D6 §5.9 - the breach line, with the reservation table, rate-limited (INV-40 corollary).

On the ancestor controller the published frames carried `p_free_w 8–9 kW` while the
house was 1.4 kW over its allowance, and the reservation table that would have shown why -
`granted_w 348.3, measured_w 2940.0` - was not in the log. It is now: on every tick
with a breach or a deficit, one WARNING carries load, granted, measured, nameplate and
reserved for every load, the stage and its reason, `P_allow`, `P_total` and which
constraint bound. One line per condition per 60 s, so an hour-long breach is visible
without drowning the log.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    BreachLog,
    allocate,
)
from tests.core.allocation.conftest import (
    NOW,
    alloc_ctx,
    budget_of,
    comfort,
    controlled,
    demand,
    loop_view,
    meter,
    tank_view,
)


def _breaching_tick() -> tuple[object, float]:
    """Return a report with a comfort breach and the allowance it broke."""
    cold = loop_view(
        demand=demand(
            max_w=960.0, comfort=comfort(current=19.0, target=24.0, floor=21.0, violated=True)
        )
    )
    other = loop_view(
        load_id="loop_hall",
        demand=demand(
            max_w=960.0, comfort=comfort(current=19.0, target=24.0, floor=21.0, violated=True)
        ),
    )
    tank = tank_view()
    ctx = alloc_ctx(
        [cold, other, tank],
        budget=budget_of(1000.0),
        views={
            "loop_bath": controlled("loop_bath", measured_w=0.0),
            "loop_hall": controlled("loop_hall", measured_w=0.0),
            "tank": controlled("tank", measured_w=2940.0),
        },
    )
    _grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())
    return report, 1000.0


def test_the_breach_line_carries_the_reservation_table(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every load, with what it was granted, measured and reserved (§5.9)."""
    report, p_allow = _breaching_tick()
    log = BreachLog()

    with caplog.at_level(logging.WARNING):
        spoke = log.log(
            report,
            stage=2,
            reason="projection: 98 % of the ceiling",
            p_allow_w=p_allow,
            p_total_w=4900.0,
            now=NOW,
        )

    assert spoke is True
    line = caplog.text
    assert "breach" in line
    assert "loop_bath granted 960" in line
    assert "tank granted 0 measured 2940 nameplate 3000 reserved 3000" in line
    assert "P_allow 1000 W" in line
    assert "P_total 4900 W" in line


def test_the_line_is_rate_limited_to_one_per_minute_per_condition(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A breach that lasts an hour says so once a minute, not once a tick."""
    report, p_allow = _breaching_tick()
    log = BreachLog()
    kwargs = {
        "stage": 2,
        "reason": "projection: 98 % of the ceiling",
        "p_allow_w": p_allow,
        "p_total_w": 4900.0,
    }

    with caplog.at_level(logging.WARNING):
        first = log.log(report, now=NOW, **kwargs)  # type: ignore[arg-type]
        second = log.log(report, now=NOW + timedelta(seconds=10), **kwargs)  # type: ignore[arg-type]
        later = log.log(report, now=NOW + timedelta(seconds=61), **kwargs)  # type: ignore[arg-type]

    assert (first, second, later) == (True, False, True)
    assert caplog.text.count("reservations") == 2


def test_a_tick_with_neither_a_breach_nor_a_deficit_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log is for the two conditions §5.9 names and for nothing else."""
    tank = tank_view()
    ctx = alloc_ctx([tank], budget=budget_of(10_000.0), meter_snapshot=meter(grid_w=3000.0))
    _grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    with caplog.at_level(logging.WARNING):
        spoke = BreachLog().log(
            report,
            stage=0,
            reason="projection: 31 % of the ceiling",
            p_allow_w=10_000.0,
            p_total_w=3000.0,
            now=NOW,
        )

    assert report.over_allowance is False
    assert spoke is False
    assert caplog.text == ""


def test_a_deficit_tick_logs_under_its_own_condition(caplog: pytest.LogCaptureFixture) -> None:
    """A deficit and a breach are rate-limited apart: one is not the other's excuse."""
    tank = tank_view()
    ctx = alloc_ctx(
        [tank],
        budget=budget_of(2000.0),
        meter_snapshot=meter(grid_w=3400.0),
        views={"tank": controlled("tank", measured_w=2940.0)},
    )
    _grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    with caplog.at_level(logging.WARNING):
        spoke = BreachLog().log(
            report,
            stage=3,
            reason="projection: 104 % of the ceiling",
            p_allow_w=2000.0,
            p_total_w=3400.0,
            now=NOW,
        )

    assert report.deficit_w == pytest.approx(1400.0)
    assert spoke is True
    assert "deficit" in caplog.text


def test_an_unknown_total_is_reported_as_unknown(caplog: pytest.LogCaptureFixture) -> None:
    """A missing meter reading is said, not printed as a zero."""
    report, p_allow = _breaching_tick()

    with caplog.at_level(logging.WARNING):
        BreachLog().log(
            report,
            stage=2,
            reason="projection: 98 % of the ceiling",
            p_allow_w=p_allow,
            p_total_w=None,
            now=NOW,
        )

    assert "P_total unknown" in caplog.text
