"""Constants shared across the powerplan integration.

The `CONF_*` keys are the keys of `entry.data` (D8 §4 `SiteData`): what the site
flow materialises so that behaviour never changes behind the user's back
(INV-66). Nothing here is a timezone, a currency or a country: those three are
derived from the Home Assistant environment at creation and stored (D-0120).
"""

from enum import StrEnum
from typing import Final

DOMAIN: Final = "powerplan"


class OnboardingPath(StrEnum):
    """Which axes a site steers on (HLD §4, D8 §4)."""

    FULL = "full"
    PRICE_ONLY = "price_only"
    FUSE_ONLY = "fuse_only"


# --------------------------------------------------------------------------- #
# entry.data keys (D8 §4)
# --------------------------------------------------------------------------- #

CONF_PATH: Final = "path"
CONF_NAME: Final = "name"
CONF_TIMEZONE: Final = "timezone"
CONF_TIMEZONE_SOURCE: Final = "timezone_source"
CONF_CURRENCY: Final = "currency"
CONF_ELECTRICAL: Final = "electrical"
CONF_METER: Final = "meter"
CONF_PRICES: Final = "prices"
CONF_TARIFF: Final = "tariff"
CONF_PRESENCE: Final = "presence"
CONF_NOTIFICATIONS: Final = "notifications"
CONF_QUIET_HOURS: Final = "quiet_hours"
CONF_ACTIVE: Final = "active"

#: Where the brand icon is served: an appliance entity on a hardware device shows
#: it as its picture, so it reads as PowerPlan's among the device's own rows
#: (D8 §5.16, D-0418). Home Assistant's own `/api/brands/…` needs a signed token
#: an entity picture cannot carry.
BRAND_ICON_URL: Final = "/powerplan_static/icon.png"

#: The user pages every link points into (D8 §5.13): a flow placeholder, a
#: repair's `learn_more_url`. A URL lives here, never in a translation string.
DOCS_URL: Final = "https://github.com/jkaberg/hass-powerplan/blob/main/docs"

# --------------------------------------------------------------------------- #
# Load subentries (D8 §4 `LoadSubentryData`, §5.2)
# --------------------------------------------------------------------------- #
SUBENTRY_LOAD: Final = "load"
LOAD_TYPE: Final = "type"
LOAD_PROFILE: Final = "profile"
LOAD_DEVICE_ID: Final = "device_id"
LOAD_BINDINGS: Final = "bindings"
LOAD_PARAMS: Final = "params"
LOAD_DERIVATION_VERSION: Final = "derivation_version"
LOAD_STRATEGY: Final = "strategy"
LOAD_PRIORITY: Final = "priority"
LOAD_MANUAL_OVERRIDES: Final = "manual_overrides"
#: Set once the household renames the appliance in the gear flow; until then the
#: title follows the hardware device's name (D8 §5.16, rename rule).
LOAD_TITLE_USER_SET: Final = "title_user_set"
#: Low, normal, high - the numbers the allocator reads (D-0411, D-0422).
PRIORITY_LEVELS: Final[dict[str, int]] = {"low": 15, "normal": 30, "high": 45}
_THERMAL_SETTINGS: Final = frozenset(
    {"comfort_c", "min_c", "comfort_min_c", "floor_c", "max_c", "follow_presence"}
)
#: The answers and parameters an appliance entity owns once the appliance exists
#: (levels 1–2, D8 §5.16): the gear flow reads them back and never asks or
#: re-derives them (amended INV-66).
ENTITY_SETTINGS: Final[dict[str, frozenset[str]]] = {
    "ev": frozenset({"target_soc", "min_soc_now", "force_max_h"}),
    "generic_switch": frozenset({"hours_per_day", "force_max_h"}),
    "water_heater": _THERMAL_SETTINGS | {"ready_by", "force_max_h"},
    "appliance_cycle": frozenset({"ready_by"}),
    "floor_heating": _THERMAL_SETTINGS,
    "heat_pump": _THERMAL_SETTINGS,
    "radiator": _THERMAL_SETTINGS,
    "battery": frozenset(),
}


def priority_level(priority: int) -> str:
    """Return the level a priority number reads as (thresholds 22.5 and 37.5, D-0411)."""
    return min(PRIORITY_LEVELS, key=lambda level: abs(PRIORITY_LEVELS[level] - priority))


# --------------------------------------------------------------------------- #
# Circuit subentries (D6 §6 `CircuitSubentryData`, D8 §5.3)
# --------------------------------------------------------------------------- #
SUBENTRY_CIRCUIT: Final = "circuit"
CIRCUIT_FUSE_A: Final = "fuse_a"
CIRCUIT_PHASES: Final = "phases"
CIRCUIT_MEMBERS: Final = "members"
CIRCUIT_SUB_METER: Final = "sub_meter"
CIRCUIT_UNMETERED_W: Final = "unmetered_w"

# --------------------------------------------------------------------------- #
# Group subentries (D6 §6 `GroupSubentryData`, D8 §5.3)
# --------------------------------------------------------------------------- #
SUBENTRY_GROUP: Final = "group"
GROUP_MEMBERS: Final = "members"
GROUP_MAX_CONCURRENT_W: Final = "max_concurrent_w"
GROUP_FROM_STAGE: Final = "from_stage"
GROUP_CEILING_FRACTION: Final = "ceiling_fraction"
GROUP_STARVE_SECONDS: Final = "starve_seconds"

# --------------------------------------------------------------------------- #
# Zone subentries (D6 §6 `ZoneSubentryData`, D8 §5.3)
# --------------------------------------------------------------------------- #
SUBENTRY_ZONE: Final = "zone"
ZONE_MEMBERS: Final = "members"
ZONE_NEVER_SUBSTITUTE: Final = "never_substitute"
ZONE_MIN_COP: Final = "min_cop"
ZONE_SWITCH_HYSTERESIS: Final = "switch_hysteresis"
ZONE_MIN_DWELL_MIN: Final = "min_dwell_min"
ZONE_SWITCH_CONFIRM_S: Final = "switch_confirm_s"
ZONE_CAPACITY_PENALTY: Final = "capacity_penalty"

#: The collapsed section every step puts its advanced fields in. Home Assistant
#: deprecated `show_advanced_options` in 2026.9 and asks for a section instead;
#: a collapsed section is also a better reading of INV-65, because the field is
#: there to be found rather than hidden behind a profile setting (D-0129).
SECTION_ADVANCED: Final = "advanced"

#: Where the site timezone came from: the environment, or the fallback step.
TIMEZONE_FROM_HASS: Final = "hass"
TIMEZONE_FROM_USER: Final = "user"

# --------------------------------------------------------------------------- #
# The meter's seven roles (D3 §6, `providers/meters/ha_sensors.py`)
# --------------------------------------------------------------------------- #

ROLE_GRID_POWER: Final = "grid_power"
ROLE_IMPORT_REGISTER: Final = "import_register"
ROLE_EXPORT_REGISTER: Final = "export_register"
ROLE_PRODUCTION_POWER: Final = "production_power"
ROLE_METER_WINDOW: Final = "meter_window"
ROLE_PHASE_L1: Final = "phase_current_l1"
ROLE_PHASE_L2: Final = "phase_current_l2"
ROLE_PHASE_L3: Final = "phase_current_l3"

METER_ROLES: Final = (
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
    ROLE_EXPORT_REGISTER,
    ROLE_PRODUCTION_POWER,
    ROLE_METER_WINDOW,
    ROLE_PHASE_L1,
    ROLE_PHASE_L2,
    ROLE_PHASE_L3,
)

# --------------------------------------------------------------------------- #
# Notifications (D8 §5.8)
# --------------------------------------------------------------------------- #

TRANSPORT_OFF: Final = "off"
TRANSPORT_PERSISTENT: Final = "persistent"
TRANSPORT_NOTIFY: Final = "notify"
TRANSPORTS: Final = (TRANSPORT_OFF, TRANSPORT_PERSISTENT, TRANSPORT_NOTIFY)

#: D8 §5.8's eleven categories, with D8 §5.1's defaults: a persistent
#: notification for the three that need an answer from the household, off for the
#: rest. The eight that default to off are shown only in advanced mode (INV-65).
NOTIFICATION_DEFAULTS: Final = {
    "peak_warning": TRANSPORT_PERSISTENT,
    "comfort_violation": TRANSPORT_PERSISTENT,
    "device_unhealthy": TRANSPORT_PERSISTENT,
    "peak_uncontrolled": TRANSPORT_OFF,
    "deadline_at_risk": TRANSPORT_OFF,
    "level_up": TRANSPORT_OFF,
    "price_source_dead": TRANSPORT_OFF,
    "legionella_at_risk": TRANSPORT_OFF,
    "safe_mode": TRANSPORT_OFF,
    "force_expired": TRANSPORT_OFF,
    "prices_daily_summary": TRANSPORT_OFF,
}

#: The three categories every site is asked about (D8 §5.1).
PROMINENT_CATEGORIES: Final = ("peak_warning", "comfort_violation", "device_unhealthy")

#: D8 §2: quiet hours hold the non-urgent categories.
QUIET_START_DEFAULT: Final = "22:00:00"
QUIET_END_DEFAULT: Final = "07:00:00"

#: The postcode the household gave (D13 §6 step 0, O17): sent only to the country's
#: official directory, stored for a reconfigure, never in diagnostics (D8 §5.17).
CONF_POSTCODE: Final = "postcode"
