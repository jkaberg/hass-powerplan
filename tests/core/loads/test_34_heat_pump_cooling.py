"""D4 §9 34 - a heat pump in cooling mode (D4 §5.14).

D4 §5.14 wrote the cooling mode as "the same band logic with the sign flipped",
and D5's `heat_capacitor` already banks downwards for a cooling store (D5 §9 8).
What the type lacked was the other half: a comfort that is violated *above* its
limit, a deficit that is the room's excess, a store whose bank end is below the
target, and a setpoint kind that rests at the warm limit and calls a lower
setpoint "on". Everything here is the mirror of `test_18_heat_pump.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from custom_components.powerplan.core.loads import Desired, LoadState, Role
from custom_components.powerplan.core.loads.types.heat_pump import FLOOR_BELOW_COMFORT_K
from tests.core.loads.conftest import OSLO, grant, load_ctx, load_from, materialised, reads

NOON = datetime(2026, 7, 15, 12, 0, tzinfo=OSLO)
REFERENCE = {"hp_type": "a2a", "rated_kw": 1.5, "area_m2": 60.0, "building": "2000_2010"}
COOLS = {"capabilities": frozenset({"cool"})}


def cooler(**answers: Any) -> Any:
    """Return the reference unit, set to cool at 24 °C."""
    return load_from(
        "heat_pump", {**REFERENCE, "comfort_c": 24.0, "cooling": True, **answers}, qctx=COOLS
    )


def ctx(room_c: float, setpoint_c: float = 24.0, outdoor_c: float = 35.0, **kwargs: Any) -> Any:
    """One summer tick: the room, the unit's setpoint, the heat outside."""
    return load_ctx(
        now=NOON,
        reads=reads(
            NOON,
            numbers={
                Role.TEMP: room_c,
                Role.SETPOINT: setpoint_c,
                Role.POWER: 600.0,
                Role.OUTDOOR_TEMP: outdoor_c,
            },
        ),
        outdoor_c=outdoor_c,
        zone=OSLO,
        **kwargs,
    )


def test_34_cooling_is_asked_only_of_a_unit_that_can_cool() -> None:
    """Without `cool` in the capabilities the answer is ignored: the unit heats."""
    heats = materialised("heat_pump", {**REFERENCE, "cooling": True})["params"]
    cools = materialised("heat_pump", {**REFERENCE, "comfort_c": 24.0, "cooling": True}, **COOLS)[
        "params"
    ]

    assert heats["direction"] == "heat"
    assert cools["direction"] == "cool"
    # The limit is above comfort and the bank's end below it (INV-55, INV-56).
    assert cools["floor_c"] == 24.0 + FLOOR_BELOW_COMFORT_K
    assert cools["max_c"] == 24.0 - cools["band_k"]


def test_34_comfort_is_violated_above_the_warm_limit_and_the_deficit_is_the_excess() -> None:
    """A room at 29 °C is over a 28 °C limit; 25.5 °C is 1.5 K to take out."""
    load = cooler()

    hot = load.device_type.comfort(load, ctx(29.0))
    warm = load.device_type.comfort(load, ctx(25.5))

    assert hot.violated
    assert not warm.violated
    assert warm.deficit == 1.5
    assert warm.direction == "cool"


def test_34_the_store_banks_downwards() -> None:
    """The room's store is a cooling store, so D5 banks coolness below the target."""
    load = cooler()

    assert load.store is not None
    assert load.store.direction == "cool"
    assert load.store.min_level() == 24.0 - 1.0
    assert load.store.max_level() == 24.0 + FLOOR_BELOW_COMFORT_K


def test_34_a_bank_lowers_the_setpoint_within_the_band_and_never_below_it() -> None:
    """D5's pre-cooling reaches the device as a lower setpoint, clamped to comfort − band."""
    load = cooler()

    _, bank = load.apply(grant(1500.0), LoadState(), ctx(24.0, setpoint_delta=-3.0))

    assert bank.command is not None
    (write,) = bank.command.writes
    assert write.value == 23.0
    assert bank.command.want_on


def test_34_a_shed_raises_the_setpoint_to_the_warm_band_end() -> None:
    """Shedding a cooler is letting the room warm: the setpoint goes up, never down."""
    load = cooler()

    _, shed = load.apply(
        grant(0.0, shed=True, shed_reason="ceiling"), LoadState(), ctx(24.0, desired=Desired.SHED)
    )

    assert shed.command is not None
    (write,) = shed.command.writes
    assert float(write.value) > 24.0
    assert not shed.command.want_on


def test_34_the_heat_to_remove_grows_with_the_heat_outside() -> None:
    """Cooling demand is the envelope's loss times outdoor minus target, never negative."""
    load = cooler()

    at_35 = load.device_type.heat_demand_w(load, ctx(24.0, outdoor_c=35.0))
    at_20 = load.device_type.heat_demand_w(load, ctx(24.0, outdoor_c=20.0))

    assert at_35 is not None
    assert at_35 > 0.0
    assert at_20 == 0.0


def test_34_the_esphome_capture_offers_cooling() -> None:
    """The reference house's air-to-air unit lists `cool`, so the question is asked."""
    from custom_components.powerplan.providers.profiles import generic_climate  # noqa: PLC0415
    from tests.providers.profiles.conftest import dump_view  # noqa: PLC0415 - HA-side helper

    match = generic_climate.PROFILE.match(dump_view("esphome_air_to_air_heatpump"))

    assert "cool" in match.capabilities
    assert match.suggested_type == "heat_pump"
