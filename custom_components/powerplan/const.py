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
CONF_HARD_LIMITS: Final = "hard_limits"
CONF_PRESENCE: Final = "presence"
CONF_NOTIFICATIONS: Final = "notifications"
CONF_QUIET_HOURS: Final = "quiet_hours"
CONF_ACTIVE: Final = "active"

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
