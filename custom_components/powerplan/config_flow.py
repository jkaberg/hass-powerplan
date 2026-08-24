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

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTIVE,
    CONF_CURRENCY,
    CONF_ELECTRICAL,
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
from .flow.questionnaire import (
    DONT_KNOW,
    implausible_price,
    missing_required,
    price_implausible,
    price_stored,
    seconds_of,
    store_value,
    value_of,
)
from .flow.text import PRESET_CUSTOM, PRESET_UNKNOWN, Text, target_label, tariff_table
from .flow.zone import ZoneSubentryFlow
from .providers.prices.formats import registry as formats
from .providers.prices.nordpool_action import NordpoolActionSource
from .runtime import step_index

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

    import voluptuous as vol
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
        #: The roles the meter step found (or the site had), for its confirmation.
        self._found: dict[str, str] = {}
        self._price_source = steps.SOURCE_NORDPOOL
        #: The agreement question's answer: a source key, or Norgespris (D1 §6).
        self._agreement: str | None = None
        self._sources: list[dict[str, Any]] = []
        self._modifier_keys: list[str] = []
        self._modifier_index = 0
        self._modifiers: list[dict[str, Any]] = []
        #: The add-ons' stored options by key, for a reconfigure's pre-fill (HUB-9).
        self._stored_modifiers: dict[str, dict[str, Any]] = {}
        self._export: dict[str, Any] | None = None
        self._carrier_keys: list[str] = []
        self._carrier_index = 0
        self._carriers: list[dict[str, Any]] = []
        self._presets: list[tuple[str, str]] = []
        self._preset_file: str | None = None
        #: The tariff step's own answer - a preset, `unknown` or `custom` -
        #: which names the tariff on the screens that follow (HUB-11).
        self._preset_choice: str | None = None
        #: The chosen preset's JSON (a template has nulls) and the tariff this site
        #: keeps: that JSON with the household's own numbers (D2 §6, INV-66).
        self._raw: dict[str, Any] | None = None
        self._copy: dict[str, Any] | None = None
        #: A template's step table as the household entered it from the bill.
        self._steps: dict[str, Any] = {}
        self._spec: TariffSpec | None = None
        self._version: TariffVersion | None = None
        self._summary: TariffSummary | None = None
        self._target: dict[str, Any] = {}
        self._bills: dict[str, Any] = {}
        self._limits: dict[str, Any] = {}
        self._presence: dict[str, Any] = {}
        #: What the electrical step suggested for the per-phase limit (HUB-19).
        self._phase_suggestion: float | None = None
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
            # "Vet ikke" is shown again where the fuse was assumed (D3 §6).
            assumed = set(stored_electrical.get("assumed") or ())
            fuse = float(stored_electrical.get("main_fuse_a", 0))
            self._electrical = {
                "country": stored_electrical.get("country", ""),
                "system": DONT_KNOW if "system" in assumed else stored_electrical.get("system", ""),
                "phases": str(stored_electrical.get("phases", "")),
                "main_fuse_a": DONT_KNOW if "main_fuse_a" in assumed else f"{fuse:g}",
                "per_phase_limit_a": stored_electrical.get("per_phase_limit_a"),
            }
            self._profile = steps.electrical_profile(self._electrical)
        self._meter = dict(data[CONF_METER]) if data.get(CONF_METER) else None
        # The meter's device is restored too, so its step is pre-filled (HUB-22).
        self._meter_device = (self._meter or {}).get("device_id")

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
        self._agreement = (
            steps.AGREEMENT_NORGESPRIS
            if self._price_source == steps.SOURCE_NORDPOOL
            and "fixed_price" in self._modifier_keys
            and steps.AGREEMENT_NORGESPRIS in steps.agreements(self._country)
            else self._price_source
        )
        self._stored_modifiers = {
            modifier["key"]: dict(modifier.get("options") or {}) for modifier in self._modifiers
        }
        self._export = dict(prices["export"]) if prices.get("export") else None
        self._carriers = [dict(carrier) for carrier in prices.get("carriers") or ()]
        self._carrier_keys = [carrier["carrier"] for carrier in self._carriers]

        # An entry from before WP U.2 may carry `hard_limits`: read by nothing,
        # so not restored and not written back (D8 §5.15 S2, §9 22).

        tariff = data.get(CONF_TARIFF) or {}
        preset_file = tariff.get("preset_file")
        if self._path is OnboardingPath.FULL and preset_file:
            self._preset_file = preset_file
            self._raw = await self.hass.async_add_executor_job(steps.preset_raw, preset_file)
            if tariff.get("spec") and steps.needs_steps(self._raw):
                self._steps = steps.step_answers(tariff["spec"])
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
            await self._apply_tariff()

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
        """Ask "Hva vil du kalle dette hjemmet?" - Home Assistant's own name for it by default."""
        if user_input is None:
            if not self._reconfiguring and self._name == steps.DEFAULT_SITE_NAME:
                self._name = self.hass.config.location_name or steps.DEFAULT_SITE_NAME
            return self._form("name", steps.name_schema(self._name))
        self._name = user_input[CONF_NAME] or steps.DEFAULT_SITE_NAME

        self._timezone = await steps.resolve_timezone(self.hass)
        if self._timezone is None:
            self._zones = await steps.timezone_options(self.hass)
            return await self.async_step_timezone()
        self._timezone_source = TIMEZONE_FROM_HASS
        _LOGGER.debug("site timezone %s taken from hass.config", self._timezone)
        return await self._after_name()

    async def _after_name(self) -> ConfigFlowResult:
        """Go to detection first: the meter, then the fuse (D8 §5.15, questions 3 and 4)."""
        if self._path is OnboardingPath.PRICE_ONLY:
            return await self.async_step_electrical()
        return await self.async_step_meter()

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
        return await self._after_name()

    # -------------------------------------------------------------- electrical

    @property
    def _asks_country(self) -> bool:
        """The country is asked only when Home Assistant has none (HUB-2)."""
        return not self.hass.config.country

    def _electrical_form(
        self, values: Mapping[str, Any], errors: Mapping[str, str] | None = None
    ) -> ConfigFlowResult:
        country = values.get("country") or self._country
        self._phase_suggestion = steps.phase_limit_suggestion(country, values)
        return self._form(
            "electrical",
            steps.electrical_schema(country=country, values=values, ask_country=self._asks_country),
            errors=errors,
        )

    async def async_step_electrical(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask "Hvor stor er hovedsikringen?" - and in Norway the voltage (D3 §6)."""
        if user_input is None:
            return self._electrical_form(self._electrical)
        answers = self._flat(user_input)
        answers.setdefault("country", self._country or "")
        if self._phase_suggestion is not None and steps.same_value(
            answers.get("per_phase_limit_a"), self._phase_suggestion
        ):
            # Left at the fuse it was shown: no lower limit, whatever fuse was picked.
            answers.pop("per_phase_limit_a")
        try:
            profile = steps.electrical_profile(answers)
        except steps.StepError as err:
            return self._electrical_form(answers, errors={err.field: err.key})
        self._profile = profile
        self._electrical = steps.electrical_data(answers, profile)
        if self._path is OnboardingPath.FUSE_ONLY:
            return await self.async_step_presence()
        return await self.async_step_prices()

    # ------------------------------------------------------------------- meter

    async def async_step_meter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask "Hvor måler du strømforbruket?" - the device that measures power (D3 §6)."""
        if user_input is None:
            return self._form("meter", steps.meter_device_schema(self._meter_device))
        device = user_input.get("device")
        stored = (self._meter or {}).get("roles") or {}
        if stored and device == (self._meter or {}).get("device_id"):
            self._found = dict(stored)
        else:
            self._found = device_pick.prefill_roles(self.hass, device)
        self._meter_device = device
        if steps.meter_found(self._found):
            return await self.async_step_meter_confirm()
        return await self.async_step_meter_roles()

    async def async_step_meter_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what was found with its value now; the role form only on "Endre" (D3 §6)."""
        if user_input is not None:
            if user_input.get("confirm") == steps.METER_CHANGE:
                return await self.async_step_meter_roles()
            try:
                self._meter = steps.meter_data(self.hass, self._meter_device, self._found)
            except steps.StepError as err:
                return self._form(
                    "meter_roles",
                    steps.meter_roles_schema(self._found),
                    errors={err.field: err.key},
                )
            return await self.async_step_electrical()
        text = await Text.load(self.hass)
        return self._form(
            "meter_confirm",
            steps.meter_confirm_schema(),
            placeholders={"found": steps.meter_values(self.hass, text, self._found)},
        )

    async def async_step_meter_roles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind the roles of D3 §6 by hand; every one of them is optional."""
        if user_input is None:
            return self._form("meter_roles", steps.meter_roles_schema(self._found))
        answers = steps.meter_roles_answers(user_input)
        try:
            self._meter = steps.meter_data(self.hass, self._meter_device, answers)
        except steps.StepError as err:
            return self._form(
                "meter_roles",
                steps.meter_roles_schema(
                    {key: value for key, value in answers.items() if isinstance(value, str)}
                ),
                errors={err.field: err.key},
            )
        self._found = dict(self._meter["roles"])
        return await self.async_step_electrical()

    # ------------------------------------------------------------------ prices

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask "Hvilken strømavtale har du?" - spot, Norgespris or fixed (D1 §6)."""
        if user_input is None:
            default_source = (
                self._agreement or self._price_source
                if self._reconfiguring
                else steps.default_price_source(self._country)
            )
            return self._form("prices", steps.price_source_schema(default_source, self._country))
        self._agreement = str(user_input["source"])
        # Norgespris is priced on the spot source by the `fixed_price` add-on (F-2).
        self._price_source = (
            steps.SOURCE_NORDPOOL
            if self._agreement == steps.AGREEMENT_NORGESPRIS
            else self._agreement
        )
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
        """Nord Pool, rendered from the source's own schema (D1 §6).

        Asked only to correct: on a first setup where the house's one Nord Pool
        entry names its area, nothing is left to ask (CTL-9).
        """
        stored = self._stored_source_options(steps.SOURCE_NORDPOOL)
        if user_input is None:
            values = stored or steps.nordpool_defaults(self.hass, self._currency)
            if not self._reconfiguring and steps.nordpool_detected(values):
                user_input = {"config_entry": values["config_entry"], "area": values["area"]}
            else:
                return self._form(
                    "prices_nordpool",
                    steps.nordpool_schema(values=values, text=await Text.load(self.hass)),
                )
        answers = self._flat(user_input)
        answers["currency"] = self._currency
        # The publication clock left at what the area derives is not an override (HUB-19).
        answers = steps.drop_suggested(answers, steps.nordpool_suggested(answers.get("area")))
        self._sources = [
            {
                "key": steps.SOURCE_NORDPOOL,
                "options": value_of(NordpoolActionSource.schema, answers, prefix="nordpool"),
            }
        ]
        return await self._after_source()

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
        return await self._after_source()

    async def async_step_prices_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a fixed price per kWh, for a house with no curve at all (D1 §6)."""
        if user_input is None:
            stored = self._stored_source_options(steps.SOURCE_FIXED) or {}
            return self._form(
                "prices_fixed",
                steps.fixed_price_schema(self._currency, default=stored.get("price")),
            )
        if price_implausible(user_input["price"]):
            return self._form(
                "prices_fixed",
                steps.fixed_price_schema(self._currency),
                errors={"price": "price_in_minor_unit"},
            )
        # Shown in the minor unit, stored in major units as before (CTL-3).
        price = price_stored(user_input["price"], self._currency)
        self._sources = [
            {
                "key": steps.SOURCE_FIXED,
                "options": {"price": str(price), "currency": self._currency},
            }
        ]
        return await self._after_source()

    async def _after_source(self) -> ConfigFlowResult:
        """Go on to the grid company, then the follow-ups (D8 §5.15; the grid charge after it, HUB-3)."""
        if self._path is OnboardingPath.FULL:
            return await self.async_step_tariff()
        return await self.async_step_export()

    async def async_step_modifiers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask "Er strømavtalen din spesiell?" - the price add-ons, a follow-up (D1 §6)."""
        included = self._preset_components()
        if user_input is None:
            chosen = self._modifier_keys or None
            also = ("fixed_price",) if self._agreement == steps.AGREEMENT_NORGESPRIS else ()
            text = await Text.load(self.hass)
            return self._form(
                "modifiers",
                steps.modifiers_schema(self._country, chosen, also=also, included=included),
                placeholders={
                    "included": text.join(text.word("modifier", key) for key in included)
                    or text.word("text", "none")
                },
            )
        ticked = set(user_input.get("modifiers") or []) - set(included)
        # Asked in the order they are listed, not the order they were ticked (HUB-3).
        self._modifier_keys = [key for key in steps.offered_modifiers() if key in ticked]
        self._modifier_index = 0
        self._modifiers = []
        return await self._next_modifier()

    async def _next_modifier(self) -> ConfigFlowResult:
        if self._modifier_index < len(self._modifier_keys):
            key = self._modifier_keys[self._modifier_index]
            step: _Step = getattr(type(self), f"async_step_modifier_{key}")
            return await step(self, None)
        return await self.async_step_carriers()

    async def _modifier_step(self, key: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        """One step per add-on, `modifier_<key>`, rendered from its schema (D1 §6).

        Each has its own title, description and field help (review HUB-7, 8,
        21): a shared step could only be titled with the add-on's key.
        """
        schema = modifiers.entry(key).schema
        stored = self._stored_modifiers.get(key)
        if user_input is None:
            return self._form(
                f"modifier_{key}",
                steps.modifier_options_schema(key, currency=self._currency, values=stored),
            )
        answers = self._flat(user_input)
        missing = missing_required(schema, answers)
        wrong = implausible_price(schema, answers, self._currency)
        if missing is not None or wrong is not None:
            # A required answer with nothing to default to - Norgespris's price,
            # an empty tier table - is refused, never stored empty (review §10);
            # so is a price typed in kroner into an øre box.
            field, code = (missing, "required") if missing else (wrong, "price_in_minor_unit")
            return self._form(
                f"modifier_{key}",
                steps.modifier_options_schema(key, currency=self._currency, values=stored),
                errors={str(field): code},
            )
        self._modifiers.append(
            {
                "key": key,
                "component": modifiers.entry(key).component,
                "options": value_of(
                    schema, answers, prefix=f"modifier_{key}", currency=self._currency
                ),
                "source": "user",
            }
        )
        self._modifier_index += 1
        return await self._next_modifier()

    async def async_step_export(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask "Selger du strøm tilbake?" - whether, and how, an exported kWh is paid (D1 §6).

        The amounts follow on their own step, and only when there is something to
        price: "I do not export" never sees them (D8 §5.15 rule 5, HUB-17).
        """
        if user_input is None:
            return self._form("export", steps.export_schema(values=self._export))
        mode = str(user_input.get("mode", steps.EXPORT_NONE))
        if mode == steps.EXPORT_NONE:
            self._export = None
            return await self.async_step_modifiers()
        if (self._export or {}).get("mode") != mode:
            self._export = {"key": "export_price", "options": {}, "mode": mode}
        if not steps.EXPORT_FIELDS.get(mode, ("amount",)):
            return await self._save_export(mode, {})
        return await self.async_step_export_amounts()

    async def async_step_export_amounts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the amounts the export mode reads, in the minor unit or percent (INV-51)."""
        assert self._export is not None
        mode = str(self._export["mode"])
        if user_input is None:
            return self._form(
                "export_amounts",
                steps.export_amounts_schema(
                    mode, currency=self._currency, values=self._export.get("options")
                ),
            )
        return await self._save_export(mode, self._flat(user_input))

    async def _save_export(self, mode: str, answers: Mapping[str, Any]) -> ConfigFlowResult:
        schema = modifiers.entry("export_price").schema
        options = value_of(schema, answers, prefix="export", currency=self._currency)
        options["mode"] = mode
        self._export = {"key": "export_price", "options": options, "mode": mode}
        return await self.async_step_modifiers()

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
        return await self.async_step_presence()

    async def _carrier_step(
        self, carrier: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """One carrier's price, `carrier_<key>`: fixed, or read daily from a sensor (D1 §6).

        A step of its own per carrier for the same reason as the add-ons: a
        shared one was titled with the carrier's key (review R2).
        """
        if user_input is None:
            return self._form(f"carrier_{carrier}", steps.carrier_options_schema(self._currency))
        # The price per kWh is shown in the minor unit, stored in major units (CTL-3).
        price = price_stored(user_input.get("price", 0.0), self._currency)
        self._carriers.append(
            {
                "carrier": carrier,
                "mode": user_input.get("mode", steps.CARRIER_FIXED),
                "price": str(price if price is not None else 0),
                "entity_id": user_input.get("entity_id"),
            }
        )
        self._carrier_index += 1
        return await self._next_carrier()

    # ------------------------------------------------------------------ tariff

    async def async_step_tariff(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask "Hvilket nettselskap har du?" - the preset select of D2 §6."""
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
                    ask_country=self._asks_country,
                ),
            )
        country = user_input.get("country") or self._country
        self._electrical["country"] = country
        self._preset_choice = str(user_input["preset"])
        chosen = steps.preset_file(self._preset_choice, country)
        if chosen != self._preset_file:
            # Another company's table: the last one's numbers are not this one's.
            self._steps, self._limits = {}, {}
        self._preset_file = chosen
        self._raw = await self.hass.async_add_executor_job(steps.preset_raw, chosen)
        if steps.needs_steps(self._raw):
            return await self.async_step_tariff_steps()
        await self._apply_tariff()
        return await self.async_step_tariff_preset()

    async def _apply_tariff(self) -> None:
        """Build the tariff this site keeps from the preset and the household's numbers."""
        assert self._raw is not None
        self._copy = steps.filled_tariff(
            self._raw, country=self._country, steps=self._steps, limits=self._limits
        )
        self._spec = loader.from_raw(self._copy, source="flow")
        today = await self._local_today()
        self._version = self._spec.version_at(today)
        self._summary = loader.summarize(self._spec, today)

    async def async_step_tariff_steps(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the steps from the bill - the country's rule has no numbers of its own (D2 §6)."""
        if user_input is None:
            return self._form(
                "tariff_steps", steps.steps_schema(self._currency, values=self._steps)
            )
        try:
            rows = steps.step_rows(user_input)
        except steps.StepError as err:
            return self._form(
                "tariff_steps",
                steps.steps_schema(self._currency, values=user_input),
                errors={err.field: err.key},
            )
        self._steps = dict(user_input)
        if not rows:
            # No bill at hand: the safe default is no capacity component, as
            # `custom` - the summary says so, and a reconfigure can add it later.
            self._preset_file = steps.PRESET_CUSTOM
            self._raw = await self.hass.async_add_executor_job(
                steps.preset_raw, steps.PRESET_CUSTOM
            )
        await self._apply_tariff()
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
        """Ask "Stemmer dette med nettleiefakturaen din?" - yes, or the list again (INV-67, HUB-4)."""
        assert self._spec is not None
        assert self._summary is not None
        if user_input is None:
            text = await Text.load(self.hass)
            return self._form(
                "tariff_preset",
                steps.tariff_confirm_schema(),
                placeholders={
                    "name": self._tariff_name(text),
                    "table": tariff_table(text, self._summary),
                    "source": self._summary.source_url or text.word("text", "none"),
                },
            )
        if user_input.get("confirm") == steps.TARIFF_NO:
            # HA flows have no back: "no" shows the grid companies again, where
            # "Finner ikke mitt" and "Legg inn selv" are (HUB-4).
            return await self.async_step_tariff()
        return await self.async_step_tariff_target()

    async def async_step_tariff_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask "Hvilket effekttrinn vil du holde deg i?" - the target and how strictly (D2 §6)."""
        assert self._version is not None
        text = await Text.load(self.hass)
        hours = {"hours": text.number(steps.peak_hours(self._version))}
        if user_input is None:
            return self._form(
                "tariff_target",
                steps.tariff_target_schema(self._version, text=text, values=self._target),
                placeholders=hours,
            )
        answers = self._flat(user_input)
        try:
            steps.validate_target(answers, self._version, self._profile)
        except steps.StepError as err:
            return self._form(
                "tariff_target",
                steps.tariff_target_schema(self._version, text=text, values=self._target),
                errors={err.field: err.key},
                placeholders=hours,
            )
        self._target = answers
        if steps.needs_bills(self._version):
            return await self.async_step_tariff_bills()
        if steps.needs_limits(self._version):
            return await self.async_step_tariff_limits()
        return await self.async_step_export()

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
        return await self.async_step_export()

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
        # The household's contract, not the starting value, is what the site keeps.
        await self._apply_tariff()
        return await self.async_step_export()

    # ------------------------------------------------- presence, notifications
    # The hard-limit step is gone: its answer was read by nothing, and the grense
    # is D3's fuse and per-phase limit (D8 §5.15 S2, review NEW-1).

    async def async_step_presence(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Presence: automatic from `person` entities, or set by hand (D8 §5.1)."""
        if user_input is None:
            return self._form("presence", steps.presence_schema(self.hass, values=self._presence))
        answers = self._flat(user_input)
        delay = seconds_of(answers.get("away_delay_min"))
        mode = str(answers.get("mode", "auto"))
        self._presence = {
            "mode": mode,
            "persons": list(self._presence.get("persons") or []) if mode == "auto" else [],
            "away_delay_min": 30 if delay is None else round(delay / 60),
        }
        if mode == "auto":
            return await self.async_step_presence_persons()
        return await self.async_step_notifications()

    async def async_step_presence_persons(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Whom automatic presence follows - asked only after "auto" (HUB-17)."""
        if user_input is None:
            return self._form(
                "presence_persons", steps.persons_schema(self.hass, values=self._presence)
            )
        self._presence["persons"] = list(user_input.get("persons") or [])
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
        # A half-hour select answers `22:00`; stored `22:00:00` as a time selector's was.
        self._quiet = [
            f"{str(answers.get('quiet_start', QUIET_START_DEFAULT))[:5]}:00",
            f"{str(answers.get('quiet_end', QUIET_END_DEFAULT))[:5]}:00",
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
                copy=self._copy,
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
            CONF_PRESENCE: self._presence,
            CONF_NOTIFICATIONS: self._notifications,
            CONF_QUIET_HOURS: self._quiet,
            CONF_ACTIVE: False,
        }

    def _preset_components(self) -> list[str]:
        """Return the add-ons the chosen grid company's preset already prices (D2 §6, D-0126).

        They come with the preset, so the add-on step does not offer them: a
        household that ticked the day/night charge by hand used to replace the
        preset's numbers with its own empty ones (D-0430).
        """
        if self._path is not OnboardingPath.FULL or self._version is None:
            return []
        registered = set(modifiers.keys())
        # A grid charge published with the levies included already carries them,
        # so the levy add-on is not offered on top (D-0523). VAT is: the preset's
        # VAT covers its own charge, the household's VAT still taxes the spot price.
        levies = [
            key for key in self._version.energy_components.get("includes") or () if key == "levy"
        ]
        return [key for key in (*self._version.energy_components, *levies) if key in registered]

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
                steps.review_schema(reconfiguring=self._reconfiguring),
                placeholders={
                    **await review.placeholders(self.hass, data, tariff=await self._tariff_line()),
                    "first": (await Text.load(self.hass)).word(
                        "review",
                        "first_reconfigure" if self._reconfiguring else "first_setup",
                        name=self._name,
                    ),
                },
                last_step=True,
            )
        # A reconfigure keeps the site's own state; only a first setup asks (HUB-5).
        data[CONF_ACTIVE] = (
            self._active if self._reconfiguring else not user_input.get("start_in_observe", True)
        )

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
