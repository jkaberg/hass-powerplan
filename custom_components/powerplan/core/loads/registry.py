"""The two registries of D4, and the one function that uses both (D4 §3, §4.6).

Extension is by registry, not by conditional: a new device type or control kind
is one module, registered, and the config flow renders from the registry's
schema. Nothing switches on a type key - `build_load()` asks the registry and
the type builds itself.

`device_types` is the type registry (`types/base.py`); `CONTROL_KINDS` is the
kind registry, which exists so the flow can offer the kinds a type declares and
so a snapshot can name the one a load is using.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .kinds.base import ControlKind
from .kinds.battery_mode import BatteryMode, BatteryModeCfg
from .kinds.mode import ModeCfg, ModeKind
from .kinds.modulate import Modulate, ModulateCfg
from .kinds.setpoint import Setpoint, SetpointCfg
from .kinds.switch import Switch, SwitchCfg
from .types import base as device_types

if TYPE_CHECKING:
    from .base import Load, LoadConfig
    from .stores.base import StoreModel

__all__ = ["CONTROL_KINDS", "KindEntry", "build_load", "control_kinds", "device_types"]


@dataclass(frozen=True, slots=True)
class KindEntry:
    """One registered control kind and the configuration it takes (D4 §4.2)."""

    key: str
    kind: Callable[..., ControlKind]
    config: Callable[..., Any]


#: The four kinds of v1, and WP7.7's `battery_mode` (D4 §5.9). `sg_ready` is v1.x
#: and lands as another row (D4 §10).
CONTROL_KINDS: Mapping[str, KindEntry] = {
    "battery_mode": KindEntry(key="battery_mode", kind=BatteryMode, config=BatteryModeCfg),
    "modulate": KindEntry(key="modulate", kind=Modulate, config=ModulateCfg),
    "setpoint": KindEntry(key="setpoint", kind=Setpoint, config=SetpointCfg),
    "mode": KindEntry(key="mode", kind=ModeKind, config=ModeCfg),
    "switch": KindEntry(key="switch", kind=Switch, config=SwitchCfg),
}


def control_kinds() -> tuple[str, ...]:
    """Every registered control-kind key, sorted."""
    return tuple(sorted(CONTROL_KINDS))


def build_load(cfg: LoadConfig, store: StoreModel | None = None) -> Load:
    """Build the runtime `Load` for `cfg` through its registered type (D4 §4.6).

    This is what D7 calls per subentry at setup, and what a test calls instead of
    assembling a kind by hand.
    """
    return device_types.get(cfg.type_key).build(cfg, store)
