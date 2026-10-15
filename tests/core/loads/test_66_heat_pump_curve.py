"""D4 §9 66 - the heat pump's curve follows the heating type (D-0773, D-0825).

An air-to-water pump's COP is 0.35 × Carnot at the supply its heating needs: 28 + 0.25·(20 − T_out)
under floor heating, 35 + 0.5·(20 − T_out), at most 55, on radiators. The curve is materialised, shown
and editable; pre-heating is off and has no outdoor cap for a new pump.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.powerplan.core.loads import device_types
from custom_components.powerplan.core.loads.questionnaire import QCtx
from custom_components.powerplan.core.loads.stores.cop import (
    DEFAULT_COP_CURVES,
    a2w_curve,
    defrost_factor,
)
from custom_components.powerplan.core.loads.types.heat_pump import curve_of

CTX = QCtx(capabilities=frozenset({"setpoint"}), readable={"min_temp": 16.0, "max_temp": 30.0})
PUMP = device_types.get("heat_pump")


def _derive(**answers: object):  # type: ignore[no-untyped-def]
    return PUMP.derive(PUMP.questionnaire.validate(dict(answers), CTX), CTX)


@pytest.mark.parametrize(
    ("heating", "expected"),
    [
        ("floor", {-7.0: 2.58, 2.0: 3.51, 7.0: 4.39}),
        ("radiators", {-7.0: 2.03, 2.0: 2.64, 7.0: 3.19}),
    ],
)
def test_66_the_air_to_water_curve_is_a_third_of_carnot_at_the_supply_the_heating_needs(
    heating: str, expected: dict[float, float]
) -> None:
    """COP 2.58 / 3.51 / 4.39 at -7 / +2 / +7 °C under a floor; 2.03 / 2.64 / 3.19 on radiators."""
    curve = a2w_curve(heating)
    for outdoor, cop in expected.items():
        assert curve.at(outdoor) == pytest.approx(cop, abs=0.005)


def test_66_a_floor_is_warmer_to_the_pump_than_radiators_and_neither_falls_with_the_weather() -> (
    None
):
    """The floor's lower supply is the better COP at every anchor; both curves rise with the outdoor temperature."""
    floor, radiators = a2w_curve("floor"), a2w_curve("radiators")
    assert [t for t, _ in floor.points] == [t for t, _ in radiators.points]
    for (_, high), (_, low) in zip(floor.points, radiators.points, strict=True):
        assert high > low
    for curve in (floor, radiators):
        cops = [cop for _, cop in curve.points]
        assert cops == sorted(cops)


def test_66_the_radiators_supply_is_capped_at_55_degrees() -> None:
    """At -20 °C the formula says 55 °C exactly; below it the cap holds, so the curve is Carnot at 55."""
    assert a2w_curve("radiators").at(-20.0) == pytest.approx(0.35 * 328.15 / 75.0, abs=1e-6)


def test_66_a_new_air_to_water_pump_is_derived_from_its_heating_type() -> None:
    """The materialised curve is the heating type's; radiators are the answer when nothing is said."""
    unsaid = _derive(hp_type="a2w")
    floor = _derive(hp_type="a2w", heating="floor")
    assert unsaid.params["heating"] == "radiators"
    assert unsaid.params["cop_curve"] == {f"{t}": c for t, c in a2w_curve("radiators").points}
    assert floor.params["cop_curve"] == {f"{t}": c for t, c in a2w_curve("floor").points}
    assert floor.strategy_params["cop_curve"] == floor.params["cop_curve"]


def test_66_the_heating_type_moves_no_other_pump() -> None:
    """Air-to-air and ground-source keep their own curves whatever the heating says."""
    for hp_type in ("a2a", "gshp"):
        derived = _derive(hp_type=hp_type, heating="floor")
        assert derived.params["cop_curve"] == {
            f"{t}": c for t, c in DEFAULT_COP_CURVES[hp_type].points
        }


def test_66_the_curve_is_shown_and_editable() -> None:
    """A curve the household typed replaces the derived one, and `curve_of` reads back what was stored."""
    typed = {-10.0: 2.0, 0.0: 3.0, 10.0: 4.0}
    derived = _derive(hp_type="a2w", heating="floor", cop_curve=typed)
    assert derived.params["cop_curve"] == {"-10.0": 2.0, "0.0": 3.0, "10.0": 4.0}
    assert PUMP.questionnaire.get("cop_curve").advanced
    assert PUMP.questionnaire.get("cop_curve").derived_default


def test_66_a_new_pump_does_not_preheat_and_has_no_outdoor_cap() -> None:
    """`preheat` is off and `preheat_max_outdoor_c` empty: each hour decides once the household turns it on."""
    derived = _derive(hp_type="a2w")
    assert derived.params["preheat"] is False
    assert derived.params["preheat_max_outdoor_c"] is None
    assert derived.strategy_params["preheat_max_outdoor_c"] is None


def test_66_a_ground_source_pump_has_no_defrost() -> None:
    """The outdoor coil frosts; a borehole does not."""
    assert _derive(hp_type="a2a").strategy_params["defrost"] is True
    assert _derive(hp_type="a2w").strategy_params["defrost"] is True
    assert _derive(hp_type="gshp").strategy_params["defrost"] is False


@pytest.mark.parametrize(
    ("outdoor", "factor"),
    [(-5.1, 1.0), (-5.0, 0.85), (0.0, 0.85), (3.0, 0.85), (3.1, 1.0), (-15.0, 1.0), (10.0, 1.0)],
)
def test_66_defrost_costs_a_sixth_of_the_cop_between_minus_five_and_plus_three(
    outdoor: float, factor: float
) -> None:
    """× 0.85 from -5 to +3 °C inclusive."""
    assert defrost_factor(outdoor) == factor


@pytest.mark.inv("INV-66")
def test_66_a_stored_pump_keeps_the_curve_it_was_built_with() -> None:
    """INV-66: the old air-to-water table stays in the subentry; no release rewrites it."""
    old = {"-15.0": 1.6, "-7.0": 2.2, "2.0": 2.9, "7.0": 3.4, "15.0": 4.0}
    derived = _derive(hp_type="a2w")

    load = SimpleNamespace(config=SimpleNamespace(params={**derived.params, "cop_curve": old}))

    assert curve_of(load).at(-7.0) == 2.2  # type: ignore[arg-type]
