"""The review step's explanation (INV-67, D8 §5.1; review HUB-14).

No flow ends on a bare "Success". The last step states, in the user's language,
what PowerPlan derived and what it will do first - which is also the cheapest
place to catch a wrong answer, because a household recognises "about 25 kW" and
"the average of your three highest hours" long before it recognises `it_230` or
`mean_top_n`.

**How it stays translatable.** The frame is `config.step.review.description` in
`strings.json`, so every sentence is translated; this module fills in *data*
only - numbers formatted for the language, the household's own names for its
sensors and persons, and the labels the flow's own selectors already translate
(`selector.voltage_system`, `.price_source`, `.modifier`, `.presence_mode`, the
notification categories), assembled by `flow/text.py` (D8 §5.15 R1, H7). No
entity id, registry key or service name fills a placeholder (R2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.powerplan.const import (
    CONF_ELECTRICAL,
    CONF_HARD_LIMITS,
    CONF_METER,
    CONF_NAME,
    CONF_NOTIFICATIONS,
    CONF_PRESENCE,
    CONF_PRICES,
    CONF_QUIET_HOURS,
    CONF_TIMEZONE,
    CONF_TIMEZONE_SOURCE,
    PROMINENT_CATEGORIES,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
    TRANSPORT_OFF,
)
from custom_components.powerplan.providers.forecasts.base import detect_weather_entity

from .text import Text, entity_name

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

__all__ = ["placeholders"]


def _connection(text: Text, electrical: Mapping[str, Any]) -> str:
    """`63 A · Tre faser · 230 V IT (uten nøytralleder)`: the answers, in words."""
    fuse = f"{text.number(float(electrical['main_fuse_a']))} A"
    phases = text.word("phases", str(electrical["phases"]))
    system = text.word("review", f"system_{electrical['system']}")
    return " · ".join(part for part in (fuse, phases, system) if part)


def _meter(hass: HomeAssistant, text: Text, meter: Mapping[str, Any] | None, absent: str) -> str:
    """Name the power and register sensors, and count the roles besides."""
    if not meter or not meter.get("roles"):
        return absent
    roles = meter["roles"]
    named = [
        entity_name(hass, roles[role])
        for role in (ROLE_GRID_POWER, ROLE_IMPORT_REGISTER)
        if roles.get(role)
    ]
    extra = len(roles) - len(named)
    names = text.join(named) or absent
    return text.word("text", "more", names=names, count=extra) if extra else names


def _prices(text: Text, prices: Mapping[str, Any] | None, absent: str) -> str:
    """Name the price source (with its area), each add-on and the export by their labels."""
    if prices is None:
        return absent
    parts: list[str] = []
    for source in prices["sources"]:
        label = text.word("price_source", str(source["key"]))
        area = (source.get("options") or {}).get("area")
        parts.append(f"{label} {area}" if area else label)
    parts.extend(text.word("modifier", str(modifier["key"])) for modifier in prices["modifiers"])
    export = prices.get("export")
    if export is not None and export.get("mode"):
        mode = text.word("export_mode", str(export["mode"]))
        parts.append(text.word("review", "export", mode=mode))
    return ", ".join(part for part in parts if part) or absent


def _forecasts(hass: HomeAssistant, text: Text, meter: Mapping[str, Any] | None) -> str:
    """D10 §6: nothing to ask, so the review says what was found.

    The state read INV-3 restricts to `runtime.py` and `providers/` happens
    inside `detect_weather_entity` itself - reached here through
    `providers/forecasts/base.py`, the same way `flow/load.py` reaches
    `providers/profiles` rather than reading live state directly.
    """
    entity_id = detect_weather_entity(hass)
    weather = (
        text.word("review", "weather_found", weather=entity_name(hass, entity_id))
        if entity_id
        else text.word("review", "weather_none")
    )
    learned = bool(meter) and ROLE_IMPORT_REGISTER in (meter or {}).get("roles", {})
    usage = text.word("review", "usage_meter" if learned else "usage_none")
    return f"{weather} {usage}"


def _presence(hass: HomeAssistant, text: Text, presence: Mapping[str, Any]) -> str:
    """Name the persons it follows - or the manual choice, in its own words."""
    people = presence.get("persons") or []
    if presence.get("mode") == "manual" or not people:
        return text.word("presence_mode", "manual")
    return text.join(entity_name(hass, person) for person in people)


def _notifications(text: Text, policy: Mapping[str, Any], quiet: tuple[str, str]) -> str:
    """Name the categories that reach the household, and the quiet hours."""
    labels: list[str] = []
    for category, config in policy.items():
        if config["transport"] == TRANSPORT_OFF:
            continue
        where = (
            "config.step.notifications.data"
            if category in PROMINENT_CATEGORIES
            else "config.step.notifications.sections.advanced.data"
        )
        labels.append(text.string(f"{where}.{category}") or category.replace("_", " "))
    listed = text.join(labels) if labels else text.word("review", "notifications_none")
    hours = f"{quiet[0][:5]}–{quiet[1][:5]}"
    return f"{listed} · {text.word('review', 'quiet', hours=hours)}"


async def placeholders(
    hass: HomeAssistant, data: Mapping[str, Any], *, tariff: str | None
) -> dict[str, str]:
    """Return the values the review's translated frame is filled with (INV-67).

    `tariff` is the grid tariff's line as the flow already knows it - the grid
    company and the target in words (`flow/text.py`) - or `None` without one.
    """
    text = await Text.load(hass)
    absent = text.word("review", "absent")
    electrical = data[CONF_ELECTRICAL]
    derived = electrical["derived"]
    quiet = tuple(data[CONF_QUIET_HOURS])
    hard_limits = data.get(CONF_HARD_LIMITS) or {}
    contracted = hard_limits.get("contracted_kw")
    return {
        "name": data[CONF_NAME],
        "connection": _connection(text, electrical),
        "fuse_kw": text.number(derived["fuse_w"] / 1000.0, 1),
        "plausible_kw": text.number(derived["plausible_w"][1] / 1000.0, 1),
        "hard_limit_kw": absent if contracted is None else text.number(float(contracted)),
        "meter": _meter(hass, text, data[CONF_METER], absent),
        "prices": _prices(text, data[CONF_PRICES], absent),
        "tariff": tariff or absent,
        "forecasts": _forecasts(hass, text, data[CONF_METER]),
        "presence": _presence(hass, text, data[CONF_PRESENCE]),
        "notifications": _notifications(text, data[CONF_NOTIFICATIONS], (quiet[0], quiet[1])),
        "timezone": data[CONF_TIMEZONE],
        "timezone_source": text.word("timezone_source", data[CONF_TIMEZONE_SOURCE]),
    }
