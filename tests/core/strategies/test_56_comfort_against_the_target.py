"""D5 §9 56 - the default coast is half a kelvin below the target (D-0819).

The level planner uses all of the band it is given, and a floor loop in the smoke tier was
a kelvin under its target through each evening peak - with the thermostat's swing, 1.3 K below
what the household asked for - and a heat pump 0.6 K under it on average, where the planner it
replaced left rooms a tenth or two under. Comfort is judged against the target, so the band the
planner coasts into is half of EN 16798-1 Category I's: `coast_k` 0.5 K, which a household can
raise.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.strategies.heat_capacitor import COAST_K
from tests.core.strategies.test_08_heat_capacitor import COMFORT_C, levels, planned


@pytest.mark.inv("INV-55")
def test_56_the_default_coast_stays_within_half_a_kelvin_of_the_target() -> None:
    """On a volatile day with a deep band, no slot of the default plan is under target − 0.5 K."""
    assert pytest.approx(0.5) == COAST_K
    plan = planned(worth_share=None)
    assert min(levels(plan)) >= COMFORT_C - COAST_K - 1e-9
    assert min(levels(plan)) < COMFORT_C - 0.1, "it does use the room it has"


def test_56_a_household_that_raises_coast_k_gets_the_deeper_coast() -> None:
    """The knob is the household's: 1 K coasts further than the default and no further than 1 K."""
    deep = levels(planned(coast_k=1.0, worth_share=None))
    default = levels(planned(worth_share=None))
    assert min(deep) < min(default) - 0.1
    assert min(deep) >= COMFORT_C - 1.0 - 1e-9
