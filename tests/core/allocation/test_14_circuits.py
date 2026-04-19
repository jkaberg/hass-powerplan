"""D6 §9 14 - a circuit binds its members before the site is considered (INV-60).

"The garage circuit is fused at 32 A: the charger and the sauna will never exceed
it together" (D6 §6). Constraints are **hierarchical**: the site cap is applied
first and the circuit tightens it, `min()` by `min()`, so an inner limit can only
ever take away. A circuit breach is a `fuse_breach` for **its members only** - the
site keeps allocating, because a 32 A garage fuse says nothing about the other 60
amps of the house.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    CircuitLimit,
    ShedReason,
    SiteFuse,
    allocate,
)
from tests.core.allocation.conftest import (
    FUSE_W,
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    demand,
    ev_view,
    tank_view,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.strategies import LoadView

#: The garage: 32 A on one phase.
GARAGE_W = 32.0 * W_PER_AMP


def _garage(*, sub_meter_w: float | None = None, unmetered_w: float = 0.0) -> CircuitLimit:
    """Return the garage circuit: 32 A shared by the charger and the sauna."""
    return CircuitLimit(
        key="circuit_garage",
        limit_w=GARAGE_W,
        members=frozenset({"ev", "sauna"}),
        sub_meter_w=sub_meter_w,
        unmetered_w=unmetered_w,
    )


def _sauna() -> LoadView:
    """Return a 20 A sauna heater on the garage circuit, priority 25."""
    return tank_view(
        load_id="sauna",
        priority=25,
        nameplate_w=20.0 * W_PER_AMP,
        demand=demand(max_w=20.0 * W_PER_AMP, reason="sauna heating"),
    )


@pytest.mark.inv("INV-60")
def test_14_the_sauna_and_the_charger_never_exceed_the_garage_fuse() -> None:
    """20 A of sauna on a 32 A circuit leaves the charger 12 A, not 32."""
    ev = ev_view()
    sauna = _sauna()
    ctx = alloc_ctx([sauna, ev], budget=budget_of(20_000.0))

    grants, _report, _state = allocate(ctx, (SiteFuse(FUSE_W), _garage()), AllocCfg(), AllocState())

    assert grants["sauna"].w == pytest.approx(20.0 * W_PER_AMP)
    assert grants["ev"].w == pytest.approx(12.0 * W_PER_AMP)
    assert grants["ev"].capped_by == ("circuit_garage",)
    assert grants["sauna"].w + grants["ev"].w <= GARAGE_W


@pytest.mark.inv("INV-60")
def test_14_a_sub_meter_is_used_where_there_is_one_and_the_load_gives_its_own_back() -> None:
    """The sub-meter reads the circuit including this load: it may not be charged twice."""
    ev = ev_view()
    ctx = alloc_ctx(
        [ev],
        budget=budget_of(20_000.0),
        views={"ev": controlled("ev", measured_w=10.0 * W_PER_AMP)},
        previous={},
    )
    circuit = _garage(sub_meter_w=25.0 * W_PER_AMP)

    grants, _report, _state = allocate(ctx, (circuit,), AllocCfg(), AllocState())

    # 32 A of fuse − (25 A measured − 10 A of our own) = 17 A.
    assert grants["ev"].w == pytest.approx(17.0 * W_PER_AMP)


@pytest.mark.inv("INV-60")
def test_14_unmetered_load_behind_the_circuit_is_subtracted() -> None:
    """A freezer nobody meters still sits behind the fuse (§5.8)."""
    ev = ev_view()
    ctx = alloc_ctx([ev], budget=budget_of(20_000.0))

    grants, _report, _state = allocate(
        ctx, (_garage(unmetered_w=2.0 * W_PER_AMP),), AllocCfg(), AllocState()
    )

    assert grants["ev"].w == pytest.approx(30.0 * W_PER_AMP)


@pytest.mark.inv("INV-60")
def test_14_a_nested_circuit_can_only_tighten_its_parent() -> None:
    """Two fuses in series: the tighter one wins, whichever order they are listed."""
    ev = ev_view()
    ctx = alloc_ctx([ev], budget=budget_of(20_000.0))
    inner = CircuitLimit(key="circuit_charger", limit_w=16.0 * W_PER_AMP, members=frozenset({"ev"}))

    grants, _report, _state = allocate(ctx, (_garage(), inner), AllocCfg(), AllocState())

    assert grants["ev"].w == pytest.approx(16.0 * W_PER_AMP)
    assert grants["ev"].capped_by == ("circuit_charger",)


@pytest.mark.inv("INV-60")
def test_14_a_circuit_breach_is_a_stage_four_for_its_members_only() -> None:
    """8 kW measured on a 7.36 kW circuit: the members go off, the tank does not."""
    ev = ev_view()
    sauna = _sauna()
    tank = tank_view()
    ctx = alloc_ctx(
        [sauna, ev, tank],
        budget=budget_of(20_000.0),
        stage=0,
    )
    breached = _garage(sub_meter_w=8000.0)

    grants, report, _state = allocate(ctx, (breached,), AllocCfg(), AllocState())

    assert grants["ev"].w == 0.0
    assert grants["sauna"].w == 0.0
    assert grants["ev"].stage == 4
    assert grants["ev"].blunt is True
    assert report.shed_reason["ev"] == ShedReason.CIRCUIT
    assert report.shed_reason["sauna"] == ShedReason.CIRCUIT

    # The site is unaffected: the tank keeps its grant and the site stage stands.
    assert grants["tank"].w == pytest.approx(3000.0)
    assert grants["tank"].stage == 0
    assert report.circuits["circuit_garage"].breach is True
    assert report.circuits["circuit_garage"].members == ("ev", "sauna")
