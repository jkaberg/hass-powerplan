"""D4 §9 16, the Easee cloud row - a charger steered through an action (D4 §5.9).

The Easee cloud integration (HACS `easee`, nordicopen/easee_hass v0.9.74) exposes
no writable current at all: the charger's dynamic limit is set through the
action `easee.set_charger_dynamic_limit(device_id, current, time_to_live)` and
read back from `sensor.*_dynamic_charger_limit` (`services.py`, `const.py`). So
the profile binds `CURRENT_SET` to the sensor - the read-back witness (INV-22) -
and writes it through the device (D4 §5.10).

What is never bound, on the integration's own advice: the *Charger enabled*
switch and every non-dynamic limit, which live in the charger's flash ("only
the ones called dynamic are recommended to use on a regular basis",
ChargingControl wiki). Below 6 A the charger pauses and at 6 A or more it
resumes - the limit is the switch.

The fixture is written from the installed integration's source, not captured:
the reference house runs its Easee over Bluetooth (`easee_cloud_charger.json`,
D-0371).
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import Transport
from custom_components.powerplan.providers.profiles import easee_cloud, registry
from tests.providers.profiles.conftest import (
    THERMAL_DUMPS,
    binding_of,
    dump_view,
    entities_of,
)

FIXTURE = "easee_cloud_charger"
DYNAMIC_LIMIT = "sensor.carport_dynamic_charger_limit"
STATUS = "sensor.carport_status"


def test_16_the_written_charger_matches_on_its_entity_shapes() -> None:
    """No platform in a dump: the dynamic-limit sensor beside a status is the evidence."""
    match = easee_cloud.PROFILE.match(dump_view(FIXTURE))

    assert match.confidence == easee_cloud.SHAPE_CONFIDENCE == 0.8
    assert match.suggested_type == "ev"
    assert match.missing == ()


def test_16b_the_platform_is_worth_more_than_the_shapes() -> None:
    """`platform easee → 0.95`."""
    match = easee_cloud.PROFILE.match(dump_view(FIXTURE, platform="easee"))

    assert match.confidence == easee_cloud.PLATFORM_CONFIDENCE == 0.95


def test_16c_the_limit_is_the_dynamic_charger_limit_sensor() -> None:
    """Written through the action, read back from the sensor (D4 §5.10)."""
    match = easee_cloud.PROFILE.match(dump_view(FIXTURE))
    limit = binding_of(match, Role.CURRENT_SET)

    assert limit is not None
    assert limit.entity_id == DYNAMIC_LIMIT
    assert limit.writable
    assert limit.required
    assert (limit.unit, limit.scale) == ("A", 1.0)


def test_16d_the_read_roles() -> None:
    """Status, power in kW scaled to W, the energies, the flash maximum read-only, the reason."""
    match = easee_cloud.PROFILE.match(dump_view(FIXTURE))
    roles = entities_of(match)
    power = binding_of(match, Role.POWER)

    assert roles[Role.STATUS.value] == STATUS
    assert power is not None
    assert (power.entity_id, power.unit, power.scale) == ("sensor.carport_power", "kW", 1000.0)
    assert roles[Role.SESSION_ENERGY.value] == "sensor.carport_session_energy"
    assert roles[Role.ENERGY.value] == "sensor.carport_lifetime_energy"
    assert roles[Role.CURRENT_MAX.value] == "sensor.carport_max_charger_limit"
    assert roles[Role.BLOCKED_BY.value] == "sensor.carport_reason_for_no_current"


def test_16e_no_switch_and_no_flash_limit_is_ever_written() -> None:
    """Only the dynamic limit is writable; *Charger enabled* is not even bound."""
    match = easee_cloud.PROFILE.match(dump_view(FIXTURE))

    assert "switch.carport_charger_enabled" not in entities_of(match).values()
    assert binding_of(match, Role.ENABLE) is None
    assert [binding.role for binding in match.bindings if binding.writable] == [Role.CURRENT_SET]
    assert "limit_pauses" in match.capabilities
    assert easee_cloud.PROFILE.provisions(dump_view(FIXTURE)) == ()


def test_16f_the_quirks_are_the_easee_cloud_row_of_the_gate_table() -> None:
    """0.5 A, 60 s, a 30 s push, over the cloud (D4 §5.10)."""
    quirks = easee_cloud.PROFILE.quirks()

    assert quirks.transport is Transport.CLOUD
    assert (quirks.tolerance, quirks.min_interval_s, quirks.verify_after_s) == (0.5, 60.0, 30.0)
    assert quirks.statuses is easee_cloud.STATUSES
    assert quirks.forgets_limit_on_link_loss


def test_16g_a_limit_sensor_left_disabled_is_named_in_the_reasons() -> None:
    """The integration ships the read-back sensor disabled; the flow says to enable it."""
    view = dump_view(FIXTURE, states={DYNAMIC_LIMIT: "unavailable"})
    match = easee_cloud.PROFILE.match(view)

    assert binding_of(match, Role.CURRENT_SET) is not None
    assert any(DYNAMIC_LIMIT in reason and "enable" in reason for reason in match.reasons)


@pytest.mark.parametrize(
    "name", [*THERMAL_DUMPS, "easee_ble_charger", "zaptec_charger", "ams_datek_eva_han"]
)
def test_16h_easee_cloud_claims_no_other_device(name: str) -> None:
    """The Bluetooth Easee has a dynamic-current *number*, not a dynamic-limit sensor."""
    assert not easee_cloud.PROFILE.match(dump_view(name)).claimed


def test_16i_the_registry_ranks_easee_cloud_first() -> None:
    """Ahead of the generic profiles, which see a switch and a power sensor."""
    best = registry.best(dump_view(FIXTURE))

    assert best is not None
    assert best.profile == "easee_cloud"
