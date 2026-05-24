"""The state codec every domain's persisted section goes through (D7 §7).

Frozen dataclasses of primitives, ISO-8601 datetimes, `StrEnum`s, `Decimal`s
and tuples of those - which is what every domain promised its state would be
(D2 §7, D3 §4, D4 §7, D6 §7, D11 §7). A type that carries its own `as_dict` /
`from_dict` owns its shape and is asked for it. Lived in `engine.py` until
WP0.10 needed it for D11's section too (`design/DECISIONS.md` D-0267).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import UnionType
from typing import Any, Final, Literal, Union, get_args, get_origin, get_type_hints

__all__ = ["decode", "encode"]

_LOGGER = logging.getLogger(__name__)

#: A `Mapping[K, V]` annotation carries exactly two type arguments.
_KEY_VALUE: Final = 2

#: Below this many watts a grant is nothing (D6 §5.3).
_EPS_W: Final = 1e-6


def encode(value: Any) -> Any:  # noqa: PLR0911 - one branch per JSON-able shape (D7 §7)
    """Return `value` as JSON-able data (D7 §7).

    Frozen dataclasses of primitives, ISO-8601 datetimes, `StrEnum`s, `Decimal`s
    and tuples of those - which is what every domain promised its state would be
    (D2 §7, D3 §4, D4 §7, D6 §7). A type that carries its own `as_dict` owns its
    shape and is asked for it.
    """
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict) and is_dataclass(value):
        return as_dict()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {encode(key): encode(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [encode(item) for item in value]
    raise TypeError(f"{type(value).__name__} is not persistable state (D7 §7)")


def decode(kind: Any, raw: Any) -> Any:  # noqa: PLR0911, PLR0912 - one branch per JSON-able shape (D7 §7)
    """Return the value `_encode` wrote, rebuilt as `kind` (D7 §7)."""
    kind = _unalias(kind)
    if raw is None or kind is Any:
        return raw
    origin = get_origin(kind)
    if origin in (UnionType, Union):
        return _decode_union(kind, raw)
    if origin is Literal:
        return raw
    if origin is tuple:
        args = get_args(kind)
        if len(args) == _KEY_VALUE and args[1] is Ellipsis:
            return tuple(decode(args[0], item) for item in raw)
        return tuple(decode(arg, item) for arg, item in zip(args, raw, strict=True))
    if origin in (list, set, frozenset):
        (arg,) = get_args(kind) or (Any,)
        return (origin or list)(decode(arg, item) for item in raw)
    if origin is not None and issubclass(_as_type(origin), Mapping):
        key_kind, value_kind = get_args(kind) or (Any, Any)
        out: dict[Any, Any] = {}
        for key, item in raw.items():
            try:
                out[decode(key_kind, key)] = decode(value_kind, item)
            except (TypeError, ValueError, KeyError, AttributeError) as err:
                # A stale or malformed entry from an earlier schema - dropped,
                # not fatal: the caller's own default (a fresh LoadState(),
                # an empty section) is what a load with no persisted state
                # already means (D7 §7). One bad key must never cost the
                # whole section, or the whole site.
                _LOGGER.warning(
                    "state section: %r's entry %r could not be decoded as %s (%s) — dropped",
                    key,
                    item,
                    value_kind,
                    err,
                )
        return out
    if not isinstance(kind, type):
        return raw
    if issubclass(kind, Enum):
        return kind(raw)
    if issubclass(kind, datetime):
        return datetime.fromisoformat(raw)
    if issubclass(kind, date):
        return date.fromisoformat(raw)
    if issubclass(kind, Decimal):
        return Decimal(raw)
    if is_dataclass(kind):
        from_dict = getattr(kind, "from_dict", None)
        if callable(from_dict):
            return from_dict(raw)
        hints = get_type_hints(kind)
        return kind(
            **{
                f.name: decode(hints[f.name], raw[f.name])
                for f in fields(kind)
                if f.init and f.name in raw
            }
        )
    if issubclass(kind, bool | int | float | str):
        return kind(raw)
    return raw


def _decode_union(kind: Any, raw: Any) -> Any:
    """Return `raw` decoded as the member of a union its JSON shape fits (D7 §7)."""
    args = [arg for arg in get_args(kind) if arg is not type(None)]
    if len(args) == 1:
        return decode(args[0], raw)
    for arg in args:
        candidate = _unalias(arg)
        if not isinstance(candidate, type):
            continue
        if isinstance(raw, dict) and is_dataclass(candidate):
            return decode(candidate, raw)
        if isinstance(raw, str) and issubclass(candidate, Enum):
            try:
                return candidate(raw)
            except ValueError:
                continue
        if isinstance(raw, bool) and candidate is bool:
            return raw
        if isinstance(raw, int | float) and candidate in (int, float) and not isinstance(raw, bool):
            return candidate(raw)
    return raw


def _unalias(kind: Any) -> Any:
    """Return what a PEP 695 `type X = …` alias stands for."""
    value = getattr(kind, "__value__", None)
    return kind if value is None else _unalias(value)


def _as_type(origin: Any) -> type:
    """Return `origin` as a class, for the `issubclass` questions above."""
    return origin if isinstance(origin, type) else type(origin)
