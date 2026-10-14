"""Needs are declared (D4 §9 57, D-0746, INV-76).

Every role a type's or a kind's code touches is a need someone declared, so a
data point can no longer be read without the flow knowing to look for it - the
six floors lost four roles because nothing said they were wanted. The tiers
merge highest-first, a `mode` floor needs what a `setpoint` floor doesn't, and
every need has its two texts in both languages.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.needs import Need, Tier, need, needs_of
from custom_components.powerplan.core.loads.registry import CONTROL_KINDS, device_types
from custom_components.powerplan.core.strategies import base as strategies

PACKAGE = Path(__file__).parents[3] / "custom_components" / "powerplan"
LOADS = PACKAGE / "core" / "loads"
#: Roles a kind writes that no entity carries: the battery's command is spelled
#: out into the row's levers by its profile (D4 §5.9).
VIRTUAL = frozenset({Role.BATTERY_COMMAND, Role.BATTERY_COMMAND_MODE})


def roles_in(path: Path) -> frozenset[Role]:
    """Return every `Role.X` the module names."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return frozenset(
        Role[node.attr]
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Role"
        and node.attr in Role.__members__
    )


def declared(type_key: str, kind: str, strategy: str | None = None) -> tuple[Need, ...]:
    """Return what a load of `type_key` on `kind` with `strategy` needs."""
    device_type = device_types.get(type_key)
    chosen = strategies.get(strategy or device_type.default_strategy)
    return needs_of(device_type.needs(kind), CONTROL_KINDS[kind].kind.needs, chosen.needs)


def tier_of(needs: tuple[Need, ...], role: Role) -> Tier | None:
    """Return `role`'s tier among `needs`, or `None`."""
    return next((each.tier for each in needs if each.role is role), None)


@pytest.mark.inv("INV-76")
@pytest.mark.parametrize("type_key", device_types.keys())
def test_every_role_a_type_names_is_a_need(type_key: str) -> None:
    """A type module that reads a role without declaring it fails here."""
    device_type = device_types.get(type_key)
    wanted = {
        each.role
        for kind in device_type.kinds
        for strategy in device_type.strategies
        for each in declared(type_key, kind, strategy)
    }
    missing = roles_in(LOADS / "types" / f"{type_key}.py") - wanted
    assert not missing, f"{type_key} reads {sorted(missing)} without a need"


@pytest.mark.inv("INV-76")
@pytest.mark.parametrize("kind", sorted(CONTROL_KINDS))
def test_every_role_a_kind_names_is_a_need_of_it_or_of_a_type_on_it(kind: str) -> None:
    """A kind writes its own roles, or the role the type on it chose."""
    users = [device_types.get(key) for key in device_types.keys()]  # noqa: SIM118 - the registry's function
    wanted = {each.role for each in CONTROL_KINDS[kind].kind.needs} | {
        each.role for user in users if kind in user.kinds for each in user.needs(kind)
    }
    missing = roles_in(LOADS / "kinds" / f"{kind}.py") - wanted - VIRTUAL
    assert not missing, f"{kind} names {sorted(missing)} without a need"


def test_a_mode_floor_needs_the_select_and_the_eco_setpoint_and_a_setpoint_floor_neither() -> None:
    """The kind adds its own: a mode floor sheds by its select to a preloaded eco."""
    mode = declared("floor_heating", "mode")
    setpoint = declared("floor_heating", "setpoint")

    assert tier_of(mode, Role.MODE_SELECT) is Tier.REQUIRED
    assert tier_of(mode, Role.ECO_SETPOINT) is Tier.RECOMMENDED
    assert tier_of(setpoint, Role.MODE_SELECT) is None
    assert tier_of(setpoint, Role.ECO_SETPOINT) is None
    for needs in (mode, setpoint):
        assert tier_of(needs, Role.FLOOR_MIN) is Tier.RECOMMENDED
        assert tier_of(needs, Role.TEMP_FLOOR) is Tier.RECOMMENDED


def test_hot_water_raises_the_tank_probes_to_recommended() -> None:
    """`deadline_fill` leaves the top and bottom probes optional; `hot_water` wants them."""
    plain = declared("water_heater", "setpoint", "deadline_fill")
    planned = declared("water_heater", "setpoint", "hot_water")

    assert tier_of(plain, Role.TEMP_TOP) is Tier.OPTIONAL
    assert tier_of(planned, Role.TEMP_TOP) is Tier.RECOMMENDED
    assert tier_of(planned, Role.TEMP_BOTTOM) is Tier.RECOMMENDED
    assert next(n for n in planned if n.role is Role.TEMP_TOP).key == "hot_water_temp_top"


def test_the_highest_tier_wins_and_the_first_declared_between_equals() -> None:
    """A required role stays required whoever else calls it optional."""
    merged = needs_of(
        (need("a", Role.POWER, Tier.OPTIONAL), need("a", Role.TEMP, Tier.RECOMMENDED)),
        (need("b", Role.POWER, Tier.REQUIRED), need("b", Role.TEMP, Tier.RECOMMENDED)),
    )

    assert [(each.role, each.tier, each.key) for each in merged] == [
        (Role.POWER, Tier.REQUIRED, "b_power"),
        (Role.TEMP, Tier.RECOMMENDED, "a_temp"),
    ]


def every_need() -> set[Need]:
    """Return every need any type, kind or strategy declares."""
    found = {
        each
        for key in device_types.keys()  # noqa: SIM118 - the registry's function
        for kind in device_types.get(key).kinds
        for each in device_types.get(key).needs(kind)
    }
    found |= {each for entry in CONTROL_KINDS.values() for each in entry.kind.needs}
    found |= {each for key in strategies.keys() for each in strategies.get(key).needs}  # noqa: SIM118 - the registry's function
    return found


@pytest.mark.parametrize("language", ["en", "nb"])
def test_every_need_says_what_it_is_for_and_what_happens_without_it(language: str) -> None:
    """Both texts, in both languages, for every key - and no text for a need nobody declares."""
    selector = json.loads(
        (PACKAGE / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )["selector"]
    keys = {each.key for each in every_need()}

    for vocabulary in ("need_purpose", "need_without"):
        assert set(selector[vocabulary]["options"]) == keys, vocabulary
