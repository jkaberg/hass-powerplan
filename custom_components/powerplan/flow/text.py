"""Numbers, money and assembled labels in the system language (D8 §3, §5.15).

Every word a screen shows is a translation string; Python fills placeholders with
data only - numbers, units, money and the household's own names - formatted by
this module for one language: "1 200 kr", "0,79", "25,1 kW" in `nb`; "1,200 kr",
"0.79", "25.1 kW" in `en` (review R1). Where a label mixes words and numbers - a
target option, the tariff table, a list joined with "og" - the words are a
translation from the `selector` vocabularies (`selector.text`, `.tariff_text`,
`.review`, `.load_text`) and this module assembles them.

A flow is given no user language (D8 §5.15 H7), so the language is Home
Assistant's own, `hass.config.language`, read the way `flow/review.py` always
has; a language this integration ships no file for falls back to English words
and English numbers together, as Home Assistant's own translation fallback does.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectOptionDict
from homeassistant.helpers.translation import async_get_translations

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.tariffs.grammar import HolidayMode, StepTable

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.tariffs.grammar import TariffVersion, TimeFilter
    from custom_components.powerplan.core.tariffs.presets.loader import TariffSummary

__all__ = [
    "LANGUAGES",
    "PRESET_CUSTOM",
    "PRESET_UNKNOWN",
    "Text",
    "band_range",
    "device_name",
    "entity_name",
    "preset_options",
    "target_label",
    "target_options",
    "tariff_table",
]

#: The translation files this integration ships (D8 §5.11).
LANGUAGES: Final = ("en", "nb")
#: (thousands separator, decimal mark) per language; `nb` groups with a no-break space.
_SEPARATORS: Final = {"en": (",", "."), "nb": ("\u00a0", ",")}
#: The translation categories the assembled labels read from.
_CATEGORIES: Final = ("selector", "config", "config_subentries")
#: A currency's symbol and its minor unit (D8 §5.15 controls: øre, öre, cent).
#: A currency not listed is written with its code, which is data.
_CURRENCIES: Final = {
    "NOK": ("kr", "øre"),
    "DKK": ("kr", "øre"),
    "SEK": ("kr", "öre"),
    "EUR": ("€", "cent"),
    "USD": ("$", "¢"),
    "GBP": ("£", "p"),
    "AUD": ("A$", "c"),
}
#: Norwegian ends its alphabet Æ Ø Å, which code-point order (Å Æ Ø) does not:
#: the three letters, and the Swedish and German forms that sort with them, are
#: moved past `z` before the accents are stripped (review HUB-11).
_NORWEGIAN_TAIL: Final = {"æ": "{", "ä": "{", "ø": "|", "ö": "|", "å": "}"}
#: Where a preset's own escape hatches sit in the grid-company select (D2 §6).
PRESET_UNKNOWN: Final = "unknown"
PRESET_CUSTOM: Final = "custom"
_DAYS_IN_WEEK: Final = 7
_MONTHS_IN_YEAR: Final = 12
_RUN: Final = 3
_MINUTES_PER_HOUR: Final = 60


class Text:
    """One language's words (from the translations) and its number formats."""

    def __init__(self, language: str, strings: Mapping[str, str]) -> None:
        """Bind a language and the flat translations Home Assistant returns for it."""
        self.language = language if language in LANGUAGES else "en"
        self._strings = strings

    @classmethod
    async def load(cls, hass: HomeAssistant) -> Text:
        """Read the words in the system language (D8 §5.15 H7)."""
        language = hass.config.language
        strings: dict[str, str] = {}
        for category in _CATEGORIES:
            strings.update(await async_get_translations(hass, language, category, {DOMAIN}))
        return cls(language, strings)

    # ---------------------------------------------------------------- words

    def string(self, path: str) -> str:
        """Return the translation at `path` below `component.powerplan.`, or ``""``."""
        return self._strings.get(f"component.{DOMAIN}.{path}", "")

    def word(self, group: str, key: str, **values: Any) -> str:
        """Return `selector.<group>.options.<key>`, its placeholders filled with data."""
        template = self.string(f"selector.{group}.options.{key}")
        return template.format(**values) if values else template

    def yes_no(self, flag: bool) -> str:
        """Return the language's own yes or no."""
        return self.word("text", "yes" if flag else "no")

    def join(self, items: Iterable[str]) -> str:
        """Join names as `a`, `a og b`, `a, b og c` - the language's own conjunction."""
        names = list(items)
        if len(names) <= 1:
            return "".join(names)
        return f"{', '.join(names[:-1])} {self.word('text', 'and')} {names[-1]}"

    def sort_key(self, value: str) -> str:
        """Return a key that sorts `value` in this language's alphabet (Æ Ø Å last in `nb`)."""
        folded = value.casefold()
        if self.language == "nb":
            folded = "".join(_NORWEGIAN_TAIL.get(char, char) for char in folded)
        decomposed = unicodedata.normalize("NFKD", folded)
        return "".join(char for char in decomposed if not unicodedata.combining(char))

    # -------------------------------------------------------------- numbers

    def number(self, value: float | Decimal, decimals: int | None = None) -> str:
        """Return a number in this language: at most two decimals unless told otherwise."""
        if decimals is None:
            rendered = f"{value:,.2f}".rstrip("0").rstrip(".")
        else:
            rendered = f"{value:,.{decimals}f}"
        if rendered in ("-0", ""):
            rendered = "0"
        thousands, point = _SEPARATORS[self.language]
        return rendered.replace(",", "\0").replace(".", point).replace("\0", thousands)

    def kw(self, value: float) -> str:
        """Return a power in kW: `25,1 kW`."""
        return f"{self.number(value)} kW"

    def money(self, amount: Decimal | float, currency: str) -> str:
        """Return money in major units: `1 200 kr`, `53,39 €`; a price keeps its own scale."""
        value = Decimal(str(amount))
        symbol = _CURRENCIES.get(currency, (currency, currency))[0]
        return f"{self.number(value, _decimals(value))} {symbol}"

    def minor_per_kwh(self, amount: Decimal | float, currency: str) -> str:
        """Return a price per kWh in the currency's minor unit: `36,04 øre/kWh` (D1 §6)."""
        value = Decimal(str(amount))
        known = _CURRENCIES.get(currency)
        if known is None:
            return f"{self.number(value, _decimals(value))} {currency}/kWh"
        minor = value * 100
        return f"{self.number(minor, _decimals(minor))} {known[1]}/kWh"

    @staticmethod
    def clock(minute: int) -> str:
        """Return minutes from midnight as `HH:MM`; the end of the day is `24:00`."""
        return f"{minute // _MINUTES_PER_HOUR:02d}:{minute % _MINUTES_PER_HOUR:02d}"

    def span(self, start: int, end: int) -> str:
        """Return a span of the day, `06:00–22:00`."""
        return f"{self.clock(start)}–{self.clock(end)}"


def entity_name(hass: HomeAssistant, entity_id: str) -> str:
    """Return an entity's name as the household sees it - never its id (review R2).

    Read from the registries, not the state machine (INV-3): the entity's own
    name, prefixed by its device's where the entity is named after it. An entity
    the registry does not know is named from its object id in words.
    """
    fallback = entity_id.split(".", 1)[-1].replace("_", " ").capitalize()
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return fallback
    name = entry.name or entry.original_name
    if entry.has_entity_name and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        device_name = None if device is None else device.name_by_user or device.name
        if device_name:
            return f"{device_name} {name}" if name else device_name
    return name or fallback


def device_name(hass: HomeAssistant, device_id: str | None, fallback: str) -> str:
    """Return a device's name as the household set it, or `fallback`."""
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    if device is None:
        return fallback
    return device.name_by_user or device.name or fallback


def _decimals(value: Decimal) -> int:
    """Return the scale a price was written at: none for whole numbers, else at least two."""
    if value == value.to_integral_value():
        return 0
    exponent = value.normalize().as_tuple().exponent
    return max(2, -exponent) if isinstance(exponent, int) else 2


# --------------------------------------------------------------------------- #
# The grid company, the target (D2 §6; review HUB-11, HUB-13, ENT-2)
# --------------------------------------------------------------------------- #


def preset_options(text: Text, presets: Sequence[tuple[str, str]]) -> list[SelectOptionDict]:
    """Return the grid companies A–Å in the language's alphabet, the two escape hatches last.

    An operator's name is data (never translated); "not listed" and "enter it
    myself" are words, pinned after the list, which the frontend's own `sort`
    could not do (D2 §6, HUB-11).
    """
    named = sorted(presets, key=lambda row: text.sort_key(row[1]))
    options = [SelectOptionDict(value=stem, label=name) for stem, name in named]
    options.append(
        SelectOptionDict(value=PRESET_UNKNOWN, label=text.word("text", "preset_unknown"))
    )
    options.append(SelectOptionDict(value=PRESET_CUSTOM, label=text.word("text", "preset_custom")))
    return options


def band_range(text: Text, lower_kw: float, upper_kw: float | None) -> str:
    """Return a band of power: `2–5 kW`, or `Over 20 kW` for the open top."""
    if upper_kw is None:
        return text.word("tariff_text", "band_over", kw=text.kw(lower_kw))
    return f"{text.number(lower_kw)}–{text.kw(upper_kw)}"


def target_options(text: Text, version: TariffVersion) -> list[SelectOptionDict]:
    """`auto`, then `step_<i>` for each step with its range and fee (D2 §6, HUB-13).

    `step_<i>` and not `step:<i>`: an option of the site's target select is a
    translation key, which a colon is not (ENT-2; a stored `step:<i>` is read as
    `step_<i>`, D8 §9 22).
    """
    options = [SelectOptionDict(value="auto", label=text.word("text", "target_auto"))]
    peak = version.peak
    if peak is None or not isinstance(peak.pricing, StepTable):
        return options
    pricing = peak.pricing
    per = "per_year" if peak.price_period_unit == "year" else "per_month"
    for index, step in enumerate(pricing.steps):
        lower = 0.0 if index == 0 else pricing.upper_kw(index - 1)
        fee = step.fee_per_period
        options.append(
            SelectOptionDict(
                value=f"step_{index}",
                label=text.word(
                    "text",
                    "target_step",
                    n=index + 1,
                    range=band_range(text, lower, step.upper_kw),
                    fee=text.word("text", per, fee=text.money(fee.amount, fee.currency)),
                ),
            )
        )
    return options


def target_label(text: Text, version: TariffVersion | None, choice: str) -> str:
    """Return the label of one target choice, for the review (HUB-14)."""
    if version is not None:
        for option in target_options(text, version):
            if option["value"] == choice:
                return option["label"]
    return text.word("text", "target_auto")


# --------------------------------------------------------------------------- #
# The tariff table (D2 §6; review HUB-12)
# --------------------------------------------------------------------------- #


def tariff_table(text: Text, summary: TariffSummary) -> str:
    """Render D2's `TariffSummary` as the table a household checks against its bill.

    The metric in one sentence, the steps (or the price per kW) as a table, the
    windows that count and how much, the contracted power, and the grid's energy
    charge in the minor unit per kWh - every word a translation, every number
    formatted for the language.
    """
    parts: list[str] = []
    if not summary.peak:
        parts.append(text.word("tariff_text", "no_peak"))
    else:
        parts.append(_metric(text, summary))
        if summary.pricing == "linear":
            parts.append(_linear(text, summary))
        else:
            parts.append(_bands(text, summary))
        if summary.eligible is not None:
            parts.append(text.word("tariff_text", "eligible", when=_when(text, summary.eligible)))
        parts.extend(
            text.word(
                "tariff_text",
                "weight",
                when=_when(text, rule.when),
                weight=text.number(rule.weight),
            )
            for rule in summary.weights
        )
    if summary.contracted_kw:
        unit = "kVA" if summary.contracted_unit == "kva" else "kW"
        limits = text.join(f"{text.number(kw)} {unit}" for kw in summary.contracted_kw)
        key = "contracted_trip" if summary.trips else "contracted_surcharge"
        parts.append(text.word("tariff_text", key, limits=limits))
    if summary.energy:
        parts.append(text.word("tariff_text", "energy", rates=_rates(text, summary)))
    if summary.assumed:
        parts.append(text.word("tariff_text", "assumed"))
    return "\n\n".join(parts)


def _metric(text: Text, summary: TariffSummary) -> str:
    """Say which windows set the fee, in one sentence."""
    if summary.period == "rolling_months":
        period = text.word("tariff_text", "period_rolling", months=summary.rolling_months)
    else:
        period = text.word("tariff_text", f"period_{summary.period}")
    if summary.per_period == "max":
        return text.word("tariff_text", f"metric_max_{summary.window_min}", period=period)
    key = "metric_top_n_days" if summary.distinct_days else "metric_top_n"
    return text.word(
        "tariff_text",
        key,
        n=summary.n,
        windows=text.word("tariff_text", f"windows_{summary.window_min}"),
        period=period,
    )


def _bands(text: Text, summary: TariffSummary) -> str:
    """Render a step table (fee per period) or a tier table (price per kW) as markdown."""
    unit = summary.price_period_unit
    if summary.pricing == "steps":
        header = (
            text.word("tariff_text", "header_step"),
            text.word("tariff_text", f"header_fee_{unit}"),
        )
    else:
        header = (
            text.word("tariff_text", "header_power"),
            text.word("tariff_text", f"header_price_per_kw_{unit}"),
        )
    rows = [f"| {header[0]} | {header[1]} |", "|---|---:|"]
    rows.extend(
        f"| {band_range(text, band.lower_kw, band.upper_kw)} | "
        f"{text.money(band.price.amount, band.price.currency)} |"
        for band in summary.bands
    )
    return "\n".join(rows)


def _linear(text: Text, summary: TariffSummary) -> str:
    """Say the price per kW, with a free allowance and a floor where the tariff has them."""
    assert summary.price_per_kw is not None
    price = text.money(summary.price_per_kw.amount, summary.price_per_kw.currency)
    sentences = [text.word("tariff_text", f"linear_{summary.price_period_unit}", price=price)]
    if summary.free_kw:
        sentences.append(text.word("tariff_text", "linear_free", kw=text.kw(summary.free_kw)))
    if summary.min_kw:
        sentences.append(text.word("tariff_text", "linear_min", kw=text.kw(summary.min_kw)))
    return " ".join(sentences)


def _runs(values: Sequence[int], names: Sequence[str], length: int) -> str | None:
    """Name a set of weekdays or months: a run of three or more as `first–last`."""
    if not values or len(values) == length:
        return None
    ordered = sorted(values)
    if len(ordered) >= _RUN and ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"{names[ordered[0]]}–{names[ordered[-1]]}"
    return ", ".join(names[value] for value in ordered)


def _when(text: Text, filter_: TimeFilter) -> str:
    """Say which windows a filter matches: the hours, the days, the months, the holidays."""
    parts: list[str] = []
    if filter_.hours is not None:
        spans = text.join(text.span(start, end) for start, end in filter_.hours)
        parts.append(text.word("tariff_text", "when_hours", hours=spans))
    days = [text.word("tariff_text", f"weekday_{day}") for day in range(_DAYS_IN_WEEK)]
    if (named := _runs(filter_.weekdays or (), days, _DAYS_IN_WEEK)) is not None:
        parts.append(text.word("tariff_text", "when_days", days=named))
    months = [""] + [text.word("tariff_text", f"month_{month}") for month in range(1, 13)]
    if (named := _runs(filter_.months or (), months, _MONTHS_IN_YEAR)) is not None:
        parts.append(text.word("tariff_text", "when_months", months=named))
    if filter_.holidays is HolidayMode.EXCLUDE:
        parts.append(text.word("tariff_text", "when_no_holidays"))
    elif filter_.holidays is HolidayMode.AS_SUNDAY:
        parts.append(text.word("tariff_text", "when_holidays_sunday"))
    return " ".join(parts) or text.word("tariff_text", "when_always")


def _rates(text: Text, summary: TariffSummary) -> str:
    """List the grid's energy charge per period, in the minor unit per kWh."""
    rates: list[str] = []
    for rate in summary.energy:
        price = text.minor_per_kwh(rate.price, summary.currency)
        if rate.hours is None:
            rates.append(text.word("tariff_text", "energy_other", price=price))
        else:
            spans = text.join(text.span(start, end) for start, end in rate.hours)
            rates.append(text.word("tariff_text", "energy_hours", price=price, hours=spans))
    return ", ".join(rates)
