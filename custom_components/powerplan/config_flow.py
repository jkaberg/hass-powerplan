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
    SUBENTRY_GROUP,
    SUBENTRY_LOAD,
    SUBENTRY_ZONE,
    TIMEZONE_FROM_HASS,
    TIMEZONE_FROM_USER,
    OnboardingPath,
)
from .core.pricing import Carrier, modifiers
from .core.tariffs.presets import loader
from .flow import device_pick, review, steps
from .flow.circuit import CircuitSubentryFlow
from .flow.group import GroupSubentryFlow
from .flow.load import LoadSubentryFlow
from .flow.questionnaire import store_value, value_of
from .flow.text import PRESET_CUSTOM, PRESET_UNKNOWN, Text, target_label, tariff_table
from .flow.zone import ZoneSubentryFlow
from .providers.prices.formats import registry as formats
from .providers.prices.nordpool_action import NordpoolActionSource
from .runtime import step_index

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

    from homeassistant.config_entries import ConfigEntry, ConfigSubentryFlow

    from .core.metering.profile import ElectricalProfile
    from .core.tariffs.grammar import TariffSpec, TariffVersion
    from .core.tariffs.presets.loader import TariffSummary

    type _Step = Callable[
        [PowerplanConfigFlow, dict[str, Any] | None], Coroutine[Any, Any, ConfigFlowResult]
    ]

_LOGGER = logging.getLogger(__name__)


class PowerplanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for one site (D8 §5.1)."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Start with an empty site and the environment's own answers."""
        #: Set by `async_step_reconfigure`; every step's own form prefers the
        #: site's current answer over its onboarding default while this holds.
        self._reconfiguring = False
        self._active = False
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
        #: The tariff step's own answer - a preset, `unknown` or `custom` -
        #: which names the tariff on the screens that follow (HUB-11).
        self._preset_choice: str | None = None
        self._spec: TariffSpec | None = None
        self._version: TariffVersion | None = None
        self._summary: TariffSummary | None = None
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

    async def _restore_from_entry(self, data: Mapping[str, Any]) -> None:
        """Load a stored site into the flow's own state - `_assemble()` in reverse.

        The same instance attributes every step's own form already pre-fills
        from, so re-entering the chain at `name` shows the site's current
        answers instead of the onboarding defaults. Reconfigure never changes
        the onboarding path (D8 §5.1, like a subentry's own reconfigure never
        changes its kind) - every step it walks is the ones `self._path`
        already takes.
        """
        self._path = OnboardingPath(data[CONF_PATH])
        self._active = bool(data.get(CONF_ACTIVE, False))
        self._name = data[CONF_NAME]
        self._timezone = data.get(CONF_TIMEZONE)
        self._timezone_source = data.get(CONF_TIMEZONE_SOURCE, TIMEZONE_FROM_HASS)
        stored_electrical = data.get(CONF_ELECTRICAL) or {}
        if stored_electrical:
            # `electrical_data()`'s own stored shape is numeric (INV-66's
            # materialised form); the step's schema wants back the string form
            # its own SelectSelectors submitted (`main_fuse_a` matching one of
            # FUSE_RATINGS exactly, "63" not "63.0") - `electrical_profile()`
            # itself tolerates either, so only the pre-fill needs the string.
            self._electrical = {
                "country": stored_electrical.get("country", ""),
                "system": stored_electrical.get("system", ""),
                "phases": str(stored_electrical.get("phases", "")),
                "main_fuse_a": str(int(stored_electrical.get("main_fuse_a", 0))),
                "per_phase_limit_a": stored_electrical.get("per_phase_limit_a"),
            }
            self._profile = steps.electrical_profile(self._electrical)
        self._meter = dict(data[CONF_METER]) if data.get(CONF_METER) else None

        prices = data.get(CONF_PRICES) or {}
        self._sources = [dict(source) for source in prices.get("sources") or ()]
        if self._sources:
            self._price_source = self._sources[0]["key"]
        # Preset-injected modifiers (`_preset_modifiers`, source = the preset's
        # own id) are re-derived fresh from `self._spec`/`self._version` on
        # save; only what the household chose through this step needs to
        # survive the round trip, or `_assemble` would see it twice.
        self._modifiers = [
            dict(modifier)
            for modifier in prices.get("modifiers") or ()
            if modifier.get("source") == "user"
        ]
        self._modifier_keys = [modifier["key"] for modifier in self._modifiers]
        self._export = dict(prices["export"]) if prices.get("export") else None
        self._carriers = [dict(carrier) for carrier in prices.get("carriers") or ()]
        self._carrier_keys = [carrier["carrier"] for carrier in self._carriers]

        self._hard_limits = dict(data.get(CONF_HARD_LIMITS) or {})

        tariff = data.get(CONF_TARIFF) or {}
        preset_file = tariff.get("preset_file")
        if self._path is OnboardingPath.FULL and preset_file:
            self._preset_file = preset_file
            self._spec = await self.hass.async_add_executor_job(loader.load, preset_file)
            today = await self._local_today()
            self._version = self._spec.version_at(today)
            self._summary = loader.summarize(self._spec, today)
            self._preset_choice = steps.preset_choice(preset_file, self._country)
            # An entry from before WP U.1 holds `step:<i>` and an English
            # `description`; the first reads as `step_<i>` and the second is
            # never read again (D8 §9 22).
            target = str(tariff.get("target", "auto"))
            index = step_index(target)
            self._target = {
                "target": target if index is None else f"step_{index}",
                "target_kw": tariff.get("target_kw"),
                "risk": steps.risk_key(tariff.get("risk", steps.RISK_LABELS["flat"])),
                "eps_kwh": tariff.get("eps_kwh"),
                "cap_margin_kw": tariff.get("cap_margin_kw"),
            }
            # `tariff_data()` keeps only the answered months/periods, in order,
            # and drops which position each one was (D8 §5.1) - a household
            # that left an early month blank sees its later ones shift back by
            # the gap; INV-67's own review catches it before anything saves.
            self._bills = {
                f"month_{index + 1}": value for index, value in enumerate(tariff.get("bills") or ())
            }
            self._limits = {
                f"limit_{index + 1}": value
                for index, value in enumerate(tariff.get("contracted_kw") or ())
            }

        self._presence = dict(data.get(CONF_PRESENCE) or {})
        self._notifications = dict(data.get(CONF_NOTIFICATIONS) or {})
        stored_quiet = data.get(CONF_QUIET_HOURS)
        self._quiet = (
            list(stored_quiet) if stored_quiet else [QUIET_START_DEFAULT, QUIET_END_DEFAULT]
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-walk the site's own steps, pre-filled, ending in an update not a create.

        Every step already renders its form from `self._xxx`; restoring those
        first and entering the chain at `name` (never the path menu - D8 §5.1,
        the same "never changes kind" rule a subentry's reconfigure follows) is
        all this needs, exactly like `flow/group.py`'s own `async_step_reconfigure`
        delegates to the same `_ask` its `user` step uses.
        """
        del user_input
        self._reconfiguring = True
        await self._restore_from_entry(self._get_reconfigure_entry().data)
        return await self.async_step_name()

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
            default_source = (
                self._price_source
                if self._reconfiguring
                else steps.default_price_source(self._country)
            )
            return self._form("prices", steps.price_source_schema(default_source))
        self._price_source = user_input["source"]
        match self._price_source:
            case steps.SOURCE_ENTITY:
                return await self.async_step_prices_entity()
            case steps.SOURCE_FIXED:
                return await self.async_step_prices_fixed()
            case _:
                return await self.async_step_prices_nordpool()

    def _stored_source_options(self, key: str) -> dict[str, Any] | None:
        """Return the site's own stored options for price source `key`, if any."""
        if self._sources and self._sources[0].get("key") == key:
            options = self._sources[0].get("options")
            return options if isinstance(options, dict) else None
        return None

    async def async_step_prices_nordpool(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nord Pool, rendered from the source's own schema (D1 §6)."""
        if user_input is None:
            values = self._stored_source_options(steps.SOURCE_NORDPOOL) or steps.nordpool_defaults(
                self.hass, self._currency
            )
            return self._form("prices_nordpool", steps.nordpool_schema(values=values))
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
            stored = self._stored_source_options(steps.SOURCE_ENTITY) or {}
            return self._form(
                "prices_entity",
                steps.price_entity_schema(
                    default_entity=stored.get("entity_id"), default_format=stored.get("format")
                ),
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
            stored = self._stored_source_options(steps.SOURCE_FIXED) or {}
            price = float(stored["price"]) if "price" in stored else 0.0
            return self._form(
                "prices_fixed", steps.fixed_price_schema(self._currency, default=price)
            )
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
            key = self._modifier_keys[self._modifier_index]
            step: _Step = getattr(type(self), f"async_step_modifier_{key}")
            return await step(self, None)
        return await self.async_step_export()

    async def _modifier_step(self, key: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        """One step per add-on, `modifier_<key>`, rendered from its schema (D1 §6).

        Each has its own title, description and field help (review HUB-7, 8,
        21): a shared step could only be titled with the add-on's key.
        """
        if user_input is None:
            return self._form(f"modifier_{key}", steps.modifier_options_schema(key))
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
            return self._form("export", steps.export_schema(values=self._export))
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
            return self._form("carriers", steps.carriers_schema(self._carrier_keys or None))
        self._carrier_keys = list(user_input.get("carriers") or [])
        self._carrier_index = 0
        self._carriers = []
        return await self._next_carrier()

    async def _next_carrier(self) -> ConfigFlowResult:
        if self._carrier_index < len(self._carrier_keys):
            carrier = self._carrier_keys[self._carrier_index]
            step: _Step = getattr(type(self), f"async_step_carrier_{carrier}")
            return await step(self, None)
        return await self._after_prices()

    async def _carrier_step(
        self, carrier: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """One carrier's price, `carrier_<key>`: fixed, or read daily from a sensor (D1 §6).

        A step of its own per carrier for the same reason as the add-ons: a
        shared one was titled with the carrier's key (review R2).
        """
        if user_input is None:
            return self._form(f"carrier_{carrier}", steps.carrier_options_schema(self._currency))
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
                    country=self._country,
                    presets=self._presets,
                    chosen=self._preset_file,
                    text=await Text.load(self.hass),
                ),
            )
        country = user_input.get("country") or self._country
        self._electrical["country"] = country
        self._preset_choice = str(user_input["preset"])
        self._preset_file = steps.preset_file(self._preset_choice, country)
        self._spec = await self.hass.async_add_executor_job(loader.load, self._preset_file)
        today = await self._local_today()
        self._version = self._spec.version_at(today)
        self._summary = loader.summarize(self._spec, today)
        return await self.async_step_tariff_preset()

    def _tariff_name(self, text: Text) -> str:
        """Name the tariff as the household chose it: the operator's own name, or the escape hatch."""
        assert self._spec is not None
        if self._preset_choice == PRESET_UNKNOWN:
            return text.word("text", "preset_unknown")
        if PRESET_CUSTOM in (self._preset_choice, self._preset_file):
            return text.word("text", "preset_custom")
        return self._spec.name

    async def async_step_tariff_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what that preset means, in the household's own words (INV-67)."""
        assert self._spec is not None
        assert self._summary is not None
        if user_input is None:
            text = await Text.load(self.hass)
            return self._form(
                "tariff_preset",
                vol.Schema({}),
                placeholders={
                    "name": self._tariff_name(text),
                    "table": tariff_table(text, self._summary),
                    "source": self._summary.source_url or text.word("text", "none"),
                },
            )
        return await self.async_step_tariff_target()

    async def async_step_tariff_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set what the site defends and how much it gambles (D2 §6, dec. 18)."""
        assert self._version is not None
        text = await Text.load(self.hass)
        if user_input is None:
            return self._form(
                "tariff_target",
                steps.tariff_target_schema(self._version, text=text, values=self._target),
            )
        answers = self._flat(user_input)
        try:
            steps.validate_target(answers)
        except steps.StepError as err:
            return self._form(
                "tariff_target",
                steps.tariff_target_schema(self._version, text=text, values=self._target),
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
            return self._form("tariff_bills", steps.bills_schema(peak, values=self._bills))
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
            return self._form(
                "tariff_limits", steps.limits_schema(self._version, values=self._limits)
            )
        self._limits = dict(user_input)
        return await self.async_step_hard_limits()

    # ---------------------------------------------- hard limits, presence, etc.

    async def async_step_hard_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set what the whole connection may draw (D8 §5.1)."""
        assert self._profile is not None
        if user_input is None:
            contracted = self._hard_limits.get("contracted_kw") or self._limits.get("limit_1")
            return self._form("hard_limits", steps.hard_limits_schema(self._profile, contracted))
        self._hard_limits = {"contracted_kw": float(user_input["contracted_kw"])}
        return await self.async_step_presence()

    async def async_step_presence(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Presence: automatic from `person` entities, or set by hand (D8 §5.1)."""
        if user_input is None:
            return self._form("presence", steps.presence_schema(self.hass, values=self._presence))
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
            return self._form(
                "notifications",
                steps.notifications_schema(
                    self.hass,
                    text=await Text.load(self.hass),
                    values=self._notifications,
                    quiet=self._quiet,
                ),
            )
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
                steps.review_schema(default_observe=not self._active),
                placeholders=await review.placeholders(
                    self.hass, data, tariff=await self._tariff_line()
                ),
                last_step=True,
            )
        data[CONF_ACTIVE] = not user_input.get("start_in_observe", True)

        if self._reconfiguring:
            entry = self._get_reconfigure_entry()
            _LOGGER.info(
                "reconfiguring site %s (%s), timezone %s (%s), observe=%s",
                self._name,
                entry.entry_id,
                self._timezone,
                self._timezone_source,
                not data[CONF_ACTIVE],
            )
            # Not `async_update_reload_and_abort`: its own reload would race
            # the site's existing update listener, which already reloads on
            # exactly this - `entry.data` changed (`Runtime.
            # async_handle_subentry_update`, D7 §2) - and HA now warns that
            # having both is deprecated (2026.12.0).
            self.hass.config_entries.async_update_entry(entry, title=self._name, data=data)
            return self.async_abort(reason="reconfigure_successful")

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

    async def _tariff_line(self) -> str | None:
        """Return the review's grid tariff: the company and the target, in words (HUB-14)."""
        if self._path is not OnboardingPath.FULL or self._spec is None:
            return None
        text = await Text.load(self.hass)
        target = target_label(text, self._version, str(self._target.get("target", "auto")))
        return f"{self._tariff_name(text)} · {target}"

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
        flow is WP2.4's, the circuit flow WP2.5's, the group flow WP3.2's and
        the zone flow WP5.3's.
        """
        return {
            SUBENTRY_LOAD: LoadSubentryFlow,
            SUBENTRY_CIRCUIT: CircuitSubentryFlow,
            SUBENTRY_GROUP: GroupSubentryFlow,
            SUBENTRY_ZONE: ZoneSubentryFlow,
        }


def _modifier_step_for(key: str) -> _Step:
    async def step(
        self: PowerplanConfigFlow, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._modifier_step(key, user_input)

    step.__name__ = f"async_step_modifier_{key}"
    step.__doc__ = f"The `{key}` price add-on's own step (D1 §6, review HUB-7)."
    return step


def _carrier_step_for(carrier: str) -> _Step:
    async def step(
        self: PowerplanConfigFlow, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._carrier_step(carrier, user_input)

    step.__name__ = f"async_step_carrier_{carrier}"
    step.__doc__ = f"The `{carrier}` carrier's own price step (D1 §6)."
    return step


# One step id per registered add-on and per carrier, from the registries: a new
# modifier is one module and gets its own step, with its own strings (D1 §6).
for _key in modifiers.keys():  # noqa: SIM118 - the registry's function
    if _key != "export_price":
        setattr(PowerplanConfigFlow, f"async_step_modifier_{_key}", _modifier_step_for(_key))
for _carrier in Carrier:
    if _carrier is not Carrier.ELECTRICITY:
        setattr(
            PowerplanConfigFlow, f"async_step_carrier_{_carrier}", _carrier_step_for(str(_carrier))
        )

__all__ = ["PowerplanConfigFlow"]
