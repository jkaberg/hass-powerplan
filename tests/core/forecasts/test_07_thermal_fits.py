"""D10 §9 7 - the thermal fits, their bounds and the INV-63 fallback (D10 §5.6).

The episodes come from the simulators, so the fit meets a house that does not
already behave like the one-node store it is fitting (D9 §2): `RoomSim` is one
node with a known `u × area`, and `SlabSim` is a screed node coupled to a room
node, which is the case the product's `SlabStore` simplifies.

Three claims, and the last two are INV-63:

* a one-node room's coasting recovers its loss coefficient within 10 %, and a
  two-node slab's fit predicts a held-out episode within 10 %;
* too few episodes → `ok = False` and `effective = configured`;
* a value outside the bounds is **never applied**, whatever its R² - and the
  well-insulated slab below shows that is not hypothetical: only a quarter of
  the energy leaving that house comes out of the screed, so the coefficient the
  screed's own fall implies lands under D10 §5.6's per-m² floor and is dropped.
  The load then keeps `configured` - for a slab that is `None`, and a loss term
  nobody can compute is skipped, never guessed (D4 §5.7).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.forecasts import FitKey, LoadHistory, coast_rate, heatup_rate
from tests.core.forecasts.conftest import local
from tests.sim.base import TEMP_AIR, TEMP_FLOOR, Env
from tests.sim.room import RoomSim
from tests.sim.slab import SlabSim

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

START = local(2026, 1, 5, 22, 0)
STEP_S = 300.0

ROOM_AREA_M2 = 12.0
ROOM_U = 0.7
COAST_HOURS = 6.0
#: Five coasts two days apart, so the 7-day span gate is met (D10 §5.6).
OUTDOOR_C = (-10.0, -5.0, 0.0, 5.0, 8.0)
HELD_OUT_C = -2.0

SLAB_AREA_M2 = 20.0
#: A 1950s envelope from D4 §6.4's building-age table and a heavy 100 mm screed:
#: the combination whose screed-only coast lands inside D10 §5.6's bounds.
LEAKY_U = 2.0
HEAVY_SCREED_MM = 100.0

type Rows = tuple[tuple[datetime, float], ...]
type Build = Callable[[], RoomSim | SlabSim]


def _room() -> RoomSim:
    """Return a bedroom at 22 °C with its plug off - a pure coast."""
    return RoomSim(area_m2=ROOM_AREA_M2, u_envelope_w_per_m2k=ROOM_U, room_c=22.0, plug_on=False)


def _slab(*, u: float, screed_mm: float, mode: str = "off") -> SlabSim:
    """Return a floor loop with its cable off, or heating towards its maximum."""
    return SlabSim(
        area_m2=SLAB_AREA_M2,
        screed_mm=screed_mm,
        u_envelope_w_per_m2k=u,
        screed_c=22.0,
        room_c=21.0,
        setpoint_c=27.0,
        mode=mode,
        floor_min_c=5.0,
    )


def _episode(
    sim: RoomSim | SlabSim,
    *,
    start: datetime,
    outdoor_c: float,
    hours: float,
    level_key: str,
    indoor_key: str | None,
) -> tuple[Rows, Rows, Rows]:
    """Run one episode and return its `(level, indoor, outdoor)` rows."""
    level: list[tuple[datetime, float]] = []
    indoor: list[tuple[datetime, float]] = []
    outdoor: list[tuple[datetime, float]] = []
    at = start
    for _ in range(int(hours * 3600.0 / STEP_S)):
        at += timedelta(seconds=STEP_S)
        reads = sim.step(STEP_S, None, Env(now=at, outdoor_c=outdoor_c))
        level.append((at, reads.values[level_key]))
        if indoor_key is not None:
            indoor.append((at, reads.values[indoor_key]))
        outdoor.append((at, outdoor_c))
    return tuple(level), tuple(indoor), tuple(outdoor)


def _history(
    outdoors: Sequence[float],
    *,
    build: Build,
    capacity_kwh_per_k: float,
    area_m2: float,
    level_key: str,
    indoor_key: str | None = None,
    configured: float | None = None,
    hours: float = COAST_HOURS,
    on: bool = False,
) -> LoadHistory:
    """One episode per entry in `outdoors`, two days apart, as one history."""
    level: list[tuple[datetime, float]] = []
    indoor: list[tuple[datetime, float]] = []
    outdoor: list[tuple[datetime, float]] = []
    on_rows: list[tuple[datetime, bool]] = []
    for index, outdoor_c in enumerate(outdoors):
        start = START + timedelta(days=2 * index)
        rows = _episode(
            build(),
            start=start,
            outdoor_c=outdoor_c,
            hours=hours,
            level_key=level_key,
            indoor_key=indoor_key,
        )
        on_rows.append((start, on))
        on_rows.append((rows[0][-1][0] + timedelta(seconds=STEP_S), not on))
        level += rows[0]
        indoor += rows[1]
        outdoor += rows[2]
    return LoadHistory(
        load_id="loop",
        type_key="floor_heating",
        fits=(FitKey.LOSS_COEFF, FitKey.HEATUP_RATE),
        nameplate_w=SLAB_AREA_M2 * 80.0,
        capacity_kwh_per_k=capacity_kwh_per_k,
        area_m2=area_m2,
        configured={FitKey.LOSS_COEFF: configured},
        on_rows=tuple(on_rows),
        level_rows=tuple(level),
        indoor_rows=tuple(indoor),
        outdoor_rows=tuple(outdoor),
    )


def _room_history(outdoors: Sequence[float], configured: float | None = None) -> LoadHistory:
    """Return the bedroom's coasting history."""
    return _history(
        outdoors,
        build=_room,
        capacity_kwh_per_k=RoomSim(area_m2=ROOM_AREA_M2).kwh_per_k,
        area_m2=ROOM_AREA_M2,
        level_key=TEMP_AIR,
        configured=configured,
    )


def _slab_history(*, u: float, screed_mm: float) -> LoadHistory:
    """Return the floor loop's coasting history at `u` and `screed_mm`."""
    return _history(
        OUTDOOR_C,
        build=lambda: _slab(u=u, screed_mm=screed_mm),
        capacity_kwh_per_k=_slab(u=u, screed_mm=screed_mm).kwh_per_k,
        area_m2=SLAB_AREA_M2,
        level_key=TEMP_FLOOR,
        indoor_key=TEMP_AIR,
    )


def test_07_coast_rate_recovers_a_known_loss_coefficient() -> None:
    """A one-node room at 0.7 W/m²K over 12 m² comes back as 8.4 W/K, within 10 %."""
    fit = coast_rate(_room_history(OUTDOOR_C), START + timedelta(days=10))

    assert fit is not None
    assert fit.quality.ok, fit.quality.reason
    assert fit.value == pytest.approx(ROOM_U * ROOM_AREA_M2, rel=0.10)
    assert fit.effective == fit.value
    assert fit.unit == "W/K"
    assert fit.quality.n == len(OUTDOOR_C)
    assert fit.quality.span_days >= 7.0
    assert fit.quality.r2 is not None
    assert fit.quality.r2 >= 0.5


def test_07b_the_fitted_slab_predicts_a_held_out_episode() -> None:
    """Fit five coasts of the two-node slab, then predict the sixth within 10 %."""
    capacity = _slab(u=LEAKY_U, screed_mm=HEAVY_SCREED_MM).kwh_per_k

    fit = coast_rate(_slab_history(u=LEAKY_U, screed_mm=HEAVY_SCREED_MM), START)
    assert fit is not None
    assert fit.quality.ok, fit.quality.reason
    assert fit.effective == fit.value

    level, indoor, _outdoor = _episode(
        _slab(u=LEAKY_U, screed_mm=HEAVY_SCREED_MM),
        start=START + timedelta(days=12),
        outdoor_c=HELD_OUT_C,
        hours=COAST_HOURS,
        level_key=TEMP_FLOOR,
        indoor_key=TEMP_AIR,
    )
    actual_kwh = capacity * (level[0][1] - level[-1][1])
    drive = sum(row[1] for row in indoor) / len(indoor) - HELD_OUT_C
    predicted_kwh = fit.value * drive * COAST_HOURS / 1000.0

    assert predicted_kwh == pytest.approx(actual_kwh, rel=0.10)


@pytest.mark.inv("INV-63")
def test_07c_too_few_episodes_keeps_the_configured_value() -> None:
    """Two coasts fail the n ≥ 5 gate: published, never applied (INV-63)."""
    fit = coast_rate(_room_history(OUTDOOR_C[:2], configured=6.0), START + timedelta(days=10))

    assert fit is not None
    assert fit.quality.ok is False
    assert fit.quality.n == 2
    assert "episodes" in fit.quality.reason
    assert fit.effective == 6.0
    assert fit.value != fit.effective


@pytest.mark.inv("INV-63")
def test_07d_an_out_of_bounds_coefficient_is_never_applied() -> None:
    """A well-insulated two-node slab implies 0.19 W/K·m² - under the floor (INV-63)."""
    fit = coast_rate(_slab_history(u=0.7, screed_mm=50.0), START + timedelta(days=10))

    assert fit is not None
    assert fit.value < fit.bounds[0]
    assert fit.quality.ok is False
    assert "bounds" in fit.quality.reason
    assert fit.configured is None
    assert fit.effective is None


@pytest.mark.inv("INV-63")
def test_07e_a_fit_above_its_bounds_is_not_applied_either() -> None:
    """The same rule on the other side: a nonsense coefficient stays information."""
    tiny = replace(_room_history(OUTDOOR_C, configured=8.0), area_m2=0.01)

    fit = coast_rate(tiny, START + timedelta(days=10))

    assert fit is not None
    assert fit.value > fit.bounds[1]
    assert fit.quality.ok is False
    assert fit.effective == 8.0


def test_07f_heatup_rate_is_kelvin_per_hour_from_the_cable_running() -> None:
    """Five heating episodes give a median K/h inside D10 §5.6's bounds."""
    history = _history(
        (0.0,) * 5,
        build=lambda: _slab(u=LEAKY_U, screed_mm=HEAVY_SCREED_MM, mode="heat"),
        capacity_kwh_per_k=_slab(u=LEAKY_U, screed_mm=HEAVY_SCREED_MM).kwh_per_k,
        area_m2=SLAB_AREA_M2,
        level_key=TEMP_FLOOR,
        indoor_key=TEMP_AIR,
        hours=2.0,
        on=True,
    )

    fit = heatup_rate(history, START + timedelta(days=10))

    assert fit is not None
    assert fit.quality.ok, fit.quality.reason
    assert fit.unit == "K/h"
    assert fit.bounds == (0.1, 10.0)
    assert 0.3 <= fit.value <= 4.0
    assert fit.effective == fit.value


def test_07g_a_load_with_only_a_power_history_still_has_episodes() -> None:
    """No state history: the on/off runs come from the power trace (D10 §5.6)."""
    history = _room_history(OUTDOOR_C)
    step = timedelta(seconds=STEP_S)
    off = {at for at, _value in history.level_rows}
    power = tuple(
        (at, 0.0 if at in off else history.nameplate_w)
        for at in sorted({*off, *(at + step for at, _value in history.level_rows)})
    )
    derived = replace(history, on_rows=(), power_rows=power)

    fit = coast_rate(derived, START + timedelta(days=10))

    assert fit is not None
    assert fit.quality.n == len(OUTDOOR_C)
    assert fit.value == pytest.approx(ROOM_U * ROOM_AREA_M2, rel=0.10)


def test_07h_a_load_with_no_store_capacity_has_nothing_to_fit() -> None:
    """Without a capacity a falling temperature is not watts (D10 §5.6)."""
    assert coast_rate(replace(_room_history(OUTDOOR_C), capacity_kwh_per_k=None), START) is None


def test_07i_a_warmer_outside_than_in_is_not_a_coast() -> None:
    """An episode with no difference to fall by says nothing about a loss."""
    assert coast_rate(_room_history((30.0, 30.0, 30.0, 30.0, 30.0)), START) is None
    assert heatup_rate(_room_history(OUTDOOR_C), START) is None
