"""A season without data (D10 §9 28, D-0754, INV-63).

A floor's `coast_tau_h` passed at 61 h in March. In June the floor barely runs,
three coasts miss the gate, and the plan still uses 61 h, kept since March, not
the setup value. A pass at 58 h in October replaces it. A kept value 401 days
old gives way to the setup value, and a value out of today's bounds is never
used - not even one restored from the store, which was gated on the day it was
made (D-0762).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts.fit import Fit, FitKey, FitQuality, in_force, keep
from custom_components.powerplan.core.forecasts.fit.thermal import FOIL_FLOOR_TAU_BOUNDS_H

KEY = "loop_bath.coast_tau_h"
MARCH = datetime(2026, 3, 15, 3, 17, tzinfo=UTC)
JUNE = datetime(2026, 6, 15, 3, 17, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 15, 3, 17, tzinfo=UTC)
SETUP_H = 40.0


def _fit(
    value: float, at: datetime, *, ok: bool, bounds: tuple[float, float] = (5.0, 1000.0)
) -> Fit:
    quality = FitQuality(
        r2=None, n=40 if ok else 3, span_days=10.0, ok=ok, reason="ok" if ok else "only 3 episodes"
    )
    return Fit(
        key=FitKey.COAST_TAU,
        load_id="loop_bath",
        value=value,
        unit="h",
        bounds=bounds,
        quality=quality,
        configured=SETUP_H,
        effective=value if ok else SETUP_H,
        fitted_at=at,
    )


@pytest.mark.inv("INV-63")
def test_28_a_june_with_three_coasts_plans_on_march() -> None:
    """The miss keeps March's 61 h, and says since when."""
    march = keep({}, {KEY: _fit(61.0, MARCH, ok=True)}, MARCH)
    june = keep(march, {KEY: _fit(140.0, JUNE, ok=False)}, JUNE)[KEY]
    assert june.effective == 61.0
    assert (june.kept, june.kept_at) == (61.0, MARCH)
    assert june.applied
    assert not june.quality.ok, "today's fit is still published as missed"


def test_28_a_june_with_no_coast_at_all_keeps_march_too() -> None:
    """A fit that could not be computed today keeps the last that passed."""
    march = keep({}, {KEY: _fit(61.0, MARCH, ok=True)}, MARCH)
    june = keep(march, {}, JUNE)[KEY]
    assert june.effective == 61.0


def test_28_a_pass_in_october_replaces_it() -> None:
    """58 h passes: kept and used."""
    march = keep({}, {KEY: _fit(61.0, MARCH, ok=True)}, MARCH)
    october = keep(march, {KEY: _fit(58.0, OCTOBER, ok=True)}, OCTOBER)[KEY]
    assert (october.effective, october.kept, october.kept_at) == (58.0, 58.0, OCTOBER)


@pytest.mark.inv("INV-63")
def test_28_a_kept_value_401_days_old_gives_way_to_the_setup_value() -> None:
    """After `FIT_KEEP_DAYS` the configured value returns."""
    march = keep({}, {KEY: _fit(61.0, MARCH, ok=True)}, MARCH)
    later = MARCH + timedelta(days=401)
    fit = keep(march, {KEY: _fit(140.0, later, ok=False)}, later)[KEY]
    assert fit.effective == SETUP_H
    assert fit.kept is None
    assert not fit.applied


@pytest.mark.inv("INV-63")
def test_28_a_value_out_of_bounds_is_never_kept() -> None:
    """Out of today's bounds (a foil's 6 h on its floor probe), the kept 61 h is not used; a miss never becomes kept."""
    march = keep({}, {KEY: _fit(61.0, MARCH, ok=True)}, MARCH)
    june = keep(march, {KEY: _fit(30.0, JUNE, ok=False, bounds=(0.3, 6.0))}, JUNE)[KEY]
    assert june.effective == SETUP_H
    missed_first = keep({}, {KEY: _fit(140.0, MARCH, ok=False)}, MARCH)[KEY]
    assert missed_first.kept is None


def test_28_a_fit_stored_before_kept_existed_counts_as_kept() -> None:
    """A passing fit from the store, without `kept`, is the last pass."""
    stored = {KEY: replace(_fit(61.0, MARCH, ok=True), kept=None, kept_at=None)}
    june = keep(stored, {KEY: _fit(140.0, JUNE, ok=False)}, JUNE)[KEY]
    assert (june.effective, june.kept_at) == (61.0, MARCH)


@pytest.mark.inv("INV-63")
def test_28_a_stored_fit_out_of_todays_bounds_is_not_in_force() -> None:
    """TV-stua, 14 Oct 2026: 382.7 h passed at 03:17 under (5, 500) and was restored after D-0740."""
    stored = _fit(382.7, OCTOBER, ok=True)
    assert stored.applied, "the fit passed on the day it was made"
    assert in_force(stored, {FitKey.COAST_TAU: FOIL_FLOOR_TAU_BOUNDS_H}) is None
    assert in_force(stored, {}) == 382.7, "a slab's bounds are still (5, 500)"


def test_28_a_fit_that_missed_its_gate_is_not_in_force() -> None:
    """A miss with nothing kept leaves the load on its own value."""
    assert in_force(_fit(140.0, JUNE, ok=False), {}) is None
    kept = keep({}, {KEY: _fit(61.0, MARCH, ok=True)}, MARCH)
    june = keep(kept, {KEY: _fit(140.0, JUNE, ok=False)}, JUNE)[KEY]
    assert in_force(june, {}) == 61.0
