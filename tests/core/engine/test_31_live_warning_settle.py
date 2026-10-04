"""D7 §9 31 - the live warning waits for our own settle (D7 §5.4, D-0713).

At 1 Oct 23:00:24 UTC the reference house named 8.44 kWh "uncontrolled" while
the EV drew 5.4 kW and the tank 2.9 kW: both had just been cut, and a settling
load counts at its commanded power (INV-18), so the uncontrolled term held our
own draw (`design/reviews/field-audit-2026-10.md` §5).
"""

from __future__ import annotations

from custom_components.powerplan.core.allocation import BudgetCfg, PiState, budget
from custom_components.powerplan.core.engine import RuntimeState, _settling_cut
from custom_components.powerplan.core.metering import ControlledView
from tests.core.allocation.conftest import FUSE_W, NOW, ceiling, meter
from tests.core.engine.conftest import engine_for, inputs_at, site

EV_CUT = ControlledView(
    load_id="ev", measured_w=5400.0, commanded_w=1610.0, settling=True, phases=None
)
TANK_CUT = ControlledView(
    load_id="tank", measured_w=2900.0, commanded_w=0.0, settling=True, phases=None
)
EV_RAISED = ControlledView(
    load_id="ev", measured_w=1380.0, commanded_w=5400.0, settling=True, phases=None
)
EV_STEADY = ControlledView(
    load_id="ev", measured_w=5400.0, commanded_w=None, settling=False, phases=None
)


def test_31_a_cut_still_landing_is_ours() -> None:
    """Commanded under measured while settling; a raise on its way or a steady load isn't."""
    assert _settling_cut((EV_CUT, TANK_CUT))
    assert not _settling_cut((EV_RAISED,))
    assert not _settling_cut((EV_STEADY,))
    assert not _settling_cut(())


def _warn(settling_cut: bool, runtime: RuntimeState) -> tuple[tuple, RuntimeState]:
    cfg = site()
    engine = engine_for((), cfg=cfg)
    house = meter(grid_w=10_200.0, uncontrolled_w=8_600.0, used_kwh=0.1, t_rem_h=59.5 / 60.0)
    window = ceiling(9.70)
    chain = budget(window, house, FUSE_W, PiState(), BudgetCfg(), None)
    assert chain.projected_kwh > window.kwh
    warnings, after, _events, _notes = engine._warnings(
        runtime,
        inputs_at(cfg, NOW, grid_w=10_200.0),
        chain,
        window,
        house,
        (),
        {},
        settling_cut=settling_cut,
    )
    return warnings, after


def test_31b_no_peak_uncontrolled_while_our_cut_settles() -> None:
    """10.2 kW measured, 8.6 kW "uncontrolled": nothing while settling, the warning after."""
    warnings, after = _warn(True, RuntimeState())
    assert not [w for w in warnings if w.kind == "peak_uncontrolled"]
    assert after.peak.live is None

    warnings, after = _warn(False, RuntimeState())
    (live,) = [w for w in warnings if w.kind == "peak_uncontrolled"]
    assert after.peak.live == live.key

    # A live warning already up neither clears nor repeats while a cut settles.
    warnings, held = _warn(True, after)
    assert held.peak.live == live.key
