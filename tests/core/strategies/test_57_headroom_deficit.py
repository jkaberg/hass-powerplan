"""D5 §5.1 `Headroom.deficit_w` (D-0837): the shortfall below the ceiling, beside the clamped room."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.powerplan.core.model import Confidence, Slot
from custom_components.powerplan.core.strategies import Headroom
from custom_components.powerplan.core.tariffs import AUTO, Target

T0 = datetime(2027, 1, 12, 16, 0, tzinfo=UTC)


class _Ceiling:
    def target_w_at(self, t: datetime, target: Target) -> float:
        del t, target
        return 5000.0

    def priced_limit_now(self, t: datetime) -> None:
        del t


class _Baseline:
    def baseline_w(self, t: datetime) -> float:
        return 7000.0 if t == T0 else 1000.0

    def outdoor_c(self, t: datetime) -> None:
        del t

    def surplus_w(self, t: datetime) -> float:
        del t
        return 0.0

    def hold_w(self, load_id: str, t: datetime) -> None:
        del load_id, t


def _slots() -> list[Slot]:
    return [
        Slot(
            start=T0 + timedelta(minutes=15 * k),
            end=T0 + timedelta(minutes=15 * (k + 1)),
            total=0,
            components={},
            confidence=Confidence.KNOWN,
        )
        for k in range(2)
    ]


def _built() -> Headroom:
    return Headroom.build(
        _slots(), tariff=_Ceiling(), target=AUTO, forecasts=_Baseline(), eps_w=500.0, fraction=0.9
    )


def test_57_a_slot_over_the_ceiling_reads_zero_room_and_a_deficit() -> None:
    """Room floors at 0 for every strategy; the battery's figure is the unclamped remainder."""
    room = _built()
    over, under = _slots()[0].start, _slots()[1].start
    assert room.w_at(over) == 0.0
    assert room.deficit_w(over) == pytest.approx(7000.0 - 4500.0 * 0.9)
    assert room.w_at(under) == pytest.approx(4500.0 * 0.9 - 1000.0)
    assert room.deficit_w(under) == 0.0


def test_57_what_the_loads_above_take_adds_to_the_deficit() -> None:
    """A load above the battery that reserves 1 kW in a clear slot can push it over, too."""
    room = _built()
    over, under = _slots()[0].start, _slots()[1].start
    after = room.reserve({over: 1000.0, under: 3500.0})
    assert after.deficit_w(over) == pytest.approx(room.deficit_w(over) + 1000.0)
    assert after.w_at(over) == 0.0
    assert after.deficit_w(under) == pytest.approx(3500.0 - (4500.0 * 0.9 - 1000.0))
    assert after.w_at(under) == 0.0
    assert room.deficit_w(under) == 0.0, "reserve returns a new headroom"


def test_57_no_ceiling_is_no_deficit() -> None:
    """Outside a tariff's windows the room is infinite and nothing is short."""
    room = Headroom.build(_slots(), tariff=None, forecasts=_Baseline())
    assert room.deficit_w(T0) == 0.0
    assert room.reserve({T0: 9000.0}).deficit_w(T0) == 0.0
