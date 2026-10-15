"""D4 §9 64, 65 - lowering comfort is opt-in, and the limits from the standards (D-0772, D-0775).

A new thermal load keeps its target at comfort until the household asks for a
setback; the night one follows the site's quiet hours, moves the target and
never the floor (INV-55), and its end is a step-up `deadlines()` hands D5.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from custom_components.powerplan.core.loads import device_types
from custom_components.powerplan.core.loads.questionnaire import QCtx, materialise, rederive
from custom_components.powerplan.core.loads.targets import (
    ConstantSchedule,
    PresenceMode,
    TargetProfile,
    profile_from_params,
)
from tests.core.loads.conftest import OSLO

HEATING = ("floor_heating", "heat_pump", "radiator")
EVENING = datetime(2026, 2, 2, 21, 59, tzinfo=OSLO)
NIGHT = datetime(2026, 2, 2, 22, 0, tzinfo=OSLO)
MORNING = datetime(2026, 2, 3, 7, 0, tzinfo=OSLO)


def _derive(type_key: str, answers: dict[str, object]) -> dict[str, object]:
    device_type = device_types.get(type_key)
    validated = device_type.questionnaire.validate(answers, QCtx())
    return dict(device_type.derive(validated, QCtx()).params)


def _night_profile(**kwargs: object) -> TargetProfile:
    options: dict[str, object] = {
        "schedule": ConstantSchedule(22.0),
        "comfort_default": 22.0,
        "floor": 19.0,
        "ceiling": 27.0,
        "night_setback": True,
        "night": (time(22, 0), time(7, 0)),
        "zone": OSLO,
    }
    options.update(kwargs)
    return TargetProfile(**options)  # type: ignore[arg-type]


@pytest.mark.inv("INV-66")
@pytest.mark.parametrize("type_key", [*HEATING, "water_heater"])
def test_64_a_new_load_follows_nobody_and_no_night(type_key: str) -> None:
    """`follow_presence` is off for all four thermal types; `night_setback` for the three that heat."""
    params = _derive(type_key, {})
    assert params["follow_presence"] is False
    assert params.get("night_setback", False) is False
    profile = profile_from_params(params)
    assert profile is not None
    assert profile.target(NIGHT, PresenceMode.AWAY) == profile.comfort_default


@pytest.mark.inv("INV-55")
def test_64_the_night_lowers_the_target_by_a_kelvin_and_never_the_floor() -> None:
    """22 °C reads 21 °C from 22:00 to 07:00 under the default quiet hours."""
    profile = _night_profile()
    assert profile.target(EVENING, PresenceMode.HOME) == pytest.approx(22.0)
    assert profile.target(NIGHT, PresenceMode.HOME) == pytest.approx(21.0)
    assert profile.target(NIGHT + timedelta(hours=8, minutes=59), PresenceMode.HOME) == (
        pytest.approx(21.0)
    )
    assert profile.target(MORNING, PresenceMode.HOME) == pytest.approx(22.0)
    deep = _night_profile(night_delta=10.0)
    assert deep.target(NIGHT, PresenceMode.HOME) == pytest.approx(19.0), "the floor holds"
    assert deep.floor == pytest.approx(19.0)


@pytest.mark.inv("INV-55")
def test_64_cooling_raises_the_target_at_night_up_to_the_ceiling() -> None:
    """The sign flips for cooling; the clamp is the other way round."""
    profile = _night_profile(
        schedule=ConstantSchedule(23.0),
        comfort_default=23.0,
        direction="cool",
        floor=26.0,
        ceiling=20.0,
    )
    assert profile.target(NIGHT, PresenceMode.HOME) == pytest.approx(24.0)


@pytest.mark.inv("INV-55")
def test_64_the_nights_end_is_a_step_up_deadline() -> None:
    """`deadlines` holds 07:00 at the comfort target, and the night's start is not one."""
    profile = _night_profile()
    from_ = datetime(2026, 2, 2, 12, 0, tzinfo=OSLO)
    assert profile.deadlines(from_, from_ + timedelta(hours=36), PresenceMode.HOME) == (
        (MORNING, 22.0),
    )
    off = _night_profile(night_setback=False)
    assert off.deadlines(from_, from_ + timedelta(hours=36), PresenceMode.HOME) == ()


@pytest.mark.inv("INV-55")
def test_64_the_night_is_the_sites_quiet_hours_in_the_sites_zone() -> None:
    """The runtime puts the quiet hours in the parameters; the profile reads them back."""
    params = {
        **_derive("radiator", {"night_setback": True}),
        "night_start": "23:00:00",
        "night_end": "06:30:00",
        "night_zone": "Europe/Oslo",
    }
    profile = profile_from_params(params)
    assert profile is not None
    assert profile.night == (time(23, 0), time(6, 30))
    comfort = profile.comfort_default
    assert profile.target(datetime(2026, 2, 2, 22, 30, tzinfo=OSLO), PresenceMode.HOME) == comfort
    assert profile.target(datetime(2026, 2, 2, 23, 0, tzinfo=OSLO), PresenceMode.HOME) == (
        pytest.approx(comfort - 1.0)
    )


@pytest.mark.inv("INV-66")
def test_64_a_stored_follow_presence_survives_rederive() -> None:
    """A load set up with the old default keeps its switch through *Re-derive*."""
    device_type = device_types.get("floor_heating")
    answers = device_type.questionnaire.validate({"follow_presence": True}, QCtx())
    stored = materialise("floor_heating", answers, device_type.derive(answers, QCtx()))
    fresh, diff = rederive(stored, device_type, QCtx())
    assert fresh.params["follow_presence"] is True
    assert "follow_presence" not in diff


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ({"room": "bedroom"}, 18.0),
        ({"room": "bathroom"}, 21.0),
    ],
)
def test_65_the_bedroom_floor_is_the_whos_18(answers: dict[str, object], expected: float) -> None:
    """D-0775: 18 °C, WHO's cold-season minimum; the other rooms are unchanged."""
    assert _derive("floor_heating", answers)["floor_c"] == pytest.approx(expected)


@pytest.mark.parametrize(
    ("room", "covering", "expected"),
    [
        ("bathroom", "tile", 30.0),
        ("living", "tile", 29.0),
        ("bedroom", "tile", 29.0),
        ("bathroom", "wood", 27.0),
        ("living", "laminate", 27.0),
        ("living", "vinyl", 27.0),
        ("living", "carpet", 27.0),
    ],
)
def test_65_the_coverings_maxima(room: str, covering: str, expected: float) -> None:
    """EN 1264: room + 9 K in a bathroom, 29 °C elsewhere; floating floors 27 °C."""
    assert _derive("floor_heating", {"room": room, "covering": covering})["max_c"] == (
        pytest.approx(expected)
    )
