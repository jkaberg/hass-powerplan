"""The review step's explanation (INV-67, D8 §5.1).

No flow ends on a bare "Success". The last step states, in the user's language,
what powerplan derived and what it will do first - which is also the cheapest
place to catch a wrong answer, because a household recognises "about 25 kW" and
"the average of your three highest hours" long before it recognises `it_230` or
`mean_top_n`.

**How it stays translatable.** The frame is `config.step.review.description` in
`strings.json`, so every sentence is translated; this module fills in *values*
only. The two fragments that are words rather than numbers - where the timezone
came from, and what an unconfigured section says - are read from the `selector`
section of the same translations, which is where a translated vocabulary already
lives. The tariff's plain-language sentence comes from D2's
`render_plain_language`, which is English for now (`design/DECISIONS.md` D-0128).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.translation import async_get_translations

from custom_components.powerplan.const import (
    CONF_CURRENCY,
    CONF_ELECTRICAL,
    CONF_HARD_LIMITS,
    CONF_METER,
    CONF_NAME,
    CONF_NOTIFICATIONS,
    CONF_PRESENCE,
    CONF_PRICES,
    CONF_QUIET_HOURS,
    CONF_TARIFF,
    CONF_TIMEZONE,
    CONF_TIMEZONE_SOURCE,
    DOMAIN,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
    TRANSPORT_OFF,
)
from custom_components.powerplan.core.metering.profile import VoltageSystem
from custom_components.powerplan.providers.forecasts.base import detect_weather_entity

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

__all__ = ["placeholders"]

#: How many conductors the *service* delivers on, for the connection sentence.
_THREE_PHASE = 3
_TWO_LEGS = 2

_SYSTEM_WORDS = {
    VoltageSystem.IT_230: "230 V IT (no neutral)",
    VoltageSystem.TN_400: "400 V TN",
    VoltageSystem.TT_400: "400 V TT",
    VoltageSystem.SPLIT_240: "120/240 V split phase",
    VoltageSystem.SINGLE_230: "230 V single phase",
    VoltageSystem.SINGLE_120: "120 V single phase",
}


async def _vocabulary(hass: HomeAssistant) -> dict[str, str]:
    """Return the translated fragments the review needs (never a bare key)."""
    return await async_get_translations(hass, hass.config.language, "selector", {DOMAIN})


def _fragment(vocabulary: Mapping[str, str], key: str, value: str, fallback: str) -> str:
    return vocabulary.get(f"component.{DOMAIN}.selector.{key}.options.{value}", fallback)


def _connection(electrical: Mapping[str, Any]) -> str:
    derived = electrical["derived"]
    system = VoltageSystem(electrical["system"])
    service = derived["service_phases"]
    phases = "three-phase" if service == _THREE_PHASE else "single-phase"
    if service == _TWO_LEGS:
        phases = "two legs"
    return (
        f"{electrical['main_fuse_a']:g} A, {phases} {_SYSTEM_WORDS[system]} — "
        f"about {derived['fuse_w'] / 1000:.1f} kW"
    )


def _meter(meter: Mapping[str, Any] | None, absent: str) -> str:
    if meter is None:
        return absent
    roles = meter["roles"]
    power = roles.get(ROLE_GRID_POWER, absent)
    register = roles.get(ROLE_IMPORT_REGISTER, absent)
    extra = len(roles) - len({ROLE_GRID_POWER, ROLE_IMPORT_REGISTER} & set(roles))
    return f"{power} · {register} (+{extra} more)"


def _prices(prices: Mapping[str, Any] | None, absent: str) -> str:
    if prices is None:
        return absent
    parts: list[str] = []
    for source in prices["sources"]:
        options = source["options"]
        area = options.get("area")
        parts.append(f"{source['key']} {area}" if area else str(source["key"]))
    parts.extend(str(modifier["key"]) for modifier in prices["modifiers"])
    export = prices.get("export")
    if export is not None:
        parts.append(f"export {export['mode']}")
    return " · ".join(parts) if parts else absent


def _tariff(tariff: Mapping[str, Any] | None, absent: str) -> str:
    if tariff is None:
        return absent
    description = tariff.get("description")
    return str(description) if description else absent


def _forecasts(hass: HomeAssistant, meter: Mapping[str, Any] | None, absent: str) -> str:
    """D10 §6: nothing to ask, so the review says what was auto-detected.

    The state read INV-3 restricts to `runtime.py` and `providers/` happens
    inside `detect_weather_entity` itself - reached here through
    `providers/forecasts/base.py`, the same way `flow/load.py` reaches
    `providers/profiles` rather than reading live state directly.
    """
    entity_id = detect_weather_entity(hass)
    weather_text = f"found {entity_id}" if entity_id else absent
    if meter is None or ROLE_IMPORT_REGISTER not in meter["roles"]:
        return f"{weather_text} for temperature; no meter bound, so no usage profile to learn"
    return (
        f"{weather_text} for temperature; it will learn your household's usual load "
        "per hour of the week from the meter, offered after about two weeks"
    )


def _presence(presence: Mapping[str, Any], absent: str) -> str:
    people = presence.get("persons") or []
    if presence["mode"] == "manual" or not people:
        return absent
    return ", ".join(people)


def _notifications(policy: Mapping[str, Any], quiet: tuple[str, str]) -> str:
    on = [category for category, config in policy.items() if config["transport"] != TRANSPORT_OFF]
    listed = ", ".join(sorted(on)) if on else "—"
    return f"{listed} · quiet {quiet[0][:5]}–{quiet[1][:5]}"


async def placeholders(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, str]:
    """Return the values the review's translated frame is filled with (INV-67)."""
    vocabulary = await _vocabulary(hass)
    absent = _fragment(vocabulary, "review", "absent", "not configured")
    electrical = data[CONF_ELECTRICAL]
    derived = electrical["derived"]
    quiet = tuple(data[CONF_QUIET_HOURS])
    return {
        "name": data[CONF_NAME],
        "connection": _connection(electrical),
        "plausible_kw": f"{derived['plausible_w'][1] / 1000:.1f}",
        "hard_limit_kw": f"{data[CONF_HARD_LIMITS]['contracted_kw']:g}",
        "meter": _meter(data[CONF_METER], absent),
        "prices": _prices(data[CONF_PRICES], absent),
        "currency": data[CONF_CURRENCY],
        "tariff": _tariff(data[CONF_TARIFF], absent),
        "forecasts": _forecasts(hass, data[CONF_METER], absent),
        "presence": _presence(data[CONF_PRESENCE], absent),
        "notifications": _notifications(data[CONF_NOTIFICATIONS], (quiet[0], quiet[1])),
        "timezone": data[CONF_TIMEZONE],
        "timezone_source": _fragment(
            vocabulary,
            "timezone_source",
            data[CONF_TIMEZONE_SOURCE],
            "from your Home Assistant settings",
        ),
    }
