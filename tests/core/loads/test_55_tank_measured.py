"""D4 §9 55 - a tank measured at setup, and its level the mean of its probes (D-0741).

What the recorder knows beats D4 §6.3's tables, and an answer beats both - the
same order the floors use (D-0731): the element's p95, the standby loss and the
household's daily draw. A tank with probes above and below its own reads its
mean, the heat it holds: after the afternoon's showers the reference tank read
69 / 64 / 28 °C top to bottom, 64 °C at the centre probe and 53.7 °C as a mean.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads import LoadState, Role
from custom_components.powerplan.core.loads.questionnaire import Measured, QCtx
from custom_components.powerplan.core.loads.types import base as device_types
from custom_components.powerplan.core.loads.types.water_heater import draw_off_of
from tests.core.loads.conftest import NOW, load_ctx, load_from, reads

TANK = device_types.get("water_heater")
HOUSE = {"litres": "300", "element_kw": "3", "persons": 5.0, "control": "thermostat"}


def _derive(raw: dict[str, object], measured: dict[str, Measured]) -> dict[str, object]:
    ctx = QCtx(capabilities=frozenset({"setpoint"}), measured=measured)
    return dict(TANK.derive(TANK.questionnaire.validate(raw, ctx), ctx).params)


def test_55_the_recorder_beats_the_table() -> None:
    """2.9 kW, 148 W and 6.2 kWh a day from history replace 3 kW, 60 W and persons × 45 L."""
    params = _derive(
        HOUSE,
        {
            "nameplate_w": Measured(value=2904.0, n=900, days=10.0),
            "standby_loss_w": Measured(value=148.3, n=12, days=9.0),
            "draw_kwh_day": Measured(value=6.24, n=8, days=9.0),
        },
    )
    assert params["nameplate_w"] == 2904
    assert params["standby_loss_w"] == 148
    assert params["draw_kwh_day"] == 6.2


def test_55b_an_answer_beats_the_recorder_and_nothing_measured_keeps_the_table() -> None:
    """A typed 80 W stands; with no history the 60 W default and no daily figure."""
    answered = _derive(
        {**HOUSE, "standby_loss_w": 80.0}, {"standby_loss_w": Measured(148.0, 12, 9.0)}
    )
    assert answered["standby_loss_w"] == 80.0
    plain = _derive(HOUSE, {})
    assert plain["standby_loss_w"] == 60.0
    assert plain["draw_kwh_day"] is None
    assert plain["nameplate_w"] == 3000.0


def test_55c_a_measured_day_is_the_profile_s_day() -> None:
    """6.2 kWh a day for five people is what the draw-off profile then spends."""
    profile = draw_off_of({"persons": 5, "draw_kwh_day": 6.2})
    assert profile.kwh_per_day == pytest.approx(6.2)
    assert draw_off_of({"persons": 5}).kwh_per_day == pytest.approx(
        5 * 45.0 * 4.186 * 47.0 / 3600.0
    )


def test_55d_the_level_is_the_mean_of_the_probes() -> None:
    """69 / 64 / 28 °C reads 53.7 °C; the centre probe alone reads 64 °C."""
    load = load_from("water_heater", HOUSE)
    state = LoadState()
    three = load_ctx(
        reads=reads(NOW, numbers={Role.TEMP_TOP: 69.0, Role.TEMP: 64.0, Role.TEMP_BOTTOM: 28.0})
    )
    one = load_ctx(reads=reads(NOW, numbers={Role.TEMP: 64.0}))
    assert TANK.level(load, state, three) == pytest.approx(53.67, abs=0.01)
    assert TANK.level(load, state, one) == 64.0
