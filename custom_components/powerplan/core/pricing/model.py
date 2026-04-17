"""D1's types (D1 §3, §4).

`Slot`, `PriceCurve`, `Carrier`, `Direction` and `Confidence` live at the core
root: HLD §5 puts the shared vocabulary in `core/model.py` so D2 and D11 can
price a bill without importing D1. They are re-exported here so a reader of
D1 §4 finds them where the LLD says they are. D1's own types are `RawSlot` -
the pre-composition row the store holds - and the registry `Schema` the config
flow renders from (D1 §6).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ..model import Carrier, Confidence, Direction, Money, PriceCurve, Slot

__all__ = [
    "Carrier",
    "Confidence",
    "Direction",
    "Field",
    "FieldKind",
    "Money",
    "PriceCurve",
    "RawSlot",
    "Schema",
    "Slot",
]


@dataclass(frozen=True, slots=True)
class RawSlot:
    """One slot exactly as a source published it, normalised (D1 §4, §5.2).

    `value` is in major currency units per kWh and MAY be negative (INV-51).
    `fetched_at` is what decides `STALE` during composition, which is why the
    raw row carries it and `Slot` does not.
    """

    start: datetime
    end: datetime
    value: Decimal
    currency: str
    source: str
    fetched_at: datetime


class FieldKind(StrEnum):
    """What one registry option is, so D8 can pick a selector (D1 §6)."""

    MONEY = "money"
    NUMBER = "number"
    BOOL = "bool"
    TEXT = "text"
    TIME = "time"
    SELECT = "select"
    LIST = "list"
    ENTITY = "entity"
    """One Home Assistant entity id (`design/DECISIONS.md` D-0087, D-0121).

    The member D-0087 left owing: D8 renders it as an `EntitySelector`, so a
    schema that names an entity no longer has to call it text. It is the only
    kind WP1.3 added - a device, an area and a timezone are asked for by a step
    the flow writes by hand, not by any registry's schema, so those kinds would
    have had no schema to render.
    """


@dataclass(frozen=True, slots=True)
class Field:
    """One option of a registered modifier or forecaster (D1 §6).

    A registry entry carries its own schema so the price step of the config
    flow renders from the registry and nothing switches on a key. D4's
    `Question` is the same idea for device questionnaires; a shared type waits
    for the WP that needs both (`design/DECISIONS.md` D-0037).
    """

    key: str
    kind: FieldKind
    default: Any = None
    required: bool = False
    unit: str | None = None
    options: tuple[str, ...] = ()
    advanced: bool = False


type Schema = tuple[Field, ...]
