"""Questions in plain language, and the derivation behind them (D4 §4.6, HLD §7.9).

Describe, don't configure: the questions are about the physical thing - the room,
the covering, the area, the battery's kilowatt-hours - and never about our model.
`derive()` turns the answers into parameters, `explain()` reads back what was
decided before it is saved (INV-67), and `materialise()` writes both into the
subentry with the `derivation_version` that produced them (INV-66).

INV-66 is the one with teeth. A release that improves a default must not silently
change a running house: the load is built from what was materialised, and the
only way a new table reaches it is the user asking for a re-derive - which shows
them the diff first.

`Answers` is a **boundary**: unknown keys, options that are not
offered and numbers outside their range are refused here, with the key named, so
nothing below this line has to check them again.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import time
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .types.base import DeviceType

__all__ = [
    "AnswerError",
    "Answers",
    "Bounds",
    "Derived",
    "Explanation",
    "Option",
    "QCtx",
    "Question",
    "QuestionKind",
    "Questionnaire",
    "explain",
    "materialise",
    "rederive",
]


class AnswerError(ValueError):
    """One answer is not usable, and which one (D4 §4.6).

    A `ValueError` because `core/` raises no Home Assistant exception (INV-2);
    D8 maps `key` and `code` to a translation key on the flow's form.
    """

    def __init__(self, key: str, code: str, detail: str) -> None:
        """Name the offending question, the reason, and what was wrong with it."""
        super().__init__(f"{key}: {detail}")
        self.key = key
        self.code = code


class QuestionKind(StrEnum):
    """What a question asks for, so D8 can pick a selector (D4 §4.6).

    `CURVE` is the seventh, added with the heat pump (`design/DECISIONS.md`
    D-0201): D4 §6.4 requires the COP curve to be **shown and editable**, and a
    curve of `outdoor °C → COP` is neither a number nor a choice. It is
    validated here like everything else at this boundary - a COP of zero is not
    a slow heat pump, it is a typo.
    """

    CHOICE = "choice"
    NUMBER = "number"
    BOOL = "bool"
    TIME = "time"
    ENTITY = "entity"
    WEEKLY_TIME = "weekly_time"
    CURVE = "curve"


@dataclass(frozen=True, slots=True)
class QCtx:
    """What the flow already knows before anybody answers (HLD §7.9 point 2).

    Every default that can be taken from the house is: the HA area prefills the
    room, the site's phase count prefills the charger's, and the capabilities the
    profile detected decide whether a floor loop is steered by mode or by
    setpoint.
    """

    area: str | None = None
    device_name: str | None = None
    phases: int | None = None
    capabilities: frozenset[str] = frozenset()
    readable: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Option:
    """One choice of a `CHOICE` question (D4 §4.6)."""

    value: str
    label_key: str = ""


@dataclass(frozen=True, slots=True)
class Question:
    """One question, and where its default comes from (INV-65).

    `derived_default` marks the sliders D4 §6 pre-fills "from the above": their
    default is a function of the *other* answers, so `derive()` supplies it and
    the flow shows the number it arrived at. Every other question's default is
    resolvable from the context alone, which is what makes Advanced optional.
    """

    key: str
    kind: QuestionKind
    options: tuple[Option, ...] = ()
    default: Callable[[QCtx], Any] | Any = None
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    help_key: str = ""
    advanced: bool = False
    derived_default: bool = False
    #: The `BOOL` question this one follows: asked only when that answer is
    #: true, and left at its default otherwise (D8 §5.15 rule 5, review
    #: HUB-17 - "preheat off never sees its temperature").
    asked_if: str | None = None
    #: A capability the device must have for this question to be asked at all:
    #: `cool` for a heat pump's cooling mode. Left at its default otherwise.
    needs: str | None = None
    #: A capability that makes this question moot: `output_only` for a battery's
    #: grid charging, which a battery that cannot take a charge never does.
    unless: str | None = None

    def default_for(self, ctx: QCtx) -> Any:
        """Resolve the default, calling it when it is a function of the context."""
        return self.default(ctx) if callable(self.default) else self.default


@dataclass(frozen=True, slots=True)
class Answers:
    """Validated answers - what `derive()` is allowed to read (D4 §4.6)."""

    values: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        """Return the answer to `key`."""
        return self.values[key]

    def get(self, key: str, fallback: Any = None) -> Any:
        """Return the answer to `key`, or `fallback`."""
        return self.values.get(key, fallback)

    def number(self, key: str) -> float:
        """Return the answer to `key` as a float."""
        return float(self.values[key])

    def choice(self, key: str) -> str:
        """Return the answer to `key` as an option value."""
        return str(self.values[key])

    def flag(self, key: str) -> bool:
        """Return the answer to `key` as a boolean."""
        return bool(self.values[key])

    def as_json(self) -> dict[str, Any]:
        """Return the answers as a subentry stores them (JSON scalars only)."""
        return {key: _jsonable(value) for key, value in sorted(self.values.items())}


def _jsonable(value: Any) -> Any:
    """Narrow an answer to something a `Store` can hold.

    A multi-entity question's untouched default (`never_switch`,
    `arrival_sources`) is a tuple, and JSON has no tuple - round-tripped
    through a `Store` it comes back a list, so it is turned into one here too,
    or a subentry read straight after being written would already differ from
    itself.
    """
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class Bounds:
    """`low ≤ value ≤ high` across three answers - the comfort between its limits (D4 §6).

    Checked on what the household answered; a derived neighbour is the type's
    own table, not an answer to argue with (review CTL-16, D-0393). The error
    lands on `value`, the field the household moves.
    """

    value: str
    low: str | None = None
    high: str | None = None


@dataclass(frozen=True, slots=True)
class Questionnaire:
    """A device type's questions, and the validation at its boundary (D4 §4.6)."""

    questions: tuple[Question, ...]
    bounds: tuple[Bounds, ...] = ()

    def get(self, key: str) -> Question:
        """Return the question `key`, raising `KeyError` when there is none."""
        for question in self.questions:
            if question.key == key:
                return question
        raise KeyError(key)

    def keys(self) -> tuple[str, ...]:
        """Every question key, in the order they are asked."""
        return tuple(question.key for question in self.questions)

    def defaults(self, ctx: QCtx) -> dict[str, Any]:
        """Every default, resolved against `ctx`."""
        return {question.key: question.default_for(ctx) for question in self.questions}

    def validate(self, raw: Mapping[str, Any], ctx: QCtx) -> Answers:
        """Fill in the defaults and refuse anything that does not belong.

        Refuse, not clamp: a number outside its range means the user and this
        table disagree about the device, and silently moving it is how a load
        ends up configured for a thing nobody owns.
        """
        known = {question.key for question in self.questions}
        for key in raw:
            if key not in known:
                raise AnswerError(key, "unknown", "no such question")

        values: dict[str, Any] = {}
        for question in self.questions:
            if question.key in raw:
                values[question.key] = _coerce(question, raw[question.key])
            else:
                values[question.key] = question.default_for(ctx)
        for bounds in self.bounds:
            _check_bounds(bounds, raw, values)
        return Answers(values=values)


def _check_bounds(bounds: Bounds, raw: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    """Refuse a comfort outside its own limits, where the household answered both (CTL-16)."""
    value = values.get(bounds.value) if raw.get(bounds.value) is not None else None
    if value is None:
        return
    low = values.get(bounds.low) if bounds.low and raw.get(bounds.low) is not None else None
    high = values.get(bounds.high) if bounds.high and raw.get(bounds.high) is not None else None
    if (low is not None and value < low) or (high is not None and value > high):
        raise AnswerError(bounds.value, "outside_bounds", f"{value} is not within {low}–{high}")


def _coerce(question: Question, value: Any) -> Any:  # noqa: PLR0911 - one return per kind
    """Check one answer against its question, returning it in its own type."""
    if value is None:
        return None
    match question.kind:
        case QuestionKind.NUMBER:
            try:
                number = float(value)
            except TypeError, ValueError:
                raise AnswerError(
                    question.key, "not_a_number", f"{value!r} is not a number"
                ) from None
            if question.min is not None and number < question.min:
                raise AnswerError(question.key, "too_small", f"{number} is under {question.min}")
            if question.max is not None and number > question.max:
                raise AnswerError(question.key, "too_large", f"{number} is over {question.max}")
            return number
        case QuestionKind.CHOICE:
            allowed = {option.value for option in question.options}
            if str(value) not in allowed:
                raise AnswerError(
                    question.key, "not_an_option", f"{value!r} is not one of {sorted(allowed)}"
                )
            return str(value)
        case QuestionKind.BOOL:
            return bool(value)
        case QuestionKind.TIME:
            return _as_time(question.key, value)
        case QuestionKind.CURVE:
            return _as_curve(question.key, value)
        case _:
            return value


def _as_curve(key: str, value: Any) -> Mapping[float, float]:
    """Accept `{x: y}` of numbers - a COP curve against outdoor temperature.

    Sorted on the way in, so the stored answer and the golden file read in the
    order a human would draw them, and refused rather than repaired: a curve
    with a zero in it describes no machine.
    """
    if not isinstance(value, Mapping) or not value:
        raise AnswerError(key, "not_a_curve", f"{value!r} is not a curve of x → y points")
    points: dict[float, float] = {}
    for raw_x, raw_y in value.items():
        try:
            x, y = float(raw_x), float(raw_y)
        except TypeError, ValueError:
            raise AnswerError(
                key, "not_a_curve", f"{raw_x!r}: {raw_y!r} is not a point on a curve"
            ) from None
        if y <= 0.0:
            raise AnswerError(key, "not_a_curve", f"{y} is not a physical efficiency at {x}")
        points[x] = y
    ordered = dict(sorted(points.items()))
    if any(later < earlier for earlier, later in pairwise(ordered.values())):
        # A heat pump's COP rises with the outdoor temperature (D4 §6.4); a curve
        # that falls is two points typed the wrong way round (review CTL-13, 16).
        raise AnswerError(key, "curve_not_rising", f"{ordered} falls as it gets warmer")
    return ordered


def _as_time(key: str, value: Any) -> time:
    """Accept `time` or `"HH:MM"` for a time answer."""
    if isinstance(value, time):
        return value
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        return time(hour=hour, minute=minute)
    except TypeError, ValueError:
        raise AnswerError(key, "not_a_time", f"{value!r} is not a time of day") from None


@dataclass(frozen=True, slots=True)
class Derived:
    """What the answers imply (D4 §4.6).

    `params` holds JSON scalars only: it goes straight into the subentry, and a
    `SlabStore` is rebuilt from `store`, `area_m2` and `screed_mm` rather than
    pickled into it.
    """

    params: Mapping[str, Any]
    strategy: str
    strategy_params: Mapping[str, Any]
    priority: int
    group: str | None
    explanation_key: str
    explanation_params: Mapping[str, Any]
    derivation_version: int


@dataclass(frozen=True, slots=True)
class Explanation:
    """The review step's read-back, as a translation key and its values (INV-67).

    A key rather than a sentence: `core/` has no language (HLD §7.6), and D8
    renders the same key in `en` and `nb`.
    """

    key: str
    params: Mapping[str, Any]


@runtime_checkable
class Derivation(Protocol):
    """The half of `DeviceType` that `rederive()` needs (D4 §4.6)."""

    def derive(self, answers: Answers, ctx: QCtx) -> Derived:
        """Turn answers into parameters."""
        ...


def materialise(type_key: str, answers: Answers, derived: Derived) -> dict[str, Any]:
    """Return the subentry's data: answers, derived values, and the version (INV-66)."""
    return {
        "type": type_key,
        "derivation_version": derived.derivation_version,
        "answers": answers.as_json(),
        "params": {key: _jsonable(value) for key, value in sorted(derived.params.items())},
        "strategy": derived.strategy,
        "strategy_params": {
            key: _jsonable(value) for key, value in sorted(derived.strategy_params.items())
        },
        "priority": derived.priority,
        "group": derived.group,
    }


def rederive(
    data: Mapping[str, Any], device_type: DeviceType | Derivation, ctx: QCtx
) -> tuple[Derived, dict[str, tuple[Any, Any]]]:
    """Re-run the derivation on the stored answers and diff it (INV-66).

    The stored answers, not the stored parameters: re-deriving is the user asking
    "what would you decide about my bathroom *today*", and the answer is only
    interesting where it differs.
    """
    answers = Answers(values=dict(data.get("answers", {})))
    fresh = device_type.derive(answers, ctx)
    stored = dict(data.get("params", {}))
    diff: dict[str, tuple[Any, Any]] = {}
    for key in sorted(set(stored) | set(fresh.params)):
        old, new = stored.get(key), fresh.params.get(key)
        if old != new:
            diff[key] = (old, new)
    return fresh, diff


def explain(answers: Answers, derived: Derived) -> Explanation:
    """Return what the review step shows before anything is saved (INV-67).

    The parameters were folded into `explanation_params` by `derive()`, which is
    the only place that knows which of them are worth a sentence. `answers` is
    taken for the signature D4 §4.6 gives and for a type that wants to phrase
    the read-back from an answer the derivation dropped.
    """
    return Explanation(key=derived.explanation_key, params=dict(derived.explanation_params))
