"""The site steps' schemas, defaults, validation and derivations (D8 §5.1).

`config_flow.py` owns the order of the steps and the state between them; this
module owns what each step *is*: the fields, the defaults taken from the Home
Assistant environment, the validation INV-49 puts in the schema rather than in a
`config.py`, and the numbers INV-66 makes the flow materialise.

Three rules run through all of it:

* **the environment answers first.** The country, the currency and the timezone
  come from `hass.config`; nothing here carries an IANA key, a currency code or a
  country as a literal default (D-0120).
* **every answer has a default** (HLD §7.9 (2)). A step whose defaults cannot be
  submitted as they stand is a bug, and `tests/flows/test_translations.py` walks
  the whole flow submitting nothing else.
* **advanced is never required** (INV-65). An advanced field is dropped from the
  form when the user is not in advanced mode, and the value it would have had is
  derived instead.
"""

from __future__ import annotations

import logging
import zoneinfo
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    ConfigEntrySelector,
    ConfigEntrySelectorConfig,
    CountrySelector,
    CountrySelectorConfig,
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from custom_components.powerplan.const import (
    METER_ROLES,
    NOTIFICATION_DEFAULTS,
    PROMINENT_CATEGORIES,
    QUIET_END_DEFAULT,
    QUIET_START_DEFAULT,
    ROLE_EXPORT_REGISTER,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
    ROLE_METER_WINDOW,
    ROLE_PHASE_L1,
    ROLE_PHASE_L2,
    ROLE_PHASE_L3,
    ROLE_PRODUCTION_POWER,
    SECTION_ADVANCED,
    TRANSPORTS,
)
from custom_components.powerplan.core.metering.profile import (
    PLAUSIBLE_FACTOR,
    ElectricalProfile,
    VoltageSystem,
)
from custom_components.powerplan.core.pricing import Carrier, modifiers
from custom_components.powerplan.core.pricing.modifiers.base import SPOT
from custom_components.powerplan.core.pricing.party import PARTY
from custom_components.powerplan.core.tariffs.household import Party
from custom_components.powerplan.core.tariffs.model import PeakTariff, StepTable
from custom_components.powerplan.core.tariffs.rules import loader
from custom_components.powerplan.core.tariffs.target import (
    CAP_MARGIN_KW,
    EPS_DEFAULT_KWH_PER_HOUR,
    EPS_MAX_KWH,
    RISK_FLAT,
    RISK_FREE_RIDE,
    RISK_FULL,
    default_risk,
)
from custom_components.powerplan.providers.meters.ha_sensors import value_now
from custom_components.powerplan.providers.prices.formats import registry as formats
from custom_components.powerplan.providers.prices.markets import NORDPOOL_MARKETS
from custom_components.powerplan.providers.prices.nordpool_action import (
    AREAS,
    NORDPOOL_DOMAIN,
    NordpoolActionSource,
)

from .questionnaire import (
    DONT_KNOW,
    MAIN_FUSE_SIZES,
    advanced_section,
    amps_of,
    as_duration,
    duration_selector,
    fuse_selector,
    percent_selector,
    price_selector,
    price_shown,
    render,
    time_selector,
)
from .text import (
    PRESET_CUSTOM,
    PRESET_UNKNOWN,
    Text,
    entity_name,
    preset_options,
    target_options,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.tariffs.model import TariffSpec, TariffVersion

_LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Timezone (D-0120)
# --------------------------------------------------------------------------- #


async def resolve_timezone(hass: HomeAssistant) -> str | None:
    """Return the site's timezone from the environment, or `None` to ask.

    `hass.config.time_zone` is the site timezone: one zone for the house, set
    where every other integration reads it from. `None` here is the *only* reason
    the flow shows a timezone step, and it means Home Assistant's own setting is
    missing or is not a zone this system's tzdata knows.
    """
    key = hass.config.time_zone
    if not key:
        return None
    return key if await dt_util.async_get_time_zone(key) is not None else None


async def timezone_options(hass: HomeAssistant) -> list[str]:
    """Return every zone this system knows, sorted (reads tzdata: executor).

    tzdata also ships files that are not zones a household lives in -
    `localtime`, `posixrules` - spelled in lower case like an internal key; they
    are not offered (review R2).
    """
    zones = await hass.async_add_executor_job(zoneinfo.available_timezones)
    return sorted(zone for zone in zones if zone != zone.lower())


def timezone_schema(zones: Sequence[str], default: str | None) -> vol.Schema:
    """Return a searchable dropdown over the zones this system knows."""
    field = vol.Optional("timezone", default=default) if default else vol.Required("timezone")
    return vol.Schema(
        {
            field: SelectSelector(
                SelectSelectorConfig(
                    options=list(zones), mode=SelectSelectorMode.DROPDOWN, sort=False
                )
            )
        }
    )


# --------------------------------------------------------------------------- #
# name
# --------------------------------------------------------------------------- #

#: Every other default in the flow is derived; the site's name cannot be.
DEFAULT_SITE_NAME: Final = "Home"


def name_schema(default: str) -> vol.Schema:
    """One text field, pre-filled."""
    return vol.Schema({vol.Optional("name", default=default): str})


# --------------------------------------------------------------------------- #
# electrical (D3 §6)
# --------------------------------------------------------------------------- #

#: D3 §6's list, extended upwards (`design/DECISIONS.md` D-0124); the select is
#: `flow/questionnaire.py`'s, shared with the circuit's fuse (review CTL-1).
FUSE_RATINGS: Final = MAIN_FUSE_SIZES

#: D3 §5.1 refuses anything outside this band as "not a grid connection".
FUSE_MIN_A: Final = 6.0
FUSE_MAX_A: Final = 400.0

_THREE_PHASE_TN = (
    "SE", "FI", "DK", "DE", "NL", "BE", "AT", "CH",
    "FR", "ES", "IT", "PL", "EE", "LV", "LT", "CZ", "SK", "SI", "HR", "HU", "PT",
)  # fmt: skip
_SPLIT = ("US", "CA")
_SINGLE = ("GB", "IE", "AU", "NZ")


def default_system(country: str | None) -> VoltageSystem:
    """Return the supply system a country's houses usually have (D3 §6).

    Norway is the one country where both are common; the IT system is what the
    older stock has and what the 230 V line-to-line conversion assumes, and the
    review step states the resulting kW so a wrong answer is visible before it is
    saved rather than after (INV-67, D-0125).
    """
    if country in _SPLIT:
        return VoltageSystem.SPLIT_240
    if country in _SINGLE:
        return VoltageSystem.SINGLE_230
    if country in _THREE_PHASE_TN:
        return VoltageSystem.TN_400
    return VoltageSystem.IT_230


def default_phases(country: str | None) -> int:
    """Return the phase count a country's houses usually have (D3 §6)."""
    return 1 if country in _SPLIT + _SINGLE else 3


def default_fuse_a(country: str | None) -> str:
    """Return the main fuse a country's houses usually have (D3 §6)."""
    if country in _SPLIT:
        return "200"
    if country in _SINGLE:
        return "100"
    if country in _THREE_PHASE_TN:
        return "25"
    return "63"


#: Norway is the one country where both supply systems are common; the household
#: answers by voltage, with "Vet ikke" (D3 §6).
_ASKS_VOLTAGE: Final = ("NO",)
_VOLTAGE_OPTIONS: Final = (str(VoltageSystem.IT_230), str(VoltageSystem.TN_400), DONT_KNOW)


def electrical_schema(
    *, country: str | None, values: Mapping[str, Any] | None = None, ask_country: bool = False
) -> vol.Schema:
    """Return "Hvor stor er hovedsikringen?": the fuse, and in Norway the voltage (D3 §6).

    The main fuse is a pick of sizes labelled "63 A", a size typed in, or "Vet
    ikke", and `vol.Required` with its default so there is no clear button
    (review CTL-1). The country comes from Home Assistant and is asked only when
    it has none (HUB-2); the phases follow from it and sit under Avansert with
    the per-phase limit, whose suggestion is the fuse (HUB-19).
    """
    given = values or {}
    system = str(given.get("system") or default_system(country))
    phases = str(given.get("phases") or default_phases(country))
    fuse = str(given.get("main_fuse_a") or default_fuse_a(country))
    suggested_limit = phase_limit_suggestion(country, given)
    limit = given.get("per_phase_limit_a")
    fields: dict[Any, Any] = {}
    if ask_country:
        fields[vol.Optional("country", default=given.get("country") or country or "")] = (
            CountrySelector(CountrySelectorConfig())
        )
    fields[vol.Required("main_fuse_a", default=fuse)] = fuse_selector(FUSE_RATINGS, dont_know=True)
    if country in _ASKS_VOLTAGE:
        fields[vol.Required("system", default=system)] = SelectSelector(
            SelectSelectorConfig(
                options=list(_VOLTAGE_OPTIONS),
                mode=SelectSelectorMode.LIST,
                translation_key="voltage",
                sort=False,
            )
        )
    else:
        fields[vol.Required("system", default=system)] = SelectSelector(
            SelectSelectorConfig(
                options=[member.value for member in VoltageSystem],
                mode=SelectSelectorMode.LIST,
                translation_key="voltage_system",
                sort=False,
            )
        )
    limit_marker = (
        vol.Optional("per_phase_limit_a", default=float(limit))
        if limit is not None and float(limit) != suggested_limit
        else vol.Optional("per_phase_limit_a", description={"suggested_value": suggested_limit})
    )
    fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(
        {
            vol.Optional("phases", default=phases): SelectSelector(
                SelectSelectorConfig(
                    options=["1", "3"], mode=SelectSelectorMode.LIST, translation_key="phases"
                )
            ),
            limit_marker: NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="A"
                )
            ),
        }
    )
    return vol.Schema(fields)


def phase_limit_suggestion(country: str | None, values: Mapping[str, Any]) -> float:
    """Return what the per-phase limit is when nothing lower was set: the fuse shown (HUB-19)."""
    fuse = str(values.get("main_fuse_a") or default_fuse_a(country))
    try:
        return _amps(default_fuse_a(country) if fuse == DONT_KNOW else fuse)
    except StepError:
        return _amps(default_fuse_a(country))


def _amps(value: Any) -> float:
    """Return a fuse answer in amps: `"63"`, a typed `"45 A"` or `"45,5"` (CTL-1's "Annet…")."""
    amps = amps_of(value)
    if amps is None:
        raise StepError("main_fuse_a", "fuse_out_of_range")
    return amps


class StepError(ValueError):
    """A step's answers were refused; the message is a translation key (INV-49)."""

    def __init__(self, field: str, key: str) -> None:
        """Name the field the error belongs under and its translation key."""
        super().__init__(key)
        self.field = field
        self.key = key


def electrical_profile(answers: Mapping[str, Any]) -> ElectricalProfile:
    """Validate the electrical step and return D3's profile (INV-49).

    Both halves of D3 §6's validation live here: a fuse outside 6–400 A is not a
    grid connection, and a phase count the supply system has no conversion for is
    a configuration error the domain type itself refuses.
    """
    answer = answers["main_fuse_a"]
    fuse = float(default_fuse_a(answers.get("country"))) if answer == DONT_KNOW else _amps(answer)
    limit = float(answers.get("per_phase_limit_a") or fuse)
    for field, value in (("main_fuse_a", fuse), ("per_phase_limit_a", limit)):
        if not FUSE_MIN_A <= value <= FUSE_MAX_A:
            raise StepError(field, "fuse_out_of_range")
    country = answers.get("country")
    phases = int(answers.get("phases") or default_phases(country))
    if phases not in (1, 3):
        raise StepError("phases", "phases_not_available")
    system = answers.get("system")
    profile = ElectricalProfile(
        system=default_system(country) if system in (None, DONT_KNOW) else VoltageSystem(system),
        phases=phases,  # type: ignore[arg-type]
        main_fuse_a=fuse,
        per_phase_limit_a=limit,
    )
    try:
        profile.fuse_w()
    except ValueError as err:
        raise StepError("phases", "phases_not_available") from err
    return profile


def electrical_data(answers: Mapping[str, Any], profile: ElectricalProfile) -> dict[str, Any]:
    """Materialise D3's derived numbers into the entry (INV-66).

    A later release may change the conversion table; this site's watts per amp,
    its fuse in watts and its plausibility band are the ones it was set up with.
    """
    low, high = profile.plausible_w()
    data: dict[str, Any] = {
        "country": answers.get("country") or "",
        "system": str(profile.system),
        "phases": profile.phases,
        "main_fuse_a": profile.main_fuse_a,
        "per_phase_limit_a": profile.phase_limit_a(),
        "derived": {
            "v_ll": profile.v_ll(),
            "v_ln": profile.v_ln(),
            "service_phases": profile.service_phases(),
            "w_per_amp": profile.w_per_amp(profile.service_phases()),
            "fuse_w": profile.fuse_w(),
            "plausible_w": [low, high],
            "plausible_factor": PLAUSIBLE_FACTOR,
        },
    }
    # "Vet ikke" took the country's default; the review names it as assumed,
    # and a reconfigure shows "Vet ikke" again (D3 §6, D8 §5.15 rule 4).
    assumed = [key for key in ("main_fuse_a", "system") if answers.get(key) == DONT_KNOW]
    if assumed:
        data["assumed"] = assumed
    return data


# --------------------------------------------------------------------------- #
# meter (D3 §6)
# --------------------------------------------------------------------------- #

#: v1 reads the meter from ordinary HA sensors (`providers/meters/ha_sensors.py`).
METER_SOURCE: Final = "ha_sensors"

#: Each role's device class, the units it may carry and - for a register - the
#: state classes a cumulative reading has (D3 §6, review CTL-10). The picker is
#: filtered by the device class, which both Home Assistant lines do; the unit and
#: the state class are checked on submit, because the entity filter's unit is
#: not on the 2026.3 floor line and no filter has a state class (D-0392).
_POWER_UNITS: Final = ("W", "kW")
_ENERGY_UNITS: Final = ("Wh", "kWh", "MWh")
_CUMULATIVE: Final = ("total_increasing", "total")
_ROLE_FILTERS: Final[Mapping[str, tuple[str, tuple[str, ...], tuple[str, ...] | None]]] = {
    ROLE_GRID_POWER: ("power", _POWER_UNITS, None),
    ROLE_IMPORT_REGISTER: ("energy", _ENERGY_UNITS, _CUMULATIVE),
    ROLE_EXPORT_REGISTER: ("energy", _ENERGY_UNITS, _CUMULATIVE),
    ROLE_PRODUCTION_POWER: ("power", _POWER_UNITS, None),
    ROLE_METER_WINDOW: ("energy", _ENERGY_UNITS, None),
    ROLE_PHASE_L1: ("current", ("A",), None),
    ROLE_PHASE_L2: ("current", ("A",), None),
    ROLE_PHASE_L3: ("current", ("A",), None),
}
#: The error a role's wrong unit reports, by device class.
_UNIT_ERRORS: Final = {
    "power": "power_unit_not_watts",
    "energy": "energy_unit_not_kwh",
    "current": "current_unit_not_amps",
}


def meter_device_schema(default: str | None) -> vol.Schema:
    """One optional device pick, offering only devices that measure power (D3 §6, H8).

    Leaving it empty binds the sensors by hand on the next step.
    """
    marker = vol.Optional("device", default=default) if default else vol.Optional("device")
    return vol.Schema(
        {
            marker: DeviceSelector(
                DeviceSelectorConfig(entity=[{"domain": "sensor", "device_class": "power"}])
            )
        }
    )


#: The phase currents sit in their own collapsed section of the role form (HUB-22).
SECTION_PER_PHASE: Final = "per_phase"
_PHASE_ROLES: Final = (ROLE_PHASE_L1, ROLE_PHASE_L2, ROLE_PHASE_L3)

#: The roles without which the confirmation cannot stand: the capacity axis reads
#: one of them (INV-53). With neither found, the role form is shown instead.
_CAPACITY_ROLES: Final = (ROLE_GRID_POWER, ROLE_IMPORT_REGISTER)

#: The confirmation's two answers (D3 §6): what was found is right, or change it.
METER_OK: Final = "ok"
METER_CHANGE: Final = "change"


def meter_found(prefilled: Mapping[str, str]) -> bool:
    """Whether the device gave a role the capacity axis can read, so it can be confirmed."""
    return any(prefilled.get(role) for role in _CAPACITY_ROLES)


def meter_values(hass: HomeAssistant, text: Text, roles: Mapping[str, str]) -> str:
    """Return each found role with its value now: "Effekt nå: 1,2 kW ✓" (D3 §6, §9 21 (d)).

    A household judges a sensor by what it reads, not by its name - the export
    register the review took for a mistake read 0 kWh. The value comes through the
    meter provider (INV-3); a role whose sensor has no value shows " - ".
    """
    now = dt_util.utcnow()
    lines: list[str] = []
    for role in METER_ROLES:
        entity_id = roles.get(role)
        if not entity_id:
            continue
        quantity = _ROLE_FILTERS[role][0]
        value = value_now(hass, entity_id, quantity, now)
        label = text.string(f"config.step.meter_roles.data.{role}") or text.string(
            f"config.step.meter_roles.sections.{SECTION_PER_PHASE}.data.{role}"
        )
        if value is None:
            shown, mark = "—", "?"
        elif quantity == "power":
            shown, mark = text.kw(value / 1000.0), "✓"
        elif quantity == "energy":
            shown, mark = f"{text.number(value, 0)} kWh", "✓"
        else:
            shown, mark = f"{text.number(value, 1)} A", "✓"
        lines.append(f"- {label} ({entity_name(hass, entity_id)}): {shown} {mark}")
    return "\n".join(lines)


def meter_confirm_schema() -> vol.Schema:
    """Right as found, or change it (D3 §6): a radio, "ok" by default."""
    return vol.Schema(
        {
            vol.Required("confirm", default=METER_OK): SelectSelector(
                SelectSelectorConfig(
                    options=[METER_OK, METER_CHANGE],
                    mode=SelectSelectorMode.LIST,
                    translation_key="meter_confirm",
                )
            )
        }
    )


def meter_roles_schema(prefilled: Mapping[str, str]) -> vol.Schema:
    """Return the roles of D3 §6, pre-filled where the registry could tell.

    L1–L3 go in one optional "Per fase" section (HUB-22).
    """
    fields: dict[Any, Any] = {}
    phases: dict[Any, Any] = {}
    for role in METER_ROLES:
        device_class = _ROLE_FILTERS[role][0]
        found = prefilled.get(role)
        marker = vol.Optional(role, default=found) if found else vol.Optional(role)
        (phases if role in _PHASE_ROLES else fields)[marker] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class=device_class)
        )
    fields[vol.Optional(SECTION_PER_PHASE, default={})] = section(
        vol.Schema(phases), {"collapsed": not any(prefilled.get(r) for r in _PHASE_ROLES)}
    )
    return vol.Schema(fields)


def meter_roles_answers(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the role form's answers with the per-phase section merged up."""
    merged = {key: value for key, value in user_input.items() if key != SECTION_PER_PHASE}
    merged.update(user_input.get(SECTION_PER_PHASE) or {})
    return merged


def meter_data(
    hass: HomeAssistant, device_id: str | None, roles: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate the bound roles and materialise the meter section (INV-49)."""
    bound = {role: roles[role] for role in METER_ROLES if roles.get(role)}
    registry = er.async_get(hass)
    for role, entity_id in bound.items():
        entry = registry.async_get(entity_id)
        if entry is None:
            continue
        device_class, units, state_classes = _ROLE_FILTERS[role]
        if entry.unit_of_measurement not in (None, *units):
            raise StepError(role, _UNIT_ERRORS[device_class])
        state_class = (entry.capabilities or {}).get("state_class")
        if state_classes is not None and state_class not in (None, *state_classes):
            raise StepError(role, "register_not_cumulative")
    return {"source": METER_SOURCE, "device_id": device_id, "roles": bound}


# --------------------------------------------------------------------------- #
# prices (D1 §6)
# --------------------------------------------------------------------------- #

SOURCE_NORDPOOL: Final = "nordpool_action"
SOURCE_ENTITY: Final = "entity"
SOURCE_FIXED: Final = "fixed"
PRICE_SOURCES: Final = (SOURCE_NORDPOOL, SOURCE_ENTITY, SOURCE_FIXED)
#: "Hvilken strømavtale har du?" also answers Norgespris in Norway: the spot
#: source with the `fixed_price` add-on asked for (D1 §6). Not a stored source.
AGREEMENT_NORGESPRIS: Final = "norgespris"
_NORGESPRIS_COUNTRIES: Final = ("NO",)

#: Where Nord Pool is the obvious answer (D1 §6, HLD §8).
_NORDPOOL_COUNTRIES: Final = (
    "NO", "SE", "FI", "DK", "EE", "LV", "LT", "NL", "BE", "DE", "LU", "FR", "AT",
)  # fmt: skip


def default_price_source(country: str | None) -> str:
    """Return the source a country's houses usually have (D1 §6)."""
    return SOURCE_NORDPOOL if country in _NORDPOOL_COUNTRIES else SOURCE_FIXED


def agreements(country: str | None) -> list[str]:
    """Return the answers to "Hvilken strømavtale har du?" for `country` (D1 §6)."""
    if country in _NORGESPRIS_COUNTRIES:
        return [SOURCE_NORDPOOL, AGREEMENT_NORGESPRIS, SOURCE_ENTITY, SOURCE_FIXED]
    return list(PRICE_SOURCES)


def price_source_schema(default: str, country: str | None = None) -> vol.Schema:
    """Return D1 §6's agreement question: spot through Nord Pool or a sensor, Norgespris, fixed."""
    options = agreements(country)
    return vol.Schema(
        {
            vol.Optional(
                "source", default=default if default in options else options[0]
            ): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    mode=SelectSelectorMode.LIST,
                    translation_key="price_source",
                    sort=False,
                )
            )
        }
    )


def nordpool_defaults(hass: HomeAssistant, currency: str) -> dict[str, Any]:
    """Pre-fill the Nord Pool step from the entry the house already has.

    One Nord Pool config entry is the normal case, and its own entities name the
    area - `sensor.nord_pool_no3_current_price` is an NO3 house. The market's
    publication timezone is the source's business, never the flow's (D-0120).
    """
    entries = hass.config_entries.async_entries(NORDPOOL_DOMAIN)
    defaults: dict[str, Any] = {"currency": currency}
    if len(entries) != 1:
        return defaults
    entry = entries[0]
    defaults["config_entry"] = entry.entry_id
    registry = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        words = registered.entity_id.split(".", 1)[1].split("_")
        for area in AREAS:
            if area.lower().replace("-", "") in words:
                defaults["area"] = area
                return defaults
    return defaults


def nordpool_suggested(area: str | None) -> dict[str, str]:
    """Return Nord Pool's publication clock for `area`, derived (review HUB-19).

    Shown as the Advanced fields' suggested values, so the household sees what
    "leave it" means; an answer equal to it is not an override and is not
    stored (`drop_suggested`), which keeps the clock derived from the area.
    """
    clock = NORDPOOL_MARKETS.get(area or "")
    if clock is None:
        return {}
    return {"publication_tz": clock.tz, "publication_time": clock.local_time.strftime("%H:%M")}


def drop_suggested(answers: Mapping[str, Any], suggested: Mapping[str, Any]) -> dict[str, Any]:
    """Return `answers` without a field left at the value it was suggested (HUB-19)."""
    return {
        key: value
        for key, value in answers.items()
        if not (key in suggested and same_value(value, suggested[key]))
    }


def same_value(answer: Any, suggestion: Any) -> bool:
    """Whether an answer is the suggestion it was shown: `"13:00:00"` is `"13:00"`, `5` is `5.0`."""
    if isinstance(answer, str) and isinstance(suggestion, str):
        return answer[:5] == suggestion[:5] if ":" in suggestion else answer == suggestion
    if isinstance(answer, int | float) and isinstance(suggestion, int | float):
        return float(answer) == float(suggestion)
    return bool(answer == suggestion)


def nordpool_detected(values: Mapping[str, Any]) -> bool:
    """Whether the house's one Nord Pool entry named both itself and the area (CTL-9)."""
    return bool(values.get("config_entry") and values.get("area"))


def area_options(text: Text) -> list[SelectOptionDict]:
    """Every Nord Pool area by its region name: "NO3 – Midt-Norge" (CTL-9).

    The codes are the values; a code is not a usable translation key (`NO3`,
    `DE-LU`), so the labels are read from `selector.nordpool_area` by its
    lower-case spelling and assembled here.
    """
    return [
        SelectOptionDict(
            value=area,
            label=text.word("nordpool_area", area.lower().replace("-", "_")) or area,
        )
        for area in AREAS
    ]


def nordpool_schema(*, values: Mapping[str, Any], text: Text) -> vol.Schema:
    """Render the Nord Pool source from its own registry schema (D1 §6).

    The currency is Home Assistant's and never shown (HUB-2); the area carries
    its region name (CTL-9).
    """
    rendered = render(
        NordpoolActionSource.schema,
        translation_prefix="nordpool",
        values=values,
        overrides={
            "config_entry": ConfigEntrySelector(
                ConfigEntrySelectorConfig(integration=NORDPOOL_DOMAIN)
            ),
            "area": SelectSelector(
                SelectSelectorConfig(
                    options=area_options(text), mode=SelectSelectorMode.DROPDOWN, sort=False
                )
            ),
        },
        suggested=nordpool_suggested(values.get("area")),
    )
    return vol.Schema({key: value for key, value in rendered.schema.items() if key != "currency"})


def price_entity_schema(*, default_entity: str | None, default_format: str | None) -> vol.Schema:
    """Return the entity field and the detected format (D1 §6, `for_platform`)."""
    entity_marker = (
        vol.Optional("entity_id", default=default_entity)
        if default_entity
        else vol.Optional("entity_id")
    )
    return vol.Schema(
        {
            entity_marker: EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional("format", default=default_format or formats.keys()[0]): SelectSelector(
                SelectSelectorConfig(
                    options=list(formats.keys()),
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="price_format",
                    sort=False,
                )
            ),
        }
    )


def fixed_price_schema(currency: str, *, default: Any = None) -> vol.Schema:
    """One number: what a kWh costs when nothing publishes a curve (D1 §6).

    In the currency's minor unit per kWh, as on the bill (CTL-3); `default` is
    the stored price in major units. With nothing stored there is nothing to
    default to, so the price is required (D8 §9 21 (a)).
    """
    shown = price_shown(default, currency)
    marker = vol.Required("price") if shown is None else vol.Required("price", default=shown)
    return vol.Schema({marker: price_selector(currency)})


def pre_tickable(key: str) -> bool:
    """Return whether a modifier can be pre-ticked with no question asked.

    HLD §7.9 (2) says every answer has a default. A modifier with a required
    option and no default for it - an energy levy, a time-of-use table - has no
    answer to default to, so it is offered and never pre-ticked; the grid charge
    then arrives with the tariff preset, which has the numbers and their source
    (D2 §6, D-0126).
    """
    schema = modifiers.entry(key).schema
    return all(field.default is not None for field in schema if field.required)


def offered_modifiers() -> list[str]:
    """Return what the supplier step offers "in addition to the grid tariff" (D13 §6 2a, INV-74).

    The supplier's own lines only: a markup, tiers, day types, its own time of use.
    The grid company's charge comes with its tariff copy, VAT and levies from the
    country, strømstøtte in the state step, and Norgespris is the agreement itself.
    """
    return [
        key
        for key in modifiers.keys()  # noqa: SIM118 - the registry function
        if PARTY.get(modifiers.entry(key).component) is Party.SUPPLIER
        and modifiers.entry(key).component != SPOT
    ]


def modifiers_schema(chosen: Sequence[str] | None = None) -> vol.Schema:
    """Every supplier addition, the household's own ticked (D13 §6 step 2a).

    Nothing is pre-ticked: an addition is a line in the household's contract,
    which the flow cannot know (HLD §7.9 (2) - "none" is the default answer).
    """
    available = offered_modifiers()
    default = [key for key in chosen or () if key in available]
    return vol.Schema(
        {
            vol.Optional("modifiers", default=default): SelectSelector(
                SelectSelectorConfig(
                    options=available,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key="modifier",
                    sort=False,
                )
            )
        }
    )


def modifier_options_schema(
    key: str, *, currency: str | None = None, values: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Render one modifier's options from its registry schema (D1 §6).

    `values` are the add-on's stored options, so a reconfigure shows them again -
    lists as rows included (review HUB-9; D-0330 had left them out).
    """
    return render(
        modifiers.entry(key).schema,
        translation_prefix=f"modifier_{key}",
        values=values,
        currency=currency,
    )


EXPORT_NONE: Final = "none"


def export_schema(*, values: Mapping[str, Any] | None = None) -> vol.Schema:
    """Return D1 §6's export question - how an exported kWh is paid, or not at all.

    Only the mode: the amounts follow on their own step when there is something
    exported to price (D8 §5.15 rule 5, review HUB-17).
    """
    schema = modifiers.entry("export_price").schema
    mode_field = next(field for field in schema if field.key == "mode")
    options = [EXPORT_NONE, *(str(option) for option in mode_field.options)]
    values = values or {}
    return vol.Schema(
        {
            vol.Optional("mode", default=values.get("mode", EXPORT_NONE)): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    mode=SelectSelectorMode.LIST,
                    translation_key="export_mode",
                    sort=False,
                )
            )
        }
    )


#: Which of `export_price`'s amounts each mode reads (D1 §5.4); the others would
#: be dead fields (review HUB-17).
EXPORT_FIELDS: Final = {
    "fixed": ("amount",),
    "spot_minus": ("amount",),
    "spot_times": ("share",),
    "from_source": (),
}


def export_amounts_schema(
    mode: str, *, currency: str, values: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Return the amounts the chosen export mode reads, rendered from its own schema."""
    wanted = EXPORT_FIELDS.get(mode, ("amount", "share"))
    schema = tuple(field for field in modifiers.entry("export_price").schema if field.key in wanted)
    return render(schema, translation_prefix="export", values=values, currency=currency)


CARRIER_FIXED: Final = "fixed"
CARRIER_SENSOR: Final = "sensor"


def carriers_schema(chosen: Sequence[str] | None = None) -> vol.Schema:
    """Which other carriers the house buys (D1 §6); none by default."""
    options = [str(carrier) for carrier in Carrier if carrier is not Carrier.ELECTRICITY]
    return vol.Schema(
        {
            vol.Optional("carriers", default=list(chosen) if chosen else []): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key="carrier",
                    sort=False,
                )
            )
        }
    )


def carrier_options_schema(currency: str) -> vol.Schema:
    """One carrier's price: fixed, in the minor unit per kWh, or a sensor read daily (D1 §6)."""
    return vol.Schema(
        {
            vol.Optional("mode", default=CARRIER_FIXED): SelectSelector(
                SelectSelectorConfig(
                    options=[CARRIER_FIXED, CARRIER_SENSOR],
                    mode=SelectSelectorMode.LIST,
                    translation_key="carrier_mode",
                )
            ),
            vol.Optional("price", default=0.0): price_selector(currency),
            vol.Optional("entity_id"): EntitySelector(
                EntitySelectorConfig(domain="sensor", device_class="monetary")
            ),
        }
    )


# --------------------------------------------------------------------------- #
# tariff (D2 §6)
# --------------------------------------------------------------------------- #

#: What "I don't know / not listed" falls back to, per country: the country's
#: template where one ships - its steps asked from the bill - and no capacity
#: component where none does (D2 §2, §6).
_GENERIC_PRESET: Final = {"NO": "no/template"}

#: Where a template leaves the contracted kW to the household, the value the
#: limits step starts from (D2 §6's table): 2.0TD's common 20 A / 25 A single
#: phase (4.6 / 5.75 kW), a Dutch 3×25 A connection (17.25 kW). A starting value
#: to change, never a fact about the household (D-0521).
LIMIT_DEFAULTS: Final[Mapping[str, tuple[float, ...]]] = {"ES": (4.6, 5.75), "NL": (17.25,)}

#: The bill's step table as rows of "up to kW" and "per month" (D2 §6, the
#: template's form). Twelve rows cover every Norwegian table read but
#: Tensio's fifteen, whose top steps are over 150 kW; the bounds pre-filled
#: are the ones most DSOs use, the fees are never pre-filled.
STEP_ROWS: Final = 12
_STEP_BOUNDS: Final = (2, 5, 10, 15, 20, 25, 50, 75, 100)
#: A step table is at least a bottom step and the open top.
_MIN_STEPS: Final = 2

#: The site a price-only house gets: there is no meter, so there is no metric to
#: bill and the capacity axis is off (HLD §4, D-0127).
NO_PEAK_TARIFF: Final[dict[str, Any]] = {
    "preset_id": "no_peak",
    "preset_file": None,
    "version_ids": [],
}


def _preset_dir() -> Path:
    return Path(loader.HERE)


def discover_presets(country: str | None) -> list[tuple[str, str]]:
    """Return `(file stem, display name)` for a country's presets (blocking I/O).

    Reads and validates every shipped preset for the country, so the select can
    never offer a file that will not load. Called through the executor.
    """
    if not country:
        return []
    folder = _preset_dir() / country.lower()
    found: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.json")):
        stem = f"{country.lower()}/{path.stem}"
        try:
            raw = loader.load_raw(stem)
        except loader.PresetError:
            _LOGGER.exception("shipped preset %s does not validate and is not offered", stem)
            continue
        found.append((stem, str(raw["name"])))
    return found


def preset_raw(preset_file: str) -> dict[str, Any]:
    """Return a preset's JSON - a retired file's successor's - for the flow (blocking I/O)."""
    return loader.load_raw(loader.successor(preset_file) or preset_file)


def needs_steps(raw: Mapping[str, Any]) -> bool:
    """Whether the preset is a template whose step table the household enters (D2 §6)."""
    return any(
        (version.get("peak") or {}).get("pricing", {}).get("steps", ()) is None
        for version in raw["versions"]
    )


def steps_schema(
    currency: str, *, values: Mapping[str, Any] | None = None, ask_vat: bool = False
) -> vol.Schema:
    """Return the bill's steps as a form: "up to" kW and the fee per month, row by row (D2 §6).

    Rows left without a fee are ignored; the last row with one is the open top,
    whatever its bound says.
    """
    values = values or {}
    fields: dict[Any, Any] = {}
    for row in range(1, STEP_ROWS + 1):
        bound = _STEP_BOUNDS[row - 1] if row <= len(_STEP_BOUNDS) else None
        upper = values.get(f"upper_{row}", bound)
        fee = values.get(f"fee_{row}")
        # A bound is a default - a row without a fee is ignored whatever it says -
        # while a fee is never pre-filled: it is the household's number.
        key = (
            vol.Optional(f"upper_{row}")
            if upper is None
            else vol.Optional(f"upper_{row}", default=upper)
        )
        fields[key] = NumberSelector(
            NumberSelectorConfig(
                min=0, mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW"
            )
        )
        fields[vol.Optional(f"fee_{row}", description={"suggested_value": fee})] = price_selector(
            currency
        )
    if ask_vat:
        # Only where the country's module knows no VAT (D13 §9.1, step 1c-prime).
        fields[vol.Required("vat", default=values.get("vat", 0))] = percent_selector(slider=False)
    return vol.Schema(fields)


def step_rows(answers: Mapping[str, Any]) -> list[tuple[float | None, float]]:
    """Return the answered rows as `(upper_kw, fee)`, the last open-ended (D2 §6).

    No fee at all is an answer - "I don't have the bill at hand" - and returns no
    rows; the flow then keeps no capacity component (HLD §7.9 (2)'s safe default).
    Raises `StepError` on a single row, a missing bound below the top or bounds
    that do not rise - a table the evaluator could not classify.
    """
    rows: list[tuple[float | None, float]] = []
    for row in range(1, STEP_ROWS + 1):
        fee = answers.get(f"fee_{row}")
        if fee is None:
            continue
        upper = answers.get(f"upper_{row}")
        rows.append((None if upper is None else float(upper), float(fee)))
    if not rows:
        return []
    if len(rows) < _MIN_STEPS:
        raise StepError("fee_1", "steps_too_few")
    bounds = [upper for upper, _ in rows[:-1]]
    if any(upper is None for upper in bounds):
        raise StepError("upper_1", "steps_not_rising")
    rising = [float(upper) for upper in bounds if upper is not None]
    if any(later <= earlier for earlier, later in pairwise(rising)):
        raise StepError("upper_1", "steps_not_rising")
    return [*rows[:-1], (None, rows[-1][1])]


def step_answers(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stored copy's step table as the steps form's answers (a reconfigure)."""
    peak = raw["versions"][-1].get("peak") or {}
    table = peak.get("pricing", {}).get("steps") or ()
    answers: dict[str, Any] = {}
    for row, (upper, fee, _name) in enumerate(table, start=1):
        answers[f"upper_{row}"] = upper
        answers[f"fee_{row}"] = fee
    return answers


def filled_tariff(
    raw: Mapping[str, Any],
    *,
    country: str | None,
    steps: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the tariff this site keeps: the preset with the household's numbers (D2 §6).

    The step table comes from the steps form; the contracted kW from the limits
    step once answered, else the file's own, else the country's starting value -
    so the summary can be shown before the limits step asks.
    """
    rows = step_rows(steps) if needs_steps(raw) else []
    contracted = [
        limit["limit_kw"]
        for limit in (raw["versions"][-1].get("contracted") or {}).get("limits", ())
    ]
    kw: list[float] = []
    if contracted:
        answered = [value for _, value in sorted(limits.items()) if value is not None]
        starting = LIMIT_DEFAULTS.get((country or "").upper(), ())
        for index, stored in enumerate(contracted):
            if index < len(answered):
                kw.append(float(answered[index]))
            elif stored is not None:
                kw.append(float(stored))
            elif index < len(starting):
                kw.append(starting[index])
            else:
                raise StepError("limit_1", "limit_required")
    return loader.fill_template(raw, steps=rows, limits=kw)


def preset_choice(preset_file: str | None, country: str | None) -> str | None:
    """Return the tariff step's answer a stored preset file means (the inverse of `preset_file`).

    The country's generic tariff model *is* "not listed" (D2 §6), so it is offered
    under that label and not a second time under its file's own name.
    """
    if preset_file is None:
        return None
    if preset_file == _GENERIC_PRESET.get((country or "").upper()):
        return PRESET_UNKNOWN
    return preset_file


def tariff_schema(
    *,
    country: str | None,
    presets: Sequence[tuple[str, str]],
    chosen: str | None,
    text: Text,
    ask_country: bool = False,
) -> vol.Schema:
    """Ask "Hvilket nettselskap har du?": the grid companies A–Å, the two escape hatches last (D2 §6).

    Names are the operators' own (data); the escape hatches are translated and
    pinned after the list, sorted by the language's own alphabet (HUB-11). The
    country is asked once, and only when Home Assistant has none (HUB-2).
    """
    generic = _GENERIC_PRESET.get((country or "").upper())
    named = [(stem, name) for stem, name in presets if stem != generic]
    options = preset_options(text, named)
    default = preset_choice(chosen, country) or (named[0][0] if len(named) == 1 else PRESET_UNKNOWN)
    fields: dict[Any, Any] = {}
    if ask_country:
        fields[vol.Optional("country", default=country or "")] = CountrySelector(
            CountrySelectorConfig()
        )
    fields[vol.Optional("preset", default=default)] = SelectSelector(
        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN, sort=False)
    )
    return vol.Schema(fields)


#: "Stemmer dette med nettleiefakturaen din?" - yes, or back to the list (HUB-4).
TARIFF_YES: Final = "yes"
TARIFF_NO: Final = "no"


def tariff_confirm_schema() -> vol.Schema:
    """Return a yes/no radio: "no" shows the grid companies again, since HA flows have no back (HUB-4)."""
    return vol.Schema(
        {
            vol.Required("confirm", default=TARIFF_YES): SelectSelector(
                SelectSelectorConfig(
                    options=[TARIFF_YES, TARIFF_NO],
                    mode=SelectSelectorMode.LIST,
                    translation_key="tariff_confirm",
                )
            )
        }
    )


def preset_file(choice: str, country: str | None) -> str:
    """Return the preset file one answer of the tariff step means (D2 §6)."""
    if choice == PRESET_CUSTOM:
        return PRESET_CUSTOM
    if choice == PRESET_UNKNOWN:
        return _GENERIC_PRESET.get((country or "").upper(), PRESET_CUSTOM)
    return choice


def peak_of(version: TariffVersion) -> PeakTariff | None:
    """Return the version's capacity component, if it has one."""
    return version.peak


RISK_LABELS: Final = {"flat": RISK_FLAT, "free_ride": RISK_FREE_RIDE, "full": RISK_FULL}


def risk_key(risk: float) -> str:
    """Return the select value one risk number means."""
    for key, value in RISK_LABELS.items():
        if value == risk:
            return key
    return "flat"


def peak_hours(version: TariffVersion) -> int:
    """How many windows the metric averages - Tensio's three - for the strictness help (D2 §6)."""
    peak = peak_of(version)
    return peak.n if peak is not None else 1


def tariff_target_schema(
    version: TariffVersion, *, text: Text, values: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Target, risk, and the two advanced numbers of D2 §6.

    The target's labels are assembled in the system language (`flow/text.py`,
    HUB-13); its values are `auto` and `step_<i>` (ENT-2).
    """
    peak = peak_of(version)
    # Strict for a new site (D2 §6); a reconfigure shows the site's own.
    default = default_risk(version.rules)
    values = values or {}
    fields: dict[Any, Any] = {
        vol.Optional("target", default=values.get("target", "auto")): SelectSelector(
            SelectSelectorConfig(
                options=target_options(text, version),
                mode=SelectSelectorMode.DROPDOWN,
                sort=False,
            )
        ),
        vol.Optional("risk", default=values.get("risk", risk_key(default))): SelectSelector(
            SelectSelectorConfig(
                options=list(RISK_LABELS),
                mode=SelectSelectorMode.LIST,
                translation_key="risk",
                sort=False,
            )
        ),
    }
    if peak is not None and not isinstance(peak.pricing, StepTable):
        fields[vol.Optional("target_kw", default=values.get("target_kw") or 5.0)] = NumberSelector(
            NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW")
        )
    fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(
        {
            vol.Optional(
                "eps_kwh", default=values.get("eps_kwh", EPS_DEFAULT_KWH_PER_HOUR)
            ): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kWh"
                )
            ),
            vol.Optional(
                "cap_margin_kw", default=values.get("cap_margin_kw", CAP_MARGIN_KW)
            ): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW"
                )
            ),
        }
    )
    return vol.Schema(fields)


def validate_target(
    answers: Mapping[str, Any],
    version: TariffVersion | None = None,
    profile: ElectricalProfile | None = None,
) -> None:
    """D2 §6: an ε above 2 kWh is watts wearing a kWh label (INV-49).

    And a target the connection can never reach is refused on its field: a step
    whose lower bound is at or above what the main fuse delivers, or a Linear
    target above it (review CTL-16, D8 §9 21 (e)).
    """
    if version is not None and profile is not None:
        fuse_kw = profile.fuse_w() / 1000.0
        if _target_floor_kw(answers, version) >= fuse_kw:
            raise StepError("target", "target_above_fuse")
        target_kw = answers.get("target_kw")
        if target_kw is not None and float(target_kw) > fuse_kw:
            raise StepError("target_kw", "target_above_fuse")
    eps = answers.get("eps_kwh")
    if eps is not None and float(eps) > EPS_MAX_KWH:
        raise StepError("eps_kwh", "eps_looks_like_watts")
    if eps is not None and float(eps) <= 0:
        raise StepError("eps_kwh", "eps_not_positive")


def _target_floor_kw(answers: Mapping[str, Any], version: TariffVersion) -> float:
    """Return the lower bound of the step a target names; 0 for `auto` or a Linear tariff."""
    index = _step_of(str(answers.get("target", "auto")))
    peak = version.peak
    if index is None or index == 0 or peak is None or not isinstance(peak.pricing, StepTable):
        return 0.0
    return float(peak.pricing.upper_kw(index - 1))


def _step_of(target: str) -> int | None:
    """`step_<i>` (or a stored `step:<i>`) → `i`; anything else → `None`."""
    for prefix in ("step_", "step:"):
        if target.startswith(prefix) and target.removeprefix(prefix).isdigit():
            return int(target.removeprefix(prefix))
    return None


def needs_bills(version: TariffVersion) -> bool:
    """Return whether the preset bills on a rolling window (D2 §6)."""
    peak = peak_of(version)
    return peak is not None and peak.period == "rolling_months"


def bills_schema(peak: PeakTariff, *, values: Mapping[str, Any] | None = None) -> vol.Schema:
    """Return the last twelve monthly metrics, all of them optional (D2 §6)."""
    months = peak.rolling_months
    values = values or {}
    fields: dict[Any, Any] = {}
    for index in range(months):
        key = f"month_{index + 1}"
        marker = vol.Optional(key, default=values[key]) if key in values else vol.Optional(key)
        fields[marker] = NumberSelector(
            NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW")
        )
    return vol.Schema(fields)


def needs_limits(version: TariffVersion) -> bool:
    """Return whether the preset contracts a power limit per period (D2 §6)."""
    return version.contracted is not None


def limits_schema(version: TariffVersion, *, values: Mapping[str, Any] | None = None) -> vol.Schema:
    """One number per contracted period (D2 §6).

    Pre-filled from the preset - or, on a reconfigure, from what the household
    actually confirmed last time.
    """
    contracted = version.contracted
    assert contracted is not None
    unit = "kVA" if contracted.unit == "kva" else "kW"
    values = values or {}
    return vol.Schema(
        {
            vol.Optional(
                f"limit_{index + 1}", default=values.get(f"limit_{index + 1}", limit.limit_kw)
            ): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement=unit
                )
            )
            for index, limit in enumerate(contracted.limits)
        }
    )


def override_rows(price: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return a stored copy's state overrides as the add-on rows they came from (D13 §10, O4).

    The add-on step shows a kept VAT or levy ticked with its value, so the household
    can keep it or drop it; `migrate_tariff` turns the rows back into overrides.
    """
    overrides = ((price.get("state") or {}).get("overrides")) or {}
    rows: list[dict[str, Any]] = []
    if "vat" in overrides:
        rows.append(
            {
                "key": "vat",
                "component": "vat",
                "options": {"rate": str(overrides["vat"])},
                "source": "user",
            }
        )
    if "levy" in overrides:
        rows.append(
            {
                "key": "levy",
                "component": "levy",
                "options": {"amount": str(overrides["levy"])},
                "source": "user",
            }
        )
    return rows


def tariff_data(
    *,
    preset: str,
    spec: TariffSpec,
    version: TariffVersion,
    answers: Mapping[str, Any],
    bills: Mapping[str, Any],
    limits: Mapping[str, Any],
    copy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialise the tariff choice, including the risk default (INV-66).

    `copy` is the tariff itself - the preset's JSON with the household's own
    numbers (a template's steps, the contracted kW) - kept as `spec`, which the
    runtime builds from instead of the file: a preset edit or retirement in a
    later release can never move a live ceiling (D2 §6, §8; D-0520). The
    identity of what was chosen - preset, file and every version id - stays
    beside it so `preset_outdated` can be raised when the file moves on
    (D-0128). What the
    preset bills is not stored in words: the flow renders D2's `TariffSummary`
    whenever it is shown (HUB-12), and an older entry's `description` is ignored.
    """
    default = risk_key(default_risk(version.rules))
    chosen = str(answers.get("risk", default))
    risk = RISK_LABELS[chosen]
    return {
        "preset_id": spec.id,
        "preset_file": preset,
        "preset_name": spec.name,
        "version_ids": [item.version_id for item in spec.versions],
        "chosen_version_id": version.version_id,
        "currency": spec.currency,
        "target": answers.get("target", "auto"),
        "target_kw": answers.get("target_kw"),
        "risk": risk,
        # Whether the household picked the strictness or kept the default (D2 §6).
        "risk_source": "default" if chosen == default else "chosen",
        "eps_kwh": float(answers.get("eps_kwh", EPS_DEFAULT_KWH_PER_HOUR)),
        "cap_margin_kw": float(answers.get("cap_margin_kw", CAP_MARGIN_KW)),
        "bills": [value for _, value in sorted(bills.items()) if value is not None],
        "contracted_kw": [value for _, value in sorted(limits.items()) if value is not None],
        "spec": dict(copy) if copy is not None else None,
    }


# --------------------------------------------------------------------------- #
# presence, notifications (D8 §5.1) - the hard-limit step is gone (§5.15 S2)
# --------------------------------------------------------------------------- #


def people(hass: HomeAssistant) -> list[str]:
    """Return the house's `person` entities (from the registry, INV-3)."""
    return sorted(
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.domain == "person"
    ) or sorted(hass.states.async_entity_ids("person"))


def presence_schema(hass: HomeAssistant, *, values: Mapping[str, Any] | None = None) -> vol.Schema:
    """Auto from `person` entities, or manual (D8 §5.1); the persons follow on their own step.

    Manual never sees the persons (D8 §5.15 rule 5, review HUB-17); the delay
    before "away" is hours and minutes, not a box of minutes (CTL-8).
    """
    values = values or {}
    delay = as_duration(float(values.get("away_delay_min", 30)) * 60)
    fields: dict[Any, Any] = {
        vol.Optional(
            "mode", default=values.get("mode", "auto" if people(hass) else "manual")
        ): SelectSelector(
            SelectSelectorConfig(
                options=["auto", "manual"],
                mode=SelectSelectorMode.LIST,
                translation_key="presence_mode",
            )
        ),
    }
    fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(
        {vol.Optional("away_delay_min", default=delay): duration_selector()}
    )
    return vol.Schema(fields)


def persons_schema(hass: HomeAssistant, *, values: Mapping[str, Any] | None = None) -> vol.Schema:
    """Which persons automatic presence follows - asked only after "auto" (HUB-17)."""
    values = values or {}
    return vol.Schema(
        {
            vol.Optional("persons", default=values.get("persons") or people(hass)): EntitySelector(
                EntitySelectorConfig(domain="person", multiple=True)
            )
        }
    )


def notify_services(hass: HomeAssistant) -> list[str]:
    """Return the `notify.*` services this house has (D8 §5.8)."""
    return sorted(hass.services.async_services().get("notify", {}))


#: A companion app's notify service is `mobile_app_<its device name, slugified>`.
_MOBILE_APP: Final = "mobile_app"


def notify_label(hass: HomeAssistant, service: str, text: Text) -> str:
    """Return a notify service as the household knows it (review HUB-15).

    A phone's service is labelled with the phone's own device name; Home
    Assistant's own two services with a translated label; anything else with the
    service's name in words - never the raw key.
    """
    known = text.word("text", f"notify_{service}")
    if known:
        return known
    if service.startswith(f"{_MOBILE_APP}_"):
        slug = service.removeprefix(f"{_MOBILE_APP}_")
        devices = dr.async_get(hass)
        for entry in hass.config_entries.async_entries(_MOBILE_APP):
            if slugify(str(entry.data.get("device_name") or entry.title)) != slug:
                continue
            for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
                return device.name_by_user or device.name or entry.title
            return str(entry.data.get("device_name") or entry.title)
    return service.replace("_", " ").capitalize()


def _transport_field(category: str, default: str | None = None) -> tuple[Any, Any]:
    return (
        vol.Optional(category, default=default or NOTIFICATION_DEFAULTS[category]),
        SelectSelector(
            SelectSelectorConfig(
                options=list(TRANSPORTS),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="transport",
                sort=False,
            )
        ),
    )


def notifications_schema(
    hass: HomeAssistant,
    *,
    text: Text,
    values: Mapping[str, Any] | None = None,
    quiet: Sequence[str] | None = None,
) -> vol.Schema:
    """Per-category transport and quiet hours (D8 §5.1, §5.8).

    The three categories that ask something of the household are in the form; the
    eight that default to off are in the collapsed advanced section, because
    eleven selects abreast is the "overwhelming" HLD §7.9 exists to prevent
    (INV-65, D-0129). `values` is `notifications_data`'s own shape (one row per
    category), for a reconfigure's pre-fill.
    """
    values = values or {}

    def _default(category: str) -> str | None:
        row = values.get(category)
        return row.get("transport") if isinstance(row, Mapping) else None

    fields: dict[Any, Any] = dict(
        _transport_field(category, _default(category)) for category in PROMINENT_CATEGORIES
    )
    services = notify_services(hass)
    stored_service = next(
        (
            row["service"]
            for row in values.values()
            if isinstance(row, Mapping) and row.get("service")
        ),
        None,
    )
    if services:
        labelled = [
            SelectOptionDict(value=service, label=notify_label(hass, service, text))
            for service in services
        ]
        fields[vol.Optional("notify_service", default=stored_service or services[0])] = (
            SelectSelector(
                SelectSelectorConfig(options=labelled, mode=SelectSelectorMode.DROPDOWN, sort=False)
            )
        )
    quiet_start, quiet_end = tuple(quiet) if quiet else (None, None)
    start = (quiet_start or QUIET_START_DEFAULT)[:5]
    end = (quiet_end or QUIET_END_DEFAULT)[:5]
    # Half-hour selects: a time selector cannot hide its seconds (CTL-7, H8).
    fields[vol.Optional("quiet_start", default=start)] = time_selector(start)
    fields[vol.Optional("quiet_end", default=end)] = time_selector(end)
    # The rarely-changed categories last, after the questions that apply to all (HUB-16).
    fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(
        dict(
            _transport_field(category, _default(category))
            for category in NOTIFICATION_DEFAULTS
            if category not in PROMINENT_CATEGORIES
        )
    )
    return vol.Schema(fields)


def notifications_data(answers: Mapping[str, Any]) -> dict[str, Any]:
    """Materialise the policy for all eleven categories (D8 §4, §5.8)."""
    service = answers.get("notify_service")
    return {
        category: {
            "transport": answers.get(category, default),
            "service": service if answers.get(category, default) == "notify" else None,
        }
        for category, default in NOTIFICATION_DEFAULTS.items()
    }


def review_schema(*, reconfiguring: bool = False) -> vol.Schema:
    """Ask "Klar til å starte": one tick box, trial mode, ticked - on a first setup only.

    A reconfigure shows no toggle and keeps the site's own `active` (HUB-5): the
    switch on the home's device is where that changes.
    """
    if reconfiguring:
        return vol.Schema({})
    return vol.Schema({vol.Optional("start_in_observe", default=True): BooleanSelector()})


__all__ = [
    "AGREEMENT_NORGESPRIS",
    "CARRIER_FIXED",
    "CARRIER_SENSOR",
    "DEFAULT_SITE_NAME",
    "EXPORT_NONE",
    "FUSE_RATINGS",
    "METER_CHANGE",
    "METER_OK",
    "METER_SOURCE",
    "NO_PEAK_TARIFF",
    "PRESET_CUSTOM",
    "PRESET_UNKNOWN",
    "PRICE_SOURCES",
    "RISK_LABELS",
    "SECTION_PER_PHASE",
    "SOURCE_ENTITY",
    "SOURCE_FIXED",
    "SOURCE_NORDPOOL",
    "TARIFF_NO",
    "TARIFF_YES",
    "StepError",
    "agreements",
    "area_options",
    "bills_schema",
    "carrier_options_schema",
    "carriers_schema",
    "default_price_source",
    "discover_presets",
    "drop_suggested",
    "electrical_data",
    "electrical_profile",
    "electrical_schema",
    "export_amounts_schema",
    "export_schema",
    "fixed_price_schema",
    "limits_schema",
    "meter_confirm_schema",
    "meter_data",
    "meter_device_schema",
    "meter_found",
    "meter_roles_answers",
    "meter_roles_schema",
    "meter_values",
    "modifier_options_schema",
    "modifiers_schema",
    "name_schema",
    "needs_bills",
    "needs_limits",
    "nordpool_defaults",
    "nordpool_detected",
    "nordpool_schema",
    "nordpool_suggested",
    "notifications_data",
    "notifications_schema",
    "notify_label",
    "offered_modifiers",
    "peak_hours",
    "peak_of",
    "people",
    "persons_schema",
    "phase_limit_suggestion",
    "presence_schema",
    "preset_choice",
    "preset_file",
    "price_entity_schema",
    "price_source_schema",
    "resolve_timezone",
    "review_schema",
    "risk_key",
    "same_value",
    "tariff_confirm_schema",
    "tariff_data",
    "tariff_schema",
    "tariff_target_schema",
    "timezone_options",
    "timezone_schema",
    "validate_target",
]


# --------------------------------------------------------------------------- #
# The price by party (D13 §6): postcode, product, zone, gaps, state
# --------------------------------------------------------------------------- #


def postcode_schema(values: Mapping[str, Any] | None = None) -> vol.Schema:
    """Ask "Hva er postnummeret ditt?" - optional; empty skips it (D13 §6 step 0, O17)."""
    postcode = (values or {}).get("postcode")
    return vol.Schema(
        {
            vol.Optional("postcode", description={"suggested_value": postcode}): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            )
        }
    )


def choice_schema(field: str, options: Sequence[tuple[str, str]], chosen: str | None) -> vol.Schema:
    """One select of named options (a product, a tax zone), names as data (D13 steps 1a, 1b)."""
    return vol.Schema(
        {
            vol.Required(field, default=chosen or options[0][0]): SelectSelector(
                SelectSelectorConfig(
                    options=[SelectOptionDict(value=key, label=name) for key, name in options],
                    mode=SelectSelectorMode.LIST,
                    sort=False,
                )
            )
        }
    )


def questions_schema(questions: Sequence[Any], answers: Mapping[str, Any]) -> vol.Schema:
    """One field per gap the source left, its default pre-selected (D13 §5.6, step 1c)."""
    fields: dict[Any, Any] = {}
    for question in questions:
        value = answers.get(question.key, question.default)
        selector: Any = (
            NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any"))
            if isinstance(question.default, int | float)
            else TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
        )
        fields[vol.Required(question.key, default=value)] = selector
    return vol.Schema(fields)


def state_schema(
    schemes: Sequence[str],
    chosen: Sequence[str],
    *,
    ask_vat: bool = False,
    vat: float | None = None,
) -> vol.Schema:
    """Ask "Hvilke støtteordninger gjelder deg?" - the country's schemes only (D13 §6 step 3).

    `ask_vat` only where the country has no module or no national rate and no
    figure typed with the grid tariff asked it already (§9.1, step 1c-prime).
    """
    fields: dict[Any, Any] = {}
    if schemes:
        fields[vol.Optional("schemes", default=[key for key in chosen if key in schemes])] = (
            SelectSelector(
                SelectSelectorConfig(
                    options=list(schemes),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key="scheme",
                )
            )
        )
    if ask_vat:
        fields[vol.Required("vat", default=vat or 0)] = percent_selector(slider=False)
    return vol.Schema(fields)


def overrides_schema(values: Mapping[str, Any], currency: str) -> vol.Schema:
    """Return the state overrides a household that knows better may set (D13 O4, D8 §5.17)."""
    return vol.Schema(
        {
            vol.Optional("vat", description={"suggested_value": values.get("vat")}): (
                percent_selector(slider=False)
            ),
            vol.Optional("levy", description={"suggested_value": values.get("levy")}): (
                price_selector(currency)
            ),
        }
    )
