"""Fixtures for the D4 load tests (D9 §3).

Nothing here starts Home Assistant: `tests/core/` is the pure half of the suite
(D9 §3).

**The two fakes are behavioural, not static.** A static mock of a physical thing
hides it, and both defects these tests exist to catch - the setpoint
that ratcheted one degree per restart and the charger that dropped
twelve sessions in a night - only appear against a device that
*keeps state between calls*. `FakeThermostat` remembers its setpoint across
"restarts"; `FakeCharger`'s measured current follows the last limit it was
given, ends the session below the 6 A cliff and needs re-arming afterwards.

They are the smallest things with those behaviours, and they are deliberately shaped
like `tests/sim/`'s `SimLoad.step(dt_s, command, env) -> Reads` (D9 §3): `step()` takes
the writes of one `Command` and returns the `Reads` of the next tick. The scenario
runner swaps `FakeThermostat` for `sim.slab` and `FakeCharger` for `sim.ev` +
`sim.charger_ble` by changing the two fixtures at the bottom and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.loads import (
    ConstantSchedule,
    GateConfig,
    GateState,
    KindCtx,
    Load,
    LoadConfig,
    LoadCtx,
    LoadState,
    Mode,
    Reads,
    Role,
    RoleRead,
    TargetProfile,
    Transport,
    TransportBudget,
    Value,
    build_load,
)
from custom_components.powerplan.core.loads.kinds import (
    ModeCfg,
    ModeKind,
    Modulate,
    ModulateCfg,
    Setpoint,
    SetpointCfg,
)
from custom_components.powerplan.core.loads.stores import EnergyStore, SlabStore
from custom_components.powerplan.core.metering import ElectricalProfile, Reading, VoltageSystem
from custom_components.powerplan.core.model import Grant

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from custom_components.powerplan.core.loads import Command

OSLO = ZoneInfo("Europe/Oslo")

#: The reference house (D3 §5.1): 63 A, three-phase 230 V IT, ≈ 25.1 kW.
REFERENCE_PROFILE = ElectricalProfile(
    system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0, per_phase_limit_a=63.0
)

#: A cold February evening, well away from `HH:00:00` (HLD §7.1).
NOW = datetime(2026, 2, 3, 18, 7, 13, tzinfo=OSLO)

#: 230 V line-to-line on the IT system: a single-phase charger's W per amp.
W_PER_AMP = 230.0


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def reads(
    at: datetime = NOW,
    *,
    numbers: Mapping[Role, float] | None = None,
    texts: Mapping[Role, str] | None = None,
    options: Mapping[Role, Sequence[str]] | None = None,
    unavailable: Sequence[Role] = (),
    age_s: float = 0.0,
) -> Reads:
    """Build the `Reads` of one tick from plain values."""
    roles: dict[Role, RoleRead] = {}
    stamp = at - timedelta(seconds=age_s)
    for role, value in (numbers or {}).items():
        roles[role] = RoleRead(role=role, reading=Reading(value=value, at=stamp, source="fake"))
    for role, text in (texts or {}).items():
        existing = roles.get(role)
        roles[role] = RoleRead(
            role=role,
            reading=None if existing is None else existing.reading,
            text=text,
            options=() if existing is None else existing.options,
        )
    for role, opts in (options or {}).items():
        existing = roles.get(role)
        roles[role] = RoleRead(
            role=role,
            reading=None if existing is None else existing.reading,
            text=None if existing is None else existing.text,
            options=tuple(opts),
        )
    for role in unavailable:
        roles[role] = RoleRead(role=role, available=False)
    return Reads(at=at, roles=roles)


# --------------------------------------------------------------------------- #
# A thermostat that keeps its value between starts
# --------------------------------------------------------------------------- #


@dataclass
class FakeThermostat:
    """A thermostat with memory, an air sensor and its own regulation.

    The memory is the point of D4 §9 1: a controller that adopts what it finds
    walks the setpoint one band per restart, and only a device still holding
    last run's value can show it. `select_options` are the four the reference
    house's Z-TRM offers, spelled as Z-Wave JS spells them - including the eco
    option whose name contains "heating" (D4 §5.5).
    """

    setpoint: float = 22.0
    temp_c: float = 21.0
    mode: str = "Heating mode"
    select_options: tuple[str, ...] = (
        "Off",
        "Heating mode",
        "Cooling mode (Not implemented)",
        "Energy saving heating mode",
    )
    power_w: float = 0.0
    writes: list[tuple[Role, Value]] = field(default_factory=list)

    def step(self, dt_s: float, command: Command | None = None, at: datetime = NOW) -> Reads:
        """Apply one command, let the room move, return the next tick's reads."""
        for write in () if command is None else command.writes:
            self.writes.append((write.role, write.value))
            if write.role is Role.SETPOINT:
                self.setpoint = float(write.value)
            elif write.role is Role.MODE_SELECT:
                self.mode = str(write.value)
        aim = self.setpoint - (2.0 if "saving" in self.mode.lower() else 0.0)
        if self.mode == "Off":
            aim = 5.0
        drift = 0.2 * dt_s / 60.0
        self.temp_c += min(drift, max(-drift, aim - self.temp_c))
        self.power_w = 900.0 if self.temp_c < aim else 0.0
        return self.reads_at(at + timedelta(seconds=dt_s))

    def reads_at(self, at: datetime = NOW) -> Reads:
        """Return the `Reads` this thermostat presents at `at`."""
        return reads(
            at,
            numbers={
                Role.SETPOINT: self.setpoint,
                Role.TEMP: self.temp_c,
                Role.TEMP_FLOOR: self.temp_c,
                Role.POWER: self.power_w,
            },
            texts={Role.MODE_SELECT: self.mode},
            options={Role.MODE_SELECT: self.select_options},
        )


# --------------------------------------------------------------------------- #
# A charger with the 6 A cliff
# --------------------------------------------------------------------------- #


@dataclass
class FakeCharger:
    """An EV charger whose session ends below 6 A and needs re-arming.

    IEC 61851 signals available current as a pilot duty cycle that bottoms out
    at 6 A: write below it and the car opens its contactor and stops looking at
    the charger for about ten minutes (the ancestor's README, INV-28). The measured
    current follows the last limit a poll later, which is what made the
    30-second square wave possible.
    """

    limit_a: float = 16.0
    enabled: bool = True
    status: str = "charging"
    soc: float | None = 42.0
    cooldown_s: float = 0.0
    writes: list[tuple[Role, Value]] = field(default_factory=list)

    def step(self, dt_s: float, command: Command | None = None, at: datetime = NOW) -> Reads:
        """Apply one command, age the session by `dt_s`, return the new reads."""
        for write in () if command is None else command.writes:
            self.writes.append((write.role, write.value))
            if write.role is Role.CURRENT_SET:
                self.limit_a = float(write.value)
                if 0.0 < self.limit_a < 6.0:
                    self.status = "disconnected_cable"
                    self.cooldown_s = 600.0
            elif write.role is Role.ENABLE:
                self.enabled = bool(write.value)
        self.cooldown_s = max(0.0, self.cooldown_s - dt_s)
        if not self.enabled:
            self.status = "awaiting_start"
        elif self.cooldown_s == 0.0 and self.limit_a >= 6.0:
            self.status = "charging"
        return self.reads_at(at + timedelta(seconds=dt_s))

    @property
    def measured_w(self) -> float:
        """What the power sensor says - 0 W unless a session is actually running."""
        if self.status != "charging":
            return 0.0
        return self.limit_a * W_PER_AMP

    def reads_at(self, at: datetime = NOW, *, age_s: float = 0.0) -> Reads:
        """Return the `Reads` this charger presents at `at`."""
        numbers: dict[Role, float] = {
            Role.CURRENT_SET: self.limit_a,
            Role.CURRENT_MAX: 32.0,
            Role.POWER: self.measured_w,
        }
        if self.soc is not None:
            numbers[Role.SOC] = self.soc
        return reads(
            at,
            numbers=numbers,
            texts={Role.STATUS: self.status, Role.ENABLE: "on" if self.enabled else "off"},
            age_s=age_s,
        )


@pytest.fixture
def thermostat() -> FakeThermostat:
    """Return a thermostat that keeps its setpoint between starts."""
    return FakeThermostat()


@pytest.fixture
def charger() -> FakeCharger:
    """Return a charger with the 6 A cliff and a ten-minute sulk."""
    return FakeCharger()


@pytest.fixture
def profile() -> ElectricalProfile:
    """Return the reference house's electrical connection."""
    return REFERENCE_PROFILE


# --------------------------------------------------------------------------- #
# Builders - the shapes D4 §6 derives, written out
# --------------------------------------------------------------------------- #


def bathroom_target(**kwargs: Any) -> TargetProfile:
    """Return the reference house's bathroom loop: comfort 24 °C, floor 21, cap 27 (D4 §6.1)."""
    options: dict[str, Any] = {
        "schedule": ConstantSchedule(24.0),
        "comfort_default": 24.0,
        "floor": 21.0,
        "ceiling": 27.0,
    }
    options.update(kwargs)
    return TargetProfile(**options)


def slab() -> SlabStore:
    """Return the living-room slab: 57.5 m², 50 mm screed (D4 §9 13)."""
    return SlabStore(area_m2=57.5, screed_mm=50.0, loss_coeff_w_per_k=None, max_c=27.0)


def ev_store() -> EnergyStore:
    """Return a 64 kWh car (D4 §6.2's review sentence)."""
    return EnergyStore(capacity_kwh=64.0, min_soc=20.0, max_soc=80.0, max_charge_w=7360.0)


#: What `floor_heating.derive()` materialises for a bathroom loop under wood
#: with a cable in screed (D4 §6.1), written out so a kind test needs no flow.
FLOOR_PARAMS: Mapping[str, Any] = {
    "kind": "setpoint",
    "store": "slab",
    "comfort_c": 24.0,
    "floor_c": 21.0,
    "max_c": 27.0,
    "shed_setpoint_c": 21.0,
    "eco_setpoint_c": 22.0,
    "area_m2": 12.0,
    "screed_mm": 40.0,
    "w_per_m2": 80.0,
    "sensor": "floor",
    "swing_k": 1.0,
    "min_on_s": 900,
    "min_off_s": 900,
    "command_interval_s": 600,
    "substitutable": False,
    "follow_presence": True,
}

#: What `ev.derive()` materialises for a 64 kWh car on a 32 A single-phase
#: charger (D4 §6.2).
EV_PARAMS: Mapping[str, Any] = {
    "capacity_kwh": 64.0,
    "max_a": 32.0,
    "min_a": 6.0,
    "phases": 1,
    "target_soc": 80.0,
    "min_soc_now": 20.0,
    "charge_eff": 0.90,
    "step_up_a": 4.0,
    "settle_s": 60,
    "suppress_delta_a": 2.0,
    "suppress_stale_s": 60,
    "force_max_h": 6.0,
    "departures": {"0": "07:00", "1": "07:00", "2": "07:00", "3": "07:00", "4": "07:00"},
}


def floor_config(**kwargs: Any) -> LoadConfig:
    """Return a bathroom floor loop, overridable field by field."""
    options: dict[str, Any] = {
        "load_id": "loop_bath",
        "name": "Bathroom floor",
        "type_key": "floor_heating",
        "priority": 32,
        "nameplate_w": 960.0,
        "phases": 1,
        "params": dict(FLOOR_PARAMS),
        "target": bathroom_target(),
        "transport": Transport.ZWAVE,
    }
    options.update(kwargs)
    return LoadConfig(**options)


def ev_config(**kwargs: Any) -> LoadConfig:
    """Return the reference house's charger, overridable field by field."""
    options: dict[str, Any] = {
        "load_id": "ev",
        "name": "Car charger",
        "type_key": "ev",
        "priority": 10,
        "nameplate_w": 32.0 * W_PER_AMP,
        "phases": 1,
        "params": dict(EV_PARAMS),
        "transport": Transport.BLE,
    }
    options.update(kwargs)
    return LoadConfig(**options)


def floor_load(**kwargs: Any) -> Load:
    """Build the floor loop through the type registry, as D7 will."""
    return build_load(floor_config(**kwargs))


def ev_load(**kwargs: Any) -> Load:
    """Build the charger through the type registry, as D7 will."""
    return build_load(ev_config(**kwargs))


def setpoint_kind(**kwargs: Any) -> Setpoint:
    """Return a generic thermostat `SETPOINT` kind, defaults from D4 §5.10."""
    options: dict[str, Any] = {"shed_setpoint": 21.0}
    options.update(kwargs)
    return Setpoint(SetpointCfg(**options))


def mode_kind(**kwargs: Any) -> ModeKind:
    """Return a `MODE` kind over an operation-mode select (D4 §5.5)."""
    return ModeKind(ModeCfg(**kwargs))


def modulate_kind(**kwargs: Any) -> Modulate:
    """Return an EV `MODULATE` kind in amps with the 6 A cliff (D4 §5.3)."""
    options: dict[str, Any] = {
        "unit": "a",
        "min_value": 6.0,
        "max_value": 32.0,
        "step": 1.0,
        "cliff": True,
    }
    options.update(kwargs)
    return Modulate(ModulateCfg(**options))


def gate_config(**kwargs: Any) -> GateConfig:
    """Return a `GateConfig` with the generic-setpoint defaults of D4 §5.10."""
    options: dict[str, Any] = {"tolerance": 0.05, "min_interval_s": 120.0, "verify_after_s": 60.0}
    options.update(kwargs)
    return GateConfig(**options)


def gate_state(**kwargs: Any) -> GateState:
    """Return a `GateState`, empty unless told otherwise."""
    return GateState(**kwargs)


def load_state(**kwargs: Any) -> LoadState:
    """Return a `LoadState`, `auto` and untouched unless told otherwise."""
    return LoadState(**kwargs)


def budget(**kwargs: Any) -> TransportBudget:
    """Return an empty site transport budget."""
    return TransportBudget.empty(**kwargs)


def load_ctx(**kwargs: Any) -> LoadCtx:
    """Return the context one tick of a load depends on (D4 §5.1)."""
    options: dict[str, Any] = {
        "now": NOW,
        "reads": reads(),
        "electrical": REFERENCE_PROFILE,
        "budget": TransportBudget.empty(),
    }
    options.update(kwargs)
    return LoadCtx(**options)


def kind_ctx(**kwargs: Any) -> KindCtx:
    """Return the context one control kind depends on (D4 §4.2)."""
    options: dict[str, Any] = {
        "now": NOW,
        "reads": reads(),
        "electrical": REFERENCE_PROFILE,
        "phases": 1,
        "mode": Mode.AUTO,
    }
    options.update(kwargs)
    return KindCtx(**options)


def grant(w: float = 0.0, **kwargs: Any) -> Grant:
    """Return a `Grant` from D6, defaulting to "nothing, and not a shed" (INV-25)."""
    options: dict[str, Any] = {
        "w": w,
        "shed": False,
        "shed_reason": None,
        "stop_ok": False,
        "stage": 0,
        "blunt": False,
        "capped_by": (),
    }
    options.update(kwargs)
    return Grant(**options)
