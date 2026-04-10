"""What a device *is*, and the registry that holds them (D4 §3, §4.6).

A `DeviceType` owns the physics and the vocabulary of one kind of thing: which
control kinds fit it, which strategies make sense for it, the plain-language
questionnaire that derives its parameters (HLD §7.9), and how its demand is
computed. It never decides a grant, and it never talks to a device - a profile
does that, a kind shapes the write, and the gate decides whether it goes.

Extension is by registry, not by conditional: a new device type is one module
here, registered, and the config flow renders from `questionnaire`. WP0.5 ships
`ev` and `floor_heating`; the other six of D4 §6 land in phases 3 and 5 without
the registry changing.
"""

from typing import TYPE_CHECKING, ClassVar, Protocol

from ..base import Load, LoadConfig, TypeLogic

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..questionnaire import Answers, Derived, QCtx, Questionnaire
    from ..stores.base import StoreModel

__all__ = ["DeviceType", "entries", "get", "keys", "register"]


class DeviceType(TypeLogic, Protocol):
    """One kind of physical thing (D4 §4.6)."""

    key: ClassVar[str]
    kinds: ClassVar[tuple[str, ...]]
    strategies: ClassVar[tuple[str, ...]]
    default_strategy: ClassVar[str]
    questionnaire: ClassVar[Questionnaire]

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Turn plain-language answers into parameters and an explanation."""
        ...

    def build(self, cfg: LoadConfig, store: StoreModel | None = None) -> Load:
        """Build the runtime `Load`: kind, store, gate configuration."""
        ...


_REGISTRY: dict[str, DeviceType] = {}


def register(device_type: DeviceType) -> DeviceType:
    """Register a device type under its own `key` (D4 §4.6)."""
    _REGISTRY[device_type.key] = device_type
    return device_type


def keys() -> tuple[str, ...]:
    """Every registered type key, sorted - what a subentry may choose from."""
    return tuple(sorted(_REGISTRY))


def get(key: str) -> DeviceType:
    """Return the registered type `key`."""
    return _REGISTRY[key]


def entries() -> Mapping[str, DeviceType]:
    """Every registered type, keyed - for the flow's type step and diagnostics."""
    return dict(_REGISTRY)
