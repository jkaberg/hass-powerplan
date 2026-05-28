"""Site config flow (D8 §5.1).

The order of the steps is HLD §4's onboarding paths made concrete: a menu, a
name, the connection, then only the parts the chosen path uses, and a review that
says what powerplan derived before anything is saved (INV-67). Each step is
short, every answer has a default, and what the flow works out - D3's watts per
amp, the preset and its versions, the risk default, the timezone - is written
into `entry.data` so a later release cannot change this site's behaviour behind
the household's back (INV-66).

Three things are deliberate:

* **the site starts in observe.** `active` is `False`: every decision is computed
  and published, every would-be write is logged, and nothing is actuated until
  the household turns the site on (PLAN §7 dec. 20).
* **the timezone is derived, never typed.** It is `hass.config.time_zone`,
  materialised at creation. The step exists only for the house where Home
  Assistant has no usable zone (`design/DECISIONS.md` D-0120).
* **rendering comes from the registries.** A price source, a modifier and a
  tariff preset each describe themselves; adding one is a module, not a step here
  (D1 §6, D2 §6).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTIVE,
    CONF_CURRENCY,
    CONF_ELECTRICAL,
    CONF_HARD_LIMITS,
    CONF_METER,
    CONF_NAME,
    CONF_NOTIFICATIONS,
    CONF_PATH,
    CONF_PRESENCE,
    CONF_PRICES,
    CONF_QUIET_HOURS,
    CONF_TARIFF,
    CONF_TIMEZONE,
    CONF_TIMEZONE_SOURCE,
    DOMAIN,
    QUIET_END_DEFAULT,
    QUIET_START_DEFAULT,
    ROLE_GRID_POWER,
    ROLE_IMPORT_REGISTER,
    SECTION_ADVANCED,
    SUBENTRY_CIRCUIT,
    SUBENTRY_LOAD,
    TIMEZONE_FROM_HASS,
    TIMEZONE_FROM_USER,
    OnboardingPath,
)
from .core.pricing import modifiers
from .core.tariffs.presets import loader
from .flow import device_pick, review, steps
from .flow.circuit import CircuitSubentryFlow
from .flow.load import LoadSubentryFlow
from .flow.questionnaire import store_value, value_of
from .providers.prices.formats import registry as formats
from .providers.prices.nordpool_action import NordpoolActionSource

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry, ConfigSubentryFlow

    from .core.metering.profile import ElectricalProfile
    from .core.tariffs.grammar import TariffSpec, TariffVersion

_LOGGER = logging.getLogger(__name__)


class PowerplanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for one site (D8 §5.1)."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Start with an empty site and the environment's own answers."""
        self._path = OnboardingPath.FULL
        self._name = steps.DEFAULT_SITE_NAME
        self._timezone: str | None = None
        self._timezone_source = TIMEZONE_FROM_HASS
        self._zones: list[str] = []
        self._electrical: dict[str, Any] = {}
        self._profile: ElectricalProfile | None = None
        self._meter_device: str | None = None
        self._meter: dict[str, Any] | None = None
        self._price_source = steps.SOURCE_NORDPOOL
        self._sources: list[dict[str, Any]] = []
        self._modifier_keys: list[str] = []
        self._modifier_index = 0
        self._modifiers: list[dict[str, Any]] = []
        self._export: dict[str, Any] | None = None
        self._carrier_keys: list[str] = []
        self._carrier_index = 0
        self._carriers: list[dict[str, Any]] = []
        self._presets: list[tuple[str, str]] = []
        self._preset_file: str | None = None
        self._spec: TariffSpec | None = None
        self._version: TariffVersion | None = None
        self._description = ""
        self._target: dict[str, Any] = {}
        self._bills: dict[str, Any] = {}
        self._limits: dict[str, Any] = {}
        self._hard_limits: dict[str, Any] = {}
        self._presence: dict[str, Any] = {}
        self._notifications: dict[str, Any] = {}
        self._quiet: list[str] = []

    # ----------------------------------------------------------------- helpers

    @property
    def _country(self) -> str | None:
        """The site's country: the electrical step's answer, else the house's."""
        return self._electrical.get("country") or self.hass.config.country

    @property
    def _currency(self) -> str:
        """The site's currency, from the Home Assistant environment."""
        return self.hass.config.currency

    @staticmethod
    def _flat(user_input: Mapping[str, Any]) -> dict[str, Any]:
        """Return the step's answers with the advanced section merged up.

        A collapsed section arrives nested; every consumer downstream wants one
        flat mapping, and a field is either advanced or not - never both.
        """
        merged = {key: value for key, value in user_input.items() if key != SECTION_ADVANCED}
        merged.update(user_input.get(SECTION_ADVANCED) or {})
        return merged

    def _form(
        self,
        step_id: str,
        schema: vol.Schema,
        *,
        errors: Mapping[str, str] | None = None,
        placeholders: Mapping[str, str] | None = None,
        last_step: bool = False,
    ) -> ConfigFlowResult:
        """Show one step. Nothing but the review is ever the last step."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=dict(errors) if errors else None,
            description_placeholders=dict(placeholders) if placeholders else None,
            last_step=last_step,
        )

    async def _local_today(self) -> Any:
        """Today in the site's timezone - the date a tariff version is read at."""
        zone = await dt_util.async_get_time_zone(self._timezone or "")
        now = dt_util.utcnow()
        return (now.astimezone(zone) if zone is not None else now).date()

    # -------------------------------------------------------------------- user

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose an onboarding path (HLD §4): full, price only or fuse only."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[path.value for path in OnboardingPath],
        )

    async def async_step_full(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Price and capacity: the whole engine."""
        self._path = OnboardingPath.FULL
        return await self.async_step_name()

    async def async_step_price_only(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Price only: no meter, and therefore no capacity component (`NoPeak`)."""
        self._path = OnboardingPath.PRICE_ONLY
        return await self.async_step_name()

    async def async_step_fuse_only(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fuse only: dynamic load balancing, nothing priced."""
        self._path = OnboardingPath.FUSE_ONLY
        return await self.async_step_name()

    # -------------------------------------------------------------------- name

    async def async_step_name(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Name the site."""
        if user_input is None:
            return self._form("name", steps.name_schema(self._name))
        self._name = user_input[CONF_NAME] or steps.DEFAULT_SITE_NAME

        self._timezone = await steps.resolve_timezone(self.hass)
        if self._timezone is None:
            self._zones = await steps.timezone_options(self.hass)
            return await self.async_step_timezone()
        self._timezone_source = TIMEZONE_FROM_HASS
        _LOGGER.debug("site timezone %s taken from hass.config", self._timezone)
        return await self.async_step_electrical()

    async def async_step_timezone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the timezone - only because Home Assistant has no usable one.

        The one question in the flow with no default: there is nothing left to
        derive it from, which is exactly why it is being asked (D-0120).
        """
        if user_input is None:
            return self._form("timezone", steps.timezone_schema(self._zones, None))
        self._timezone = user_input["timezone"]
        self._timezone_source = TIMEZONE_FROM_USER
        _LOGGER.info("site timezone %s answered in the flow; hass.config has none", self._timezone)
        return await self.async_step_electrical()

    # -------------------------------------------------------------- electrical

    async def async_step_electrical(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the connection: country, system, phases, main fuse (D3 §6)."""
        if user_input is None:
            return self._form(
                "electrical",
                steps.electrical_schema(country=self._country, values=self._electrical),
            )
        answers = self._flat(user_input)
        try:
            profile = steps.electrical_profile(answers)
        except steps.StepError as err:
            return self._form(
                "electrical",
                steps.electrical_schema(country=answers.get("country"), values=answers),
                errors={err.field: err.key},
            )
        self._profile = profile
        self._electrical = steps.electrical_data(answers, profile)
        if self._path is OnboardingPath.PRICE_ONLY:
            return await self.async_step_prices()
        return await self.async_step_meter()

    # ------------------------------------------------------------------- meter

    async def async_step_meter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the meter's device, so the seven roles can be pre-filled (D3 §6)."""
        if user_input is None:
            return self._form("meter", steps.meter_device_schema(self._meter_device))
        self._meter_device = user_input.get("device")
        return await self.async_step_meter_roles()

    async def async_step_meter_roles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the seven roles of D3 §6; every one of them is optional."""
        if user_input is None:
            prefilled = device_pick.prefill_roles(self.hass, self._meter_device)
            bound = (self._meter or {}).get("roles") or prefilled
            return self._form("meter_roles", steps.meter_roles_schema(bound))
        try:
            self._meter = steps.meter_data(self.hass, self._meter_device, user_input)
        except steps.StepError as err:
            return self._form(
                "meter_roles",
                steps.meter_roles_schema(
                    {key: value for key, value in user_input.items() if isinstance(value, str)}
                ),
                errors={err.field: err.key},
            )
        if self._path is OnboardingPath.FUSE_ONLY:
            return await self.async_step_hard_limits()
        return await self.async_step_prices()

    # ------------------------------------------------------------------ prices

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Where the electricity price comes from (D1 §6)."""
        if user_input is None:
            return self._form(
                "prices", steps.price_source_schema(steps.default_price_source(self._country))
            )
        self._price_source = user_input["source"]
        match self._price_source:
            case steps.SOURCE_ENTITY:
                return await self.async_step_prices_entity()
            case steps.SOURCE_FIXED:
                return await self.async_step_prices_fixed()
            case _:
                return await self.async_step_prices_nordpool()

    async def async_step_prices_nordpool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nord Pool, rendered from the source's own schema (D1 §6)."""
        if user_input is None:
            return self._form(
                "prices_nordpool",
                steps.nordpool_schema(values=steps.nordpool_defaults(self.hass, self._currency)),
            )
        self._sources = [
            {
                "key": steps.SOURCE_NORDPOOL,
                "options": value_of(NordpoolActionSource.schema, self._flat(user_input)),
            }
        ]
        return await self.async_step_modifiers()

    async def async_step_prices_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind a price sensor the house already has, format detected (D1 §6)."""
        if user_input is None:
            return self._form(
                "prices_entity",
                steps.price_entity_schema(default_entity=None, default_format=None),
            )
        entity_id = user_input.get("entity_id")
        detected = None
        if entity_id is not None:
            platform = device_pick.price_entity_platform(self.hass, entity_id)
            candidates = formats.for_platform(platform) if platform else ()
            detected = candidates[0] if candidates else None
        self._sources = [
            {
                "key": steps.SOURCE_ENTITY,
                "options": {
                    "entity_id": entity_id,
                    "format": user_input.get("format") or detected,
                    "detected_format": detected,
                },
            }
        ]
        return await self.async_step_modifiers()

    async def async_step_prices_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a fixed price per kWh, for a house with no curve at all (D1 §6)."""
        if user_input is None:
            return self._form("prices_fixed", steps.fixed_price_schema(self._currency))
        self._sources = [
            {
                "key": steps.SOURCE_FIXED,
                "options": {"price": str(user_input["price"]), "currency": self._currency},
            }
        ]
        return await self.async_step_modifiers()

    async def async_step_modifiers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what is added on top of the raw price, from the registry (D1 §6)."""
        if user_input is None:
            chosen = self._modifier_keys or None
            return self._form("modifiers", steps.modifiers_schema(self._country, chosen))
        self._modifier_keys = list(user_input.get("modifiers") or [])
        self._modifier_index = 0
        self._modifiers = []
        return await self._next_modifier()

    async def _next_modifier(self) -> ConfigFlowResult:
        if self._modifier_index < len(self._modifier_keys):
            return await self.async_step_modifier_options()
        return await self.async_step_export()

    async def async_step_modifier_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One sub-step per chosen modifier, rendered from its schema (D1 §6)."""
        key = self._modifier_keys[self._modifier_index]
        if user_input is None:
            return self._form(
                "modifier_options",
                steps.modifier_options_schema(key),
                placeholders={"modifier": key},
            )
        self._modifiers.append(
            {
                "key": key,
                "component": modifiers.entry(key).component,
                "options": value_of(modifiers.entry(key).schema, self._flat(user_input)),
                "source": "user",
            }
        )
        self._modifier_index += 1
        return await self._next_modifier()

    async def async_step_export(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Price an exported kWh (D1 §6). Nothing here is clamped (INV-51)."""
        if user_input is None:
            return self._form("export", steps.export_schema())
        answers = self._flat(user_input)
        if answers.get("mode", steps.EXPORT_NONE) == steps.EXPORT_NONE:
            self._export = None
        else:
            schema = modifiers.entry("export_price").schema
            self._export = {"key": "export_price", "options": value_of(schema, answers)}
            self._export["mode"] = answers["mode"]
            self._export["options"]["mode"] = answers["mode"]
        return await self.async_step_carriers()

    async def async_step_carriers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Other carriers the house buys: gas, district heat, oil, pellets (D1 §6)."""
        if user_input is None:
            return self._form("carriers", steps.carriers_schema())
        self._carrier_keys = list(user_input.get("carriers") or [])
        self._carrier_index = 0
        self._carriers = []
        return await self._next_carrier()

    async def _next_carrier(self) -> ConfigFlowResult:
        if self._carrier_index < len(self._carrier_keys):
            return await self.async_step_carrier_options()
        return await self._after_prices()

    async def async_step_carrier_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One carrier's price: fixed, or read daily from a sensor (D1 §6)."""
        carrier = self._carrier_keys[self._carrier_index]
        if user_input is None:
            return self._form(
                "carrier_options",
                steps.carrier_options_schema(self._currency),
                placeholders={"carrier": carrier},
            )
        self._carriers.append(
            {
                "carrier": carrier,
                "mode": user_input.get("mode", steps.CARRIER_FIXED),
                "price": str(user_input.get("price", 0.0)),
                "entity_id": user_input.get("entity_id"),
            }
        )
        self._carrier_index += 1
        return await self._next_carrier()

    async def _after_prices(self) -> ConfigFlowResult:
        """Only the full path has a capacity tariff to configure (HLD §4)."""
        if self._path is OnboardingPath.FULL:
            return await self.async_step_tariff()
        return await self.async_step_hard_limits()

    # ------------------------------------------------------------------ tariff

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Country and grid company: the preset select of D2 §6."""
        if user_input is None:
            self._presets = await self.hass.async_add_executor_job(
                steps.discover_presets, self._country
            )
            return self._form(
                "tariff",
                steps.tariff_schema(
                    country=self._country, presets=self._presets, chosen=self._preset_file
                ),
            )
        country = user_input.get("country") or self._country
        self._electrical["country"] = country
        self._preset_file = steps.preset_file(user_input["preset"], country)
        self._spec = await self.hass.async_add_executor_job(loader.load, self._preset_file)
        today = await self._local_today()
        self._version = self._spec.version_at(today)
        self._description = loader.render_plain_language(self._spec, today)
        return await self.async_step_tariff_preset()

    async def async_step_tariff_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what that preset means, in the household's own words (INV-67)."""
        assert self._spec is not None
        if user_input is None:
            return self._form(
                "tariff_preset",
                vol.Schema({}),
                placeholders={
                    "description": self._description,
                    "name": self._spec.name,
                    "source": self._spec.source_url or "",
                },
            )
        return await self.async_step_tariff_target()

    async def async_step_tariff_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set what the site defends and how much it gambles (D2 §6, dec. 18)."""
        assert self._version is not None
        if user_input is None:
            return self._form("tariff_target", steps.tariff_target_schema(self._version))
        answers = self._flat(user_input)
        try:
            steps.validate_target(answers)
        except steps.StepError as err:
            return self._form(
                "tariff_target",
                steps.tariff_target_schema(self._version),
                errors={err.field: err.key},
            )
        self._target = answers
        if steps.needs_bills(self._version):
            return await self.async_step_tariff_bills()
        if steps.needs_limits(self._version):
            return await self.async_step_tariff_limits()
        return await self.async_step_hard_limits()

    async def async_step_tariff_bills(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the last twelve monthly peaks, for a rolling preset (D2 §6)."""
        assert self._version is not None
        peak = steps.peak_of(self._version)
        assert peak is not None
        if user_input is None:
            return self._form("tariff_bills", steps.bills_schema(peak))
        self._bills = dict(user_input)
        if steps.needs_limits(self._version):
            return await self.async_step_tariff_limits()
        return await self.async_step_hard_limits()

    async def async_step_tariff_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the contracted power per period, from the preset (D2 §6)."""
        assert self._version is not None
        if user_input is None:
            return self._form("tariff_limits", steps.limits_schema(self._version))
        self._limits = dict(user_input)
        return await self.async_step_hard_limits()

    # ---------------------------------------------- hard limits, presence, etc.

    async def async_step_hard_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set what the whole connection may draw (D8 §5.1)."""
        assert self._profile is not None
        if user_input is None:
            contracted = self._limits.get("limit_1")
            return self._form("hard_limits", steps.hard_limits_schema(self._profile, contracted))
        self._hard_limits = {"contracted_kw": float(user_input["contracted_kw"])}
        return await self.async_step_presence()

    async def async_step_presence(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Presence: automatic from `person` entities, or set by hand (D8 §5.1)."""
        if user_input is None:
            return self._form("presence", steps.presence_schema(self.hass))
        answers = self._flat(user_input)
        self._presence = {
            "mode": answers.get("mode", "auto"),
            "persons": list(answers.get("persons") or []),
            "away_delay_min": int(answers.get("away_delay_min", 30)),
        }
        return await self.async_step_notifications()

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Which categories reach the household, and how (D8 §5.1, §5.8)."""
        if user_input is None:
            return self._form("notifications", steps.notifications_schema(self.hass))
        answers = self._flat(user_input)
        self._notifications = steps.notifications_data(answers)
        self._quiet = [
            str(answers.get("quiet_start", QUIET_START_DEFAULT)),
            str(answers.get("quiet_end", QUIET_END_DEFAULT)),
        ]
        return await self.async_step_review()

    # ------------------------------------------------------------------ review

    def _assemble(self) -> dict[str, Any]:
        """Return `entry.data`: every answer and every derivation (D8 §4, INV-66)."""
        prices: dict[str, Any] | None = None
        if self._path is not OnboardingPath.FUSE_ONLY:
            prices = {
                "sources": self._sources,
                "modifiers": [*self._modifiers, *self._preset_modifiers()],
                "export": self._export,
                "carriers": self._carriers,
            }
        tariff: dict[str, Any] | None = None
        if (
            self._path is OnboardingPath.FULL
            and self._spec is not None
            and self._version is not None
        ):
            tariff = steps.tariff_data(
                preset=self._preset_file or steps.PRESET_CUSTOM,
                spec=self._spec,
                version=self._version,
                answers=self._target,
                bills=self._bills,
                limits=self._limits,
                description=self._description,
            )
        elif self._path is OnboardingPath.PRICE_ONLY:
            tariff = dict(steps.NO_PEAK_TARIFF)
        return {
            CONF_PATH: self._path.value,
            CONF_NAME: self._name,
            CONF_TIMEZONE: self._timezone,
            CONF_TIMEZONE_SOURCE: self._timezone_source,
            CONF_CURRENCY: self._currency,
            CONF_ELECTRICAL: self._electrical,
            CONF_METER: self._meter,
            CONF_PRICES: prices,
            CONF_TARIFF: tariff,
            CONF_HARD_LIMITS: self._hard_limits,
            CONF_PRESENCE: self._presence,
            CONF_NOTIFICATIONS: self._notifications,
            CONF_QUIET_HOURS: self._quiet,
            CONF_ACTIVE: False,
        }

    def _preset_modifiers(self) -> list[dict[str, Any]]:
        """Hand the preset's own energy components to D1 as modifiers (D2 §6).

        The grid's energy charge is the one modifier nobody should have to type:
        the preset has the numbers, their source and their verification date. It
        is added only for a component the household did not configure by hand, and
        it carries the preset id so the review can say where it came from
        (`design/DECISIONS.md` D-0126).
        """
        if self._version is None or self._spec is None:
            return []
        configured = {modifier["key"] for modifier in self._modifiers}
        added: list[dict[str, Any]] = []
        for key, options in self._version.energy_components.items():
            if key in configured or key not in set(modifiers.keys()):
                continue
            schema = modifiers.entry(key).schema
            added.append(
                {
                    "key": key,
                    "component": modifiers.entry(key).component,
                    "options": {
                        field.key: store_value(field, options[field.key])
                        for field in schema
                        if field.key in options
                    },
                    "source": self._spec.id,
                }
            )
        return added

    async def async_step_review(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """State what powerplan derived and what happens first (INV-67)."""
        data = self._assemble()
        if user_input is None:
            return self._form(
                "review",
                steps.review_schema(),
                placeholders=await review.placeholders(self.hass, data),
                last_step=True,
            )
        data[CONF_ACTIVE] = not user_input.get("start_in_observe", True)

        unique_id = self._unique_id()
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        _LOGGER.info(
            "creating site %s on the %s path, unique id %s, timezone %s (%s), observe=%s",
            self._name,
            self._path.value,
            unique_id,
            self._timezone,
            self._timezone_source,
            not data[CONF_ACTIVE],
        )
        return self.async_create_entry(title=self._name, data=data)

    def _unique_id(self) -> str:
        """Identify the site by something the household cannot rename (INV-50).

        The import register is the site: it is the one entity whose readings
        define every window, it belongs to the grid meter rather than to
        powerplan, and its platform unique id is a serial number. Grid power is
        the fallback where no register is bound, and a price-only site - which has
        no meter at all - gets the flow's own id, because nothing else about it is
        unique (`design/DECISIONS.md` D-0122).
        """
        roles = (self._meter or {}).get("roles", {})
        for role in (ROLE_IMPORT_REGISTER, ROLE_GRID_POWER):
            entity_id = roles.get(role)
            if entity_id:
                return f"meter:{device_pick.stable_id(self.hass, entity_id)}"
        return f"site:{self.flow_id}"

    # -------------------------------------------------------------- subentries

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this site supports.

        Loads, groups, zones and circuits are config subentries with only a
        `user` and a `reconfigure` step (D8 §5.2–5.3, PLAN §7 dec. 4). The load
        flow is WP2.4's and the circuit flow WP2.5's; groups and zones arrive
        in WP3.2 and WP5.3.
        """
        return {SUBENTRY_LOAD: LoadSubentryFlow, SUBENTRY_CIRCUIT: CircuitSubentryFlow}


__all__ = ["PowerplanConfigFlow"]
