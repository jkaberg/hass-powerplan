"""D4 §9 38 - plug-in batteries powerplan sets the output of: Anker, EcoFlow, Zendure.

Each charges from its own panels and gives the house what its output number says.
The battery's setpoint is signed, negative discharging, so the output number is
bound with a scale of −1: −400 W writes 400. A charge is never asked of it - the
profile says `output_only` and the battery type caps the command's charge side at
0 W - and each vendor's cloud sets how often powerplan may write. The fixtures are
written from each integration's own source (D-0081).
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads import Role, device_types
from custom_components.powerplan.core.loads.gate import Transport, Write
from custom_components.powerplan.core.loads.questionnaire import Answers, QCtx
from custom_components.powerplan.core.loads.types.battery import OUTPUT_ONLY_CAPABILITY
from custom_components.powerplan.providers.profiles import output_limit, registry
from tests.providers.profiles.conftest import THERMAL_DUMPS, dump_view

ROWS = [
    ("anker_solix_solarbank", output_limit.ANKER_SOLIX, "number.solarbank_system_output_preset"),
    ("ecoflow_powerstream", output_limit.ECOFLOW_CLOUD, "number.powerstream_custom_load_power"),
    ("zendure_hyper", output_limit.ZENDURE, "number.hyper_2000_output_limit"),
]
IDS = [row[1].key for row in ROWS]


@pytest.mark.parametrize(("fixture", "profile", "output"), ROWS, ids=IDS)
def test_38_each_plug_in_battery_is_claimed_by_its_own_profile(
    fixture: str, profile: output_limit.OutputLimitProfile, output: str
) -> None:
    """Platform evidence at 0.95; the output number as the setpoint; `output_only` said."""
    best = registry.best(dump_view(fixture))

    assert best is not None
    assert best.profile == profile.key
    assert best.missing == ()
    assert OUTPUT_ONLY_CAPABILITY in best.capabilities
    bound = {binding.role: binding for binding in best.bindings}
    assert bound[Role.BATTERY_POWER_SET].entity_id == output
    assert bound[Role.BATTERY_POWER_SET].scale == -1.0
    assert Role.SOC in bound


@pytest.mark.parametrize(("fixture", "profile", "output"), ROWS, ids=IDS)
def test_38_a_discharge_is_the_output_and_a_charge_is_nothing(
    fixture: str, profile: output_limit.OutputLimitProfile, output: str
) -> None:
    """−400 W writes an output of 400; +300 W clamps to the number's own 0."""
    device = profile.bind(profile.match(dump_view(fixture)).bindings)

    give = device.call_for(Write(Role.BATTERY_POWER_SET, -400.0))
    take = device.call_for(Write(Role.BATTERY_POWER_SET, 300.0))

    assert give is not None
    assert (give.domain, give.service, give.entity_id, give.data) == (
        "number",
        "set_value",
        output,
        {"value": 400},
    )
    assert take is not None
    assert take.data == {"value": 0}


def test_38_each_vendor_s_cloud_sets_the_write_interval() -> None:
    """Anker's 5-minute cloud cadence; EcoFlow's cloud and Zendure's MQTT once a minute."""
    anker = output_limit.ANKER_SOLIX.quirks()
    assert (anker.transport, anker.min_interval_s, anker.verify_after_s) == (
        Transport.CLOUD,
        300.0,
        360.0,
    )
    assert output_limit.ECOFLOW_CLOUD.quirks().min_interval_s == 60.0
    assert output_limit.ZENDURE.quirks().transport is Transport.MQTT


@pytest.mark.parametrize("other", THERMAL_DUMPS)
def test_38_no_thermostat_is_a_plug_in_battery(other: str) -> None:
    """A heater's power knob is not a battery's output."""
    view = dump_view(other)
    assert all(row[1].match(view).confidence == 0.0 for row in ROWS)


def test_38_the_battery_never_commands_a_charge_of_an_output_only_battery() -> None:
    """`output_only`: the kind's charge side is 0 W, whatever the household's charge power."""
    device_type = device_types.get("battery")
    answers = Answers(
        {question.key: question.default for question in device_type.questionnaire.questions}
    )
    plug_in = device_type.derive(answers, QCtx(capabilities=frozenset({OUTPUT_ONLY_CAPABILITY})))
    inverter = device_type.derive(answers, QCtx())

    assert plug_in.params["command_charge_w"] == 0.0
    assert inverter.params["command_charge_w"] == inverter.params["max_charge_w"]
