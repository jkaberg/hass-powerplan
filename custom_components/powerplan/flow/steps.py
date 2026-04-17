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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
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
    TimeSelector,
)
from homeassistant.util import dt as dt_util

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
from custom_components.powerplan.core.tariffs.grammar import PeakTariff, StepTable
from custom_components.powerplan.core.tariffs.presets import loader
from custom_components.powerplan.core.tariffs.target import (
    CAP_MARGIN_KW,
    EPS_DEFAULT_KWH_PER_HOUR,
    EPS_MAX_KWH,
    RISK_FLAT,
    RISK_FREE_RIDE,
    RISK_FULL,
    default_risk,
)
from custom_components.powerplan.providers.prices.formats import registry as formats
from custom_components.powerplan.providers.prices.nordpool_action import (
    AREAS,
    NORDPOOL_DOMAIN,
    NordpoolActionSource,
)

from .questionnaire import advanced_section, render

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.tariffs.grammar import TariffSpec, TariffVersion

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
    """Return every zone this system knows, sorted (reads tzdata: executor)."""
    zones = await hass.async_add_executor_job(zoneinfo.available_timezones)
    return sorted(zones)


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

#: D3 §6's list, extended upwards: a North-American service is rated 100–400 A
#: and D3 §6's own default for US is 200 A, which its list stopped short of
#: (`design/DECISIONS.md` D-0124).
FUSE_RATINGS: Final = (
    "16", "20", "25", "32", "35", "40", "50", "63",
    "80", "100", "125", "150", "200", "250", "320", "400",
)  # fmt: skip

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


def electrical_schema(
    *, country: str | None, values: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Return D3 §6's four plain questions and the advanced per-phase limit."""
    given = values or {}
    system = given.get("system") or default_system(country)
    phases = str(given.get("phases") or default_phases(country))
    fuse = str(given.get("main_fuse_a") or default_fuse_a(country))
    fields: dict[Any, Any] = {
        vol.Optional("country", default=given.get("country") or country or ""): CountrySelector(
            CountrySelectorConfig()
        ),
        vol.Optional("system", default=str(system)): SelectSelector(
            SelectSelectorConfig(
                options=[member.value for member in VoltageSystem],
                mode=SelectSelectorMode.LIST,
                translation_key="voltage_system",
                sort=False,
            )
        ),
        vol.Optional("phases", default=phases): SelectSelector(
            SelectSelectorConfig(
                options=["1", "3"], mode=SelectSelectorMode.LIST, translation_key="phases"
            )
        ),
        vol.Optional("main_fuse_a", default=fuse): SelectSelector(
            SelectSelectorConfig(
                options=list(FUSE_RATINGS), mode=SelectSelectorMode.DROPDOWN, sort=False
            )
        ),
    }
    fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(
        {
            vol.Optional("per_phase_limit_a", default=float(fuse)): NumberSelector(
                NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any")
            )
        }
    )
    return vol.Schema(fields)


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
    fuse = float(answers["main_fuse_a"])
    limit = float(answers.get("per_phase_limit_a") or fuse)
    for field, value in (("main_fuse_a", fuse), ("per_phase_limit_a", limit)):
        if not FUSE_MIN_A <= value <= FUSE_MAX_A:
            raise StepError(field, "fuse_out_of_range")
    phases = int(answers["phases"])
    if phases not in (1, 3):
        raise StepError("phases", "phases_not_available")
    profile = ElectricalProfile(
        system=VoltageSystem(answers["system"]),
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
    return {
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


# --------------------------------------------------------------------------- #
# meter (D3 §6)
# --------------------------------------------------------------------------- #

#: v1 reads the meter from ordinary HA sensors (`providers/meters/ha_sensors.py`).
METER_SOURCE: Final = "ha_sensors"

_ROLE_FILTERS: Final = {
    ROLE_GRID_POWER: ("power", None),
    ROLE_IMPORT_REGISTER: ("energy", None),
    ROLE_EXPORT_REGISTER: ("energy", None),
    ROLE_PRODUCTION_POWER: ("power", None),
    ROLE_METER_WINDOW: ("energy", None),
    ROLE_PHASE_L1: ("current", None),
    ROLE_PHASE_L2: ("current", None),
    ROLE_PHASE_L3: ("current", None),
}


def meter_device_schema(default: str | None) -> vol.Schema:
    """One optional device pick; leaving it empty binds entities by hand."""
    marker = vol.Optional("device", default=default) if default else vol.Optional("device")
    return vol.Schema({marker: DeviceSelector(DeviceSelectorConfig())})


def meter_roles_schema(prefilled: Mapping[str, str]) -> vol.Schema:
    """Return the seven roles of D3 §6, pre-filled where the registry could tell."""
    fields: dict[Any, Any] = {}
    for role in METER_ROLES:
        device_class, _ = _ROLE_FILTERS[role]
        found = prefilled.get(role)
        marker = vol.Optional(role, default=found) if found else vol.Optional(role)
        fields[marker] = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class=device_class)
        )
    return vol.Schema(fields)


def meter_data(
    hass: HomeAssistant, device_id: str | None, roles: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate the bound roles and materialise the meter section (INV-49)."""
    bound = {role: roles[role] for role in METER_ROLES if roles.get(role)}
    registry = er.async_get(hass)
    for role in (ROLE_GRID_POWER, ROLE_PRODUCTION_POWER):
        entity_id = bound.get(role)
        if entity_id is None:
            continue
        entry = registry.async_get(entity_id)
        if entry is not None and entry.unit_of_measurement not in (None, "W", "kW"):
            raise StepError(role, "power_unit_not_watts")
    return {"source": METER_SOURCE, "device_id": device_id, "roles": bound}


# --------------------------------------------------------------------------- #
# prices (D1 §6)
# --------------------------------------------------------------------------- #

SOURCE_NORDPOOL: Final = "nordpool_action"
SOURCE_ENTITY: Final = "entity"
SOURCE_FIXED: Final = "fixed"
PRICE_SOURCES: Final = (SOURCE_NORDPOOL, SOURCE_ENTITY, SOURCE_FIXED)

#: Where Nord Pool is the obvious answer (D1 §6, HLD §8).
_NORDPOOL_COUNTRIES: Final = (
    "NO", "SE", "FI", "DK", "EE", "LV", "LT", "NL", "BE", "DE", "LU", "FR", "AT",
)  # fmt: skip

#: D1 §6's pre-tick per country, intersected with what may be pre-ticked at all.
_RECOMMENDED: Final = {
    "NO": ("vat", "tou_schedule", "levy", "fixed_price"),
    "DK": ("vat", "tou_schedule", "levy"),
    "SE": ("vat", "tou_schedule"),
    "FI": ("vat", "tou_schedule"),
    "ES": ("vat", "tou_schedule"),
    "IT": ("vat", "tou_schedule"),
    "FR": ("vat", "tou_schedule"),
    "GB": ("vat", "tou_schedule"),
}


def default_price_source(country: str | None) -> str:
    """Return the source a country's houses usually have (D1 §6)."""
    return SOURCE_NORDPOOL if country in _NORDPOOL_COUNTRIES else SOURCE_FIXED


def price_source_schema(default: str) -> vol.Schema:
    """Return the three answers of D1 §6's first price question."""
    return vol.Schema(
        {
            vol.Optional("source", default=default): SelectSelector(
                SelectSelectorConfig(
                    options=list(PRICE_SOURCES),
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


def nordpool_schema(*, values: Mapping[str, Any]) -> vol.Schema:
    """Render the Nord Pool source from its own registry schema (D1 §6)."""
    return render(
        NordpoolActionSource.schema,
        translation_prefix="nordpool",
        values=values,
        overrides={
            "config_entry": ConfigEntrySelector(
                ConfigEntrySelectorConfig(integration=NORDPOOL_DOMAIN)
            )
        },
    )


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
                    options=list(formats.keys()), mode=SelectSelectorMode.DROPDOWN, sort=False
                )
            ),
        }
    )


def fixed_price_schema(currency: str) -> vol.Schema:
    """One number: what a kWh costs when nothing publishes a curve (D1 §6)."""
    return vol.Schema(
        {
            vol.Optional("price", default=0.0): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement=f"{currency}/kWh"
                )
            )
        }
    )


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


def modifiers_schema(country: str | None, chosen: Sequence[str] | None = None) -> vol.Schema:
    """Every registered modifier, with the country's pre-ticked (D1 §6)."""
    # `modifiers.keys()` is the registry function, not a mapping method.
    available = [key for key in modifiers.keys() if key != "export_price"]  # noqa: SIM118
    recommended = _RECOMMENDED.get(country or "", ())
    default = (
        list(chosen)
        if chosen is not None
        else [key for key in available if key in recommended and pre_tickable(key)]
    )
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


def modifier_options_schema(key: str) -> vol.Schema:
    """Render one modifier's options from its registry schema (D1 §6)."""
    return render(modifiers.entry(key).schema, translation_prefix=f"modifier_{key}")


EXPORT_NONE: Final = "none"


def export_schema() -> vol.Schema:
    """Return D1 §6's export question, from `export_price`'s own schema."""
    schema = modifiers.entry("export_price").schema
    mode_field = next(field for field in schema if field.key == "mode")
    options = [EXPORT_NONE, *(str(option) for option in mode_field.options)]
    rendered = render(
        tuple(field for field in schema if field.key != "mode"),
        translation_prefix="export",
    )
    fields: dict[Any, Any] = {
        vol.Optional("mode", default=EXPORT_NONE): SelectSelector(
            SelectSelectorConfig(
                options=options,
                mode=SelectSelectorMode.LIST,
                translation_key="export_mode",
                sort=False,
            )
        )
    }
    fields.update(rendered.schema)
    return vol.Schema(fields)


CARRIER_FIXED: Final = "fixed"
CARRIER_SENSOR: Final = "sensor"


def carriers_schema() -> vol.Schema:
    """Which other carriers the house buys (D1 §6); none by default."""
    options = [str(carrier) for carrier in Carrier if carrier is not Carrier.ELECTRICITY]
    return vol.Schema(
        {
            vol.Optional("carriers", default=[]): SelectSelector(
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
    """One carrier's price: a fixed number, or a sensor read daily (D1 §6)."""
    return vol.Schema(
        {
            vol.Optional("mode", default=CARRIER_FIXED): SelectSelector(
                SelectSelectorConfig(
                    options=[CARRIER_FIXED, CARRIER_SENSOR],
                    mode=SelectSelectorMode.LIST,
                    translation_key="carrier_mode",
                )
            ),
            vol.Optional("price", default=0.0): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement=f"{currency}/kWh"
                )
            ),
            vol.Optional("entity_id"): EntitySelector(EntitySelectorConfig(domain="sensor")),
        }
    )


# --------------------------------------------------------------------------- #
# tariff (D2 §6)
# --------------------------------------------------------------------------- #

PRESET_UNKNOWN: Final = "unknown"
PRESET_CUSTOM: Final = "custom"

#: What "I don't know / not listed" falls back to, per country: the country's
#: generic preset where one ships, and no capacity component where none does.
_GENERIC_PRESET: Final = {"NO": "no/generic-top3"}

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
            spec = loader.load(stem)
        except loader.PresetError:
            _LOGGER.exception("shipped preset %s does not validate and is not offered", stem)
            continue
        found.append((stem, spec.name))
    return found


def tariff_schema(
    *, country: str | None, presets: Sequence[tuple[str, str]], chosen: str | None
) -> vol.Schema:
    """Country plus the preset select of D2 §6, with its two escape hatches."""
    options = [SelectOptionDict(value=stem, label=name) for stem, name in presets]
    options.append(SelectOptionDict(value=PRESET_UNKNOWN, label="I don't know / not listed"))
    options.append(SelectOptionDict(value=PRESET_CUSTOM, label="Custom — I'll describe it"))
    default = chosen or (presets[0][0] if len(presets) == 1 else PRESET_UNKNOWN)
    return vol.Schema(
        {
            vol.Optional("country", default=country or ""): CountrySelector(
                CountrySelectorConfig()
            ),
            vol.Optional("preset", default=default): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN, sort=False)
            ),
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


def target_options(version: TariffVersion) -> list[SelectOptionDict]:
    """`automatic`, then one option per step with its fee (D2 §6)."""
    options = [SelectOptionDict(value="auto", label="Automatic — defend the step I am in")]
    peak = peak_of(version)
    if peak is None or not isinstance(peak.pricing, StepTable):
        return options
    for index, step in enumerate(peak.pricing.steps):
        fee = step.fee_per_period
        options.append(
            SelectOptionDict(
                value=f"step:{index}",
                label=f"{step.name} — {fee.amount:f} {fee.currency} per month",
            )
        )
    return options


RISK_LABELS: Final = {"flat": RISK_FLAT, "free_ride": RISK_FREE_RIDE, "full": RISK_FULL}


def risk_key(risk: float) -> str:
    """Return the select value one risk number means."""
    for key, value in RISK_LABELS.items():
        if value == risk:
            return key
    return "flat"


def tariff_target_schema(version: TariffVersion) -> vol.Schema:
    """Target, risk, and the two advanced numbers of D2 §6."""
    peak = peak_of(version)
    default = default_risk(version.grammar)
    fields: dict[Any, Any] = {
        vol.Optional("target", default="auto"): SelectSelector(
            SelectSelectorConfig(
                options=target_options(version), mode=SelectSelectorMode.DROPDOWN, sort=False
            )
        ),
        vol.Optional("risk", default=risk_key(default)): SelectSelector(
            SelectSelectorConfig(
                options=list(RISK_LABELS),
                mode=SelectSelectorMode.LIST,
                translation_key="risk",
                sort=False,
            )
        ),
    }
    if peak is not None and not isinstance(peak.pricing, StepTable):
        fields[vol.Optional("target_kw", default=5.0)] = NumberSelector(
            NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW")
        )
    fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(
        {
            vol.Optional("eps_kwh", default=EPS_DEFAULT_KWH_PER_HOUR): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kWh"
                )
            ),
            vol.Optional("cap_margin_kw", default=CAP_MARGIN_KW): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW"
                )
            ),
        }
    )
    return vol.Schema(fields)


def validate_target(answers: Mapping[str, Any]) -> None:
    """D2 §6: an ε above 2 kWh is watts wearing a kWh label (INV-49)."""
    eps = answers.get("eps_kwh")
    if eps is not None and float(eps) > EPS_MAX_KWH:
        raise StepError("eps_kwh", "eps_looks_like_watts")
    if eps is not None and float(eps) <= 0:
        raise StepError("eps_kwh", "eps_not_positive")


def needs_bills(version: TariffVersion) -> bool:
    """Return whether the preset bills on a rolling window (D2 §6)."""
    peak = peak_of(version)
    return peak is not None and peak.period == "rolling_months"


def bills_schema(peak: PeakTariff) -> vol.Schema:
    """Return the last twelve monthly metrics, all of them optional (D2 §6)."""
    months = peak.rolling_months
    return vol.Schema(
        {
            vol.Optional(f"month_{index + 1}"): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW"
                )
            )
            for index in range(months)
        }
    )


def needs_limits(version: TariffVersion) -> bool:
    """Return whether the preset contracts a power limit per period (D2 §6)."""
    return version.contracted is not None


def limits_schema(version: TariffVersion) -> vol.Schema:
    """One number per contracted period, pre-filled from the preset (D2 §6)."""
    contracted = version.contracted
    assert contracted is not None
    unit = "kVA" if contracted.unit == "kva" else "kW"
    return vol.Schema(
        {
            vol.Optional(f"limit_{index + 1}", default=limit.limit_kw): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement=unit
                )
            )
            for index, limit in enumerate(contracted.limits)
        }
    )


def tariff_data(
    *,
    preset: str,
    spec: TariffSpec,
    version: TariffVersion,
    answers: Mapping[str, Any],
    bills: Mapping[str, Any],
    limits: Mapping[str, Any],
    description: str,
) -> dict[str, Any]:
    """Materialise the tariff choice, including the risk default (INV-66).

    The grammar itself is not copied here: D2 §8 has D7 copy the spec into the
    **site store** at setup, which is what makes a preset edit in a later release
    unable to move a live ceiling. The entry holds the identity of what was
    chosen - preset, file and every version id - so that copy can be checked
    against it and `preset_outdated` raised when they differ (D-0128).
    """
    risk = RISK_LABELS[answers.get("risk", risk_key(default_risk(version.grammar)))]
    return {
        "preset_id": spec.id,
        "preset_file": preset,
        "preset_name": spec.name,
        "version_ids": [item.version_id for item in spec.versions],
        "chosen_version_id": version.version_id,
        "currency": spec.currency,
        "description": description,
        "target": answers.get("target", "auto"),
        "target_kw": answers.get("target_kw"),
        "risk": risk,
        "risk_source": "per_day_max" if risk == RISK_FREE_RIDE else "flat_default",
        "eps_kwh": float(answers.get("eps_kwh", EPS_DEFAULT_KWH_PER_HOUR)),
        "cap_margin_kw": float(answers.get("cap_margin_kw", CAP_MARGIN_KW)),
        "bills": [value for _, value in sorted(bills.items()) if value is not None],
        "contracted_kw": [value for _, value in sorted(limits.items()) if value is not None],
    }


# --------------------------------------------------------------------------- #
# hard limits, presence, notifications (D8 §5.1)
# --------------------------------------------------------------------------- #


def hard_limits_schema(profile: ElectricalProfile, contracted: float | None) -> vol.Schema:
    """Return the total the connection may draw, defaulted from the fuse."""
    default = contracted if contracted is not None else round(profile.fuse_w() / 1000.0, 1)
    return vol.Schema(
        {
            vol.Optional("contracted_kw", default=default): NumberSelector(
                NumberSelectorConfig(
                    mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW"
                )
            )
        }
    )


def presence_schema(hass: HomeAssistant) -> vol.Schema:
    """Auto from `person` entities, or manual (D8 §5.1)."""
    people = sorted(
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.domain == "person"
    ) or sorted(hass.states.async_entity_ids("person"))
    fields: dict[Any, Any] = {
        vol.Optional("mode", default="auto" if people else "manual"): SelectSelector(
            SelectSelectorConfig(
                options=["auto", "manual"],
                mode=SelectSelectorMode.LIST,
                translation_key="presence_mode",
            )
        ),
        vol.Optional("persons", default=people): EntitySelector(
            EntitySelectorConfig(domain="person", multiple=True)
        ),
    }
    fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(
        {
            vol.Optional("away_delay_min", default=30): NumberSelector(
                NumberSelectorConfig(mode=NumberSelectorMode.BOX, step=1, unit_of_measurement="min")
            )
        }
    )
    return vol.Schema(fields)


def notify_services(hass: HomeAssistant) -> list[str]:
    """Return the `notify.*` services this house has (D8 §5.8)."""
    return sorted(hass.services.async_services().get("notify", {}))


def _transport_field(category: str) -> tuple[Any, Any]:
    return (
        vol.Optional(category, default=NOTIFICATION_DEFAULTS[category]),
        SelectSelector(
            SelectSelectorConfig(
                options=list(TRANSPORTS),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="transport",
                sort=False,
            )
        ),
    )


def notifications_schema(hass: HomeAssistant) -> vol.Schema:
    """Per-category transport and quiet hours (D8 §5.1, §5.8).

    The three categories that ask something of the household are in the form; the
    eight that default to off are in the collapsed advanced section, because
    eleven selects abreast is the "overwhelming" HLD §7.9 exists to prevent
    (INV-65, D-0129).
    """
    fields: dict[Any, Any] = dict(_transport_field(category) for category in PROMINENT_CATEGORIES)
    fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(
        dict(
            _transport_field(category)
            for category in NOTIFICATION_DEFAULTS
            if category not in PROMINENT_CATEGORIES
        )
    )
    services = notify_services(hass)
    if services:
        fields[vol.Optional("notify_service", default=services[0])] = SelectSelector(
            SelectSelectorConfig(options=services, mode=SelectSelectorMode.DROPDOWN, sort=False)
        )
    fields[vol.Optional("quiet_start", default=QUIET_START_DEFAULT)] = TimeSelector()
    fields[vol.Optional("quiet_end", default=QUIET_END_DEFAULT)] = TimeSelector()
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


def review_schema() -> vol.Schema:
    """One tick box: start in observe, and it starts ticked (PLAN §7 dec. 20)."""
    return vol.Schema({vol.Optional("start_in_observe", default=True): BooleanSelector()})


__all__ = [
    "CARRIER_FIXED",
    "CARRIER_SENSOR",
    "DEFAULT_SITE_NAME",
    "EXPORT_NONE",
    "FUSE_RATINGS",
    "METER_SOURCE",
    "NO_PEAK_TARIFF",
    "PRESET_CUSTOM",
    "PRESET_UNKNOWN",
    "PRICE_SOURCES",
    "RISK_LABELS",
    "SOURCE_ENTITY",
    "SOURCE_FIXED",
    "SOURCE_NORDPOOL",
    "StepError",
    "bills_schema",
    "carrier_options_schema",
    "carriers_schema",
    "default_price_source",
    "discover_presets",
    "electrical_data",
    "electrical_profile",
    "electrical_schema",
    "export_schema",
    "fixed_price_schema",
    "hard_limits_schema",
    "limits_schema",
    "meter_data",
    "meter_device_schema",
    "meter_roles_schema",
    "modifier_options_schema",
    "modifiers_schema",
    "name_schema",
    "needs_bills",
    "needs_limits",
    "nordpool_defaults",
    "nordpool_schema",
    "notifications_data",
    "notifications_schema",
    "peak_of",
    "presence_schema",
    "preset_file",
    "price_entity_schema",
    "price_source_schema",
    "resolve_timezone",
    "review_schema",
    "risk_key",
    "tariff_data",
    "tariff_schema",
    "tariff_target_schema",
    "timezone_options",
    "timezone_schema",
    "validate_target",
]
