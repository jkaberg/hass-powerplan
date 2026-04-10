"""Control kinds - how hardware is steered (D4 §3, §4.2, §5.3–5.6).

Four kinds in v1: `MODULATE` (amps or watts, signed), `SETPOINT`, `MODE` and
`SWITCH`. `SG_READY` is designed (D4 §2) and lands in v1.x as a fifth module
here, registered the same way - the registry needs no change to take it.

A kind is orthogonal to what the device *is*: the same `Setpoint` drives a tank,
a panel heater and a heat pump, and the numbers that differ between them are
configuration the questionnaire derived.
"""

from .base import (
    Action,
    Command,
    ControlKind,
    Desired,
    Hold,
    KindCtx,
    Quantised,
    Reads,
    Role,
    RoleRead,
    Value,
    Write,
)
from .mode import ECO_TOKENS, ModeCfg, ModeKind, match_option
from .modulate import AMP_EPS, EV_MIN_A, Modulate, ModulateCfg
from .setpoint import BAND_MAX_K, Setpoint, SetpointCfg
from .switch import Switch, SwitchCfg

__all__ = [
    "AMP_EPS",
    "BAND_MAX_K",
    "ECO_TOKENS",
    "EV_MIN_A",
    "Action",
    "Command",
    "ControlKind",
    "Desired",
    "Hold",
    "KindCtx",
    "ModeCfg",
    "ModeKind",
    "Modulate",
    "ModulateCfg",
    "Quantised",
    "Reads",
    "Role",
    "RoleRead",
    "Setpoint",
    "SetpointCfg",
    "Switch",
    "SwitchCfg",
    "Value",
    "Write",
    "match_option",
]
