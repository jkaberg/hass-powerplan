"""D8 §9 18: the text a household reads is never raw, and never English in `nb`.

The UX review's guardrail (`design/reviews/ux-review.md` §0) as tests on
the rendered flow rather than as a runtime warning (D8 §5.15 item map, §0):

* **18a** every `nb` string differs from its `en` string outside an allow-list of
  names, units and numbers, and `nb` writes a decimal with a comma (review R3);
* **18b** every selector option, entity state, event type and flow error code has
  a translation in both languages (R4; the water heater's `unsafe_switch` had
  none, NEW-2);
* **18c** every step of every flow, walked in both languages over the reference
  house's registry, fills its placeholders with data only - no internal key
  (`^[a-z_]+(:<digits>)?$`), no entity id - and reads in the language it claims, with
  its numbers formatted for it (R1, R2).

* **18d**  no screen uses the design's own words (§5.15 rule 7): not a
  translation string, not a rendered flow step, not `notifications.py`'s two
  dictionaries - the household's glossary (HLD §2) instead.

The core half (`render_plain_language` gone, core returns a `TariffSummary`) is
`tests/core/tariffs/test_presets.py`.
"""

from __future__ import annotations

import json
import re
import zoneinfo
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectSelector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import (
    DOMAIN,
    SUBENTRY_CIRCUIT,
    SUBENTRY_GROUP,
    SUBENTRY_LOAD,
    SUBENTRY_ZONE,
    TRANSPORTS,
)
from custom_components.powerplan.core.engine import EventKind
from custom_components.powerplan.core.loads.questionnaire import QCtx, QuestionKind
from custom_components.powerplan.core.loads.types import base as device_types
from custom_components.powerplan.core.pricing import Carrier, modifiers
from custom_components.powerplan.core.tariffs.evaluator import ADVICE_KEYS
from custom_components.powerplan.core.tariffs.model import StepTable
from custom_components.powerplan.core.tariffs.rules import loader
from custom_components.powerplan.flow import steps as site_steps
from custom_components.powerplan.flow.load import explanation_text, option_key
from custom_components.powerplan.flow.text import Text, preset_options, target_options, tariff_table
from custom_components.powerplan.load_entities import CONTROL_OPTIONS, PLAN_STATES, PRIORITY_LEVELS
from custom_components.powerplan.notifications import TEXTS as NOTIFICATION_TEXTS
from custom_components.powerplan.providers.prices.formats import registry as formats
from custom_components.powerplan.providers.profiles import registry as profiles
from custom_components.powerplan.select import PRESENCE_OPTIONS, RISK_OPTIONS
from custom_components.powerplan.sensor import ADVICE_STATES
from tests.core.tariffs.conftest import market_golden, preset_names, shipped_spec

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from tests.e2e.fake_house import FakeHouse

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "powerplan"
LANGUAGES = ("en", "nb")

#: Values that are the same in both languages by construction - a name, a code,
#: a word both languages spell alike - and nothing else (review R3). A new entry
#: here is a decision to review, never a way to make the test pass.
SAME_IN_BOTH = frozenset(
    {
        "PowerPlan",  # the brand (BR-1)
        "Nord Pool",  # a market operator
        "Easee (Bluetooth)",  # a product and a transport
        "OK",
        "LFP",  # battery chemistries
        "NMC",
        "Vinyl",  # a floor covering, one word in both
        "Pellets",  # a fuel, one word in both
        "Plan",  # `sensor.<…>_plan`, renamed by U.4 (ENT-22)
        "Normal",  # the middle priority (D-0411), one word in both
        "Status",  # the charger's status role (review LOAD-1)
        "{min} min",  # the gauge's minutes left, a unit in both (D12 §5.3)
        "≈ {cost}",  # a slot's or a run's cost, the currency in the number (D12 §5.11 G7)
        "kW",  # a unit
        "—",  # nothing
        "April",  # months spelled the same in nb (`selector.month`)
        "August",
        "September",
        "November",
        "Norgespris",  # the state's fixed-price scheme, a name (D1 §6)
        # Nord Pool areas whose region is spelled alike in both (CTL-9)
        "SE1 – Luleå",
        "SE2 – Sundsvall",
        "SE3 – Stockholm",
        "SE4 – Malmö",
        "FI – Finland",
        "LV – Latvia",
    }
)
#: A number with or without a unit is the same in both languages (a whole number;
#: a decimal is not, because nb writes it with a comma).
NUMBER = re.compile(r"^[0-9][0-9–]*( (A|L|kW|kWh|V|%))?$")
#: A price format is named after the integration it reads: a brand in both
#: languages. The two generic shapes are words and are not on this list.
BRANDS = frozenset(
    f"selector.price_format.options.{key}"
    for key in (
        "amber",
        "comed",
        "easyenergy_action",
        "energidataservice",
        "energyzero_action",
        "entsoe",
        "nordpool_core",
        "nordpool_hacs",
        "octopus_energy",
        "pvpc",
        "tge",
        "tibber_action",
    )
)
#: Inline code is syntax the household types, not a number it reads (`-10:2.1`).
CODE = re.compile(r"`[^`]*`")

#: An internal key in a placeholder (review §0) - and an entity id anywhere.
KEY = re.compile(r"^[a-z_]+(:\d+)?$")
ENTITY_ID = re.compile(
    r"\b(?:sensor|binary_sensor|switch|number|select|climate|water_heater|button|person|"
    r"weather|calendar|schedule|input_boolean|light|notify|device_tracker|event|time)\."
    r"[a-z0-9_]+\b"
)
PLACEHOLDER = re.compile(r"\{([a-z_0-9]+)\}")
URL = re.compile(r"https?://\S+|/config/[^\s)]+")
WORD = re.compile(r"[a-zæøåäö']+")
POINT_DECIMAL = re.compile(r"\d\.\d")
COMMA_DECIMAL = re.compile(r"\d,\d(?!\d\d)")
#: English words that are not also Norwegian: one of them in an `nb` screen is an
#: English string the translation missed (review R1, R3).
ENGLISH = frozenset(
    {
        "the", "and", "of", "with", "without", "your", "you", "this", "would", "will",
        "month", "months", "about", "found", "more", "not", "configured", "from", "bills",
        "hours", "average", "highest", "yes", "car", "charging", "savings", "against",
        "its", "quiet", "three", "phase", "single", "neutral", "steps", "only", "counts",
        "between", "limited", "exceeding", "trips", "supply", "automatic", "defend",
        "custom", "listed", "know", "each", "which", "what", "when", "where", "should",
        "could", "have", "has", "been", "were", "was", "sure", "appliance", "device",
        "heat", "pump", "floor", "room", "run", "runs", "day", "days", "week", "year",
        "price", "prices", "fee", "free", "first", "never", "always", "other", "hour",
        "half", "quarter", "charge", "energy", "grid", "power", "connection", "usage",
        "learn", "learned", "temperature", "offered", "weeks", "none", "step", "markup",
        "setpoint", "switch", "enable", "current", "rating", "session", "outdoor",
        "outlet", "door", "battery", "blocked", "maximum", "boost", "thermostat",
    }
)  # fmt: skip
#: The design's own words (HLD §2's vocabulary and the UX review's §3 "now" column,
#: metaphors included), which no screen shows: the household's glossary replaces
#: them (D8 §5.15 rule 7). "Time zone" and "tidssone" are not a zone; "sats" is
#: VAT's rate, "satse" the bet the review removed.
DESIGN_WORDS = {
    "en": re.compile(
        r"\b(?:sites?|loads?|(?<!time )zones?|observ\w*|shed(?:s|ding)?|baselines?|"
        r"carriers?|modifiers?|safe mode|force mode|boost\w*|delegated|ceilings?|"
        r"allowance|stages?|engine|counterfactual|forced?)\b",
        re.IGNORECASE,
    ),
    "nb": re.compile(
        r"\b(?:anlegg\w*|nettilknytningspunkt\w*|hub|last|laster|lasten|lastene|lastens|"
        r"kurs|kursen|kurser|sone|sonen|soner|sonene|kapasitetstrinn\w*|tak|taket|"
        r"forsvar\w*|kutt\w*|utkobl\w*|tvang\w*|tvungen|tvunget|boost\w*|overstyr\w*|"
        r"observ\w*|sikker modus|grunnlinj\w*|grunnlast\w*|bærer\w*|modifikator\w*|"
        r"satse|sats på|slapp alt fritt|taus|død|motoren|tikk\w*|kontrafakt\w*|"
        r"verdt en avbrytelse)\b",
        re.IGNORECASE,
    ),
}


# --------------------------------------------------------------------------- #
# The translation documents
# --------------------------------------------------------------------------- #


def _document(language: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (INTEGRATION / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )
    return loaded


def _leaves(node: Any, prefix: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_leaves(value, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: node}


DOCUMENTS = {language: _document(language) for language in LANGUAGES}
STRINGS = {language: _leaves(document) for language, document in DOCUMENTS.items()}


def text_for(language: str) -> Text:
    """Return the `Text` Home Assistant would build from one translation file."""
    return Text(
        language,
        {f"component.{DOMAIN}.{path}": value for path, value in STRINGS[language].items()},
    )


def _has(language: str, path: str) -> bool:
    return path in STRINGS[language]


# --------------------------------------------------------------------------- #
# 18a - nb is Norwegian
# --------------------------------------------------------------------------- #


def test_18a_every_nb_string_differs_from_en_outside_the_allow_list() -> None:
    """Review R3: 83 nb values equalled en, about 50 of them English."""
    english, norwegian = STRINGS["en"], STRINGS["nb"]
    copied = sorted(
        path
        for path, value in english.items()
        if norwegian.get(path) == value
        and value not in SAME_IN_BOTH
        and path not in BRANDS
        and not NUMBER.match(value)
    )
    assert not copied, f"nb repeats en: {copied}"


def test_18a_nb_writes_a_decimal_with_a_comma() -> None:
    """Review R3: `selector.water_heater_element_kw` said "1.5 kW" in nb."""
    pointed = sorted(
        path for path, value in STRINGS["nb"].items() if POINT_DECIMAL.search(CODE.sub("", value))
    )
    assert not pointed, f"nb decimals with a point: {pointed}"


def test_18a_the_brand_is_powerplan_in_every_string() -> None:
    """Review BR-1: "PowerPlan" in both files and every string; a translation `title`."""
    for language in LANGUAGES:
        assert DOCUMENTS[language]["title"] == "PowerPlan"
        lower = sorted(path for path, value in STRINGS[language].items() if "powerplan" in value)
        assert not lower, f"{language}: {lower}"


# --------------------------------------------------------------------------- #
# 18b - every option, state, event type and error code is translated
# --------------------------------------------------------------------------- #


def _slug(value: str) -> bool:
    """Whether a value can be a translation key at all (hassfest's own rule)."""
    return re.fullmatch(r"(?![_-])[a-z0-9_-]+(?<![_-])", value) is not None


def _registry_options() -> dict[str, set[str]]:
    """Every selector vocabulary a registry renders, by translation key (D8 §5.4)."""
    wanted: dict[str, set[str]] = {
        "load_type": set(device_types.keys()),
        "load_profile": set(profiles.keys()),
        "strategy": set(),
        "modifier": {key for key in modifiers.keys() if key != "export_price"},  # noqa: SIM118
        "carrier": {str(c) for c in Carrier if c is not Carrier.ELECTRICITY},
        "carrier_mode": {site_steps.CARRIER_FIXED, site_steps.CARRIER_SENSOR},
        "price_source": set(site_steps.PRICE_SOURCES),
        "transport": set(TRANSPORTS),
        "risk": set(site_steps.RISK_LABELS),
        "presence_mode": {"auto", "manual"},
        "phases": {"1", "3"},
        "price_format": set(formats.keys()),
    }
    export = next(f for f in modifiers.entry("export_price").schema if f.key == "mode")
    wanted["export_mode"] = {site_steps.EXPORT_NONE, *(str(o) for o in export.options)}
    for key in device_types.keys():  # noqa: SIM118 - the registry's function
        device_type = device_types.get(key)
        wanted["strategy"] |= set(device_type.strategies)
        for question in device_type.questionnaire.questions:
            if question.kind is QuestionKind.CHOICE:
                wanted[f"{key}_{question.key}"] = {
                    option_key(str(option.value)) for option in question.options
                }
    for key in modifiers.keys():  # noqa: SIM118
        for field in modifiers.entry(key).schema:
            values = {str(option) for option in field.options}
            if values and all(_slug(value) for value in values) and field.key != "mode":
                wanted[f"modifier_{key}_{field.key}"] = values
    return wanted


@pytest.mark.parametrize("language", LANGUAGES)
def test_18b_every_selector_option_a_registry_renders_is_translated(language: str) -> None:
    """HUB-10: `render()` asked `selector.modifier_cumulative_tier_basis`, which no file had."""
    missing = sorted(
        f"selector.{key}.options.{value}"
        for key, values in _registry_options().items()
        for value in values
        if not _has(language, f"selector.{key}.options.{value}")
    )
    assert not missing, missing


def _codes(pattern: str, *paths: Path) -> set[str]:
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    return set(re.findall(pattern, source, flags=re.DOTALL))


@pytest.mark.parametrize("language", LANGUAGES)
def test_18b_every_flow_error_code_is_translated(language: str) -> None:
    """NEW-2: `unsafe_switch` was raised and translated nowhere; every code, every flow."""
    core = INTEGRATION / "core"
    answer_codes = _codes(r'AnswerError\(\s*[^,()]+,\s*"([a-z_]+)"', *core.rglob("*.py"))
    assert "unsafe_switch" in answer_codes
    step_codes = _codes(r'StepError\(\s*[^,()]+,\s*"([a-z_]+)"', INTEGRATION / "flow" / "steps.py")
    sections = {
        "config": step_codes
        | _codes(r'errors\[[^\]]+\] = "([a-z_]+)"', INTEGRATION / "config_flow.py"),
        "config_subentries.load": answer_codes
        | _codes(r'errors\[[^\]]+\] = "([a-z_]+)"', INTEGRATION / "flow" / "load.py"),
    }
    for kind in ("circuit", "group", "zone"):
        sections[f"config_subentries.{kind}"] = _codes(
            r'errors\[[^\]]+\] = "([a-z_]+)"', INTEGRATION / "flow" / f"{kind}.py"
        )
    missing = sorted(
        f"{prefix}.error.{code}"
        for prefix, codes in sections.items()
        for code in codes
        if not _has(language, f"{prefix}.error.{code}")
    )
    assert not missing, missing
    aborts = {
        "config": _codes(r'reason="([a-z_]+)"', INTEGRATION / "config_flow.py"),
        **{
            f"config_subentries.{kind}": _codes(
                r'reason="([a-z_]+)"', INTEGRATION / "flow" / f"{kind}.py"
            )
            for kind in ("load", "circuit", "group", "zone")
        },
    }
    unexplained = sorted(
        f"{prefix}.abort.{reason}"
        for prefix, reasons in aborts.items()
        for reason in reasons
        if not _has(language, f"{prefix}.abort.{reason}")
    )
    assert not unexplained, unexplained


def _max_steps() -> int:
    most = 0
    for name in preset_names():
        if name == "no/template":
            # Its steps are the household's; the steps form takes at most STEP_ROWS.
            most = max(most, site_steps.STEP_ROWS)
            continue
        for version in shipped_spec(name).versions:
            peak = version.peak
            if peak is not None and isinstance(peak.pricing, StepTable):
                most = max(most, len(peak.pricing.steps))
    return most


@pytest.mark.parametrize("language", LANGUAGES)
def test_18b_every_entity_state_and_event_type_is_translated(language: str) -> None:
    """R4: the advice, the target, an appliance's plan and control, and the event types."""
    states: dict[str, set[str]] = {
        "entity.sensor.advice.state": set(ADVICE_STATES),
        "entity.sensor.plan_status.state": set(PLAN_STATES),
        "entity.select.control.state": set(CONTROL_OPTIONS),
        "entity.select.priority.state": set(PRIORITY_LEVELS),
        "entity.select.presence.state": set(PRESENCE_OPTIONS),
        "entity.select.risk.state": set(RISK_OPTIONS),
        "entity.select.target.state": {"auto", "kw"}
        | {f"step_{index}" for index in range(_max_steps())},
        "entity.event.events.state_attributes.event_type.state": {kind.value for kind in EventKind},
    }
    missing = sorted(
        f"{prefix}.{value}"
        for prefix, values in states.items()
        for value in values
        if not _has(language, f"{prefix}.{value}")
    )
    assert not missing, missing
    assert _has(language, "entity.event.events.name")
    assert _has(language, "entity.sensor.next_legionella.name")


def test_18b_the_advice_sensor_is_an_enum_over_d2s_keys() -> None:
    """ENT-1: D2 §5.11's keys and `all_good`; never `top_entries`, which is data."""
    assert "top_entries" not in ADVICE_STATES
    assert set(ADVICE_STATES) == (set(ADVICE_KEYS) - {"top_entries"}) | {"all_good"}
    emitted = _codes(r'key="([a-z_]+)"', INTEGRATION / "core" / "tariffs" / "evaluator.py")
    assert emitted <= set(ADVICE_KEYS), emitted - set(ADVICE_KEYS)


# --------------------------------------------------------------------------- #
# 18c - a rendered screen: data in the placeholders, the language it claims
# --------------------------------------------------------------------------- #


def _names(hass: HomeAssistant) -> set[str]:
    """Return the household's own names: devices, entities, subentries, persons (D8 §5.15)."""
    names: set[str] = set()
    for device in dr.async_get(hass).devices:
        names |= {name for name in (device.name, device.name_by_user) if name}
    for entry in er.async_get(hass).entities.values():
        names |= {name for name in (entry.name, entry.original_name) if name}
    for config_entry in hass.config_entries.async_entries():
        names.add(config_entry.title)
        names |= {subentry.title for subentry in config_entry.subentries.values()}
    names |= {
        str(state.attributes.get("friendly_name")) for state in hass.states.async_all("person")
    }
    return names


def _selectors(schema: Any) -> list[SelectSelector[Any]]:
    found: list[SelectSelector[Any]] = []
    for value in schema.schema.values():
        if isinstance(value, section):
            found.extend(_selectors(value.schema))
        elif isinstance(value, SelectSelector):
            found.append(value)
    return found


def _strip(text: str, names: set[str]) -> str:
    """Remove what is data by construction: the household's names, URLs, inline code."""
    text = CODE.sub(" ", URL.sub(" ", text))
    for name in sorted(names, key=len, reverse=True):
        if name:
            text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", " ", text)
    return text


def assert_in_language(text: str, language: str, names: set[str], where: str) -> None:
    """No English in nb; decimals written the language's way (R1, R3)."""
    plain = _strip(text, names)
    if language == "nb":
        english = sorted(set(WORD.findall(plain.lower())) & ENGLISH)
        assert not english, f"{where}: English in nb: {english} — {text!r}"
        assert not POINT_DECIMAL.search(plain), f"{where}: point decimal in nb — {text!r}"
    else:
        assert not COMMA_DECIMAL.search(plain), f"{where}: comma decimal in en — {text!r}"


def assert_household_words(text: str, language: str, names: set[str], where: str) -> None:
    """No design word on a screen; the household's glossary instead (§5.15 rule 7)."""
    plain = PLACEHOLDER.sub(" ", _strip(text, names))
    found = sorted({match.lower() for match in DESIGN_WORDS[language].findall(plain)})
    assert not found, f"{where}: design words {found} — {text!r}"


def assert_data_only(value: str, names: set[str], where: str) -> None:
    """Assert a placeholder carries data: never an internal key, never an entity id (R2)."""
    if value in names:
        return
    assert not KEY.match(value), f"{where}: internal key {value!r}"
    assert not ENTITY_ID.search(_strip(value, names)), f"{where}: entity id in {value!r}"


def check_screen(result: dict[str, Any], language: str, names: set[str], section_path: str) -> None:
    """Check one shown step of a flow in one language."""
    step_id = result["step_id"]
    body = DOCUMENTS[language]
    for key in section_path.split("."):
        body = body[key]
    body = body["step"][step_id]
    where = f"{section_path}.step.{step_id}"
    if result["type"] is FlowResultType.MENU:
        for option in result["menu_options"]:
            assert option in body.get("menu_options", {}), f"{where}: menu option {option}"
        return
    placeholders = {
        key: str(value) for key, value in (result["description_placeholders"] or {}).items()
    }
    for key, value in placeholders.items():
        assert_data_only(value, names, f"{where}.{key}")
    shown: list[str] = list(placeholders.values())
    for part in ("title", "description"):
        template = body.get(part, "")
        missing = set(PLACEHOLDER.findall(template)) - set(placeholders)
        assert not missing, f"{where}.{part}: placeholders never filled: {missing}"
        shown.append(template.format_map(placeholders) if placeholders else template)
    for selector in _selectors(result["data_schema"]):
        config = selector.config
        key = config.get("translation_key")
        for option in config["options"]:
            value = option["value"] if isinstance(option, dict) else str(option)
            label = option["label"] if isinstance(option, dict) else str(option)
            if key is not None and _slug(value):
                assert _has(language, f"selector.{key}.options.{value}"), (
                    f"{where}: selector.{key}.options.{value}"
                )
                continue
            assert_data_only(label, names, f"{where} option {value}")
            shown.append(label)
    assert_in_language("\n".join(shown), language, names, where)
    assert_household_words("\n".join(shown), language, names, where)


async def _walk(
    hass: HomeAssistant,
    result: dict[str, Any],
    *,
    configure: Any,
    language: str,
    section_path: str,
    answers: dict[str, dict[str, Any]],
    names: set[str],
) -> dict[str, Any]:
    """Answer every step with its defaults plus `answers[step]`, checking each screen."""
    seen: list[str] = []
    for _ in range(40):
        if result["type"] not in (FlowResultType.FORM, FlowResultType.MENU):
            return result
        step_id = result["step_id"]
        seen.append(step_id)
        assert not result.get("errors"), f"{step_id}: {result['errors']}"
        check_screen(result, language, names | _names(hass), section_path)
        if result["type"] is FlowResultType.MENU:
            user_input = answers[step_id]
        else:
            user_input = {**result["data_schema"](answers.get(step_id, {}))}
        result = dict(await configure(result["flow_id"], user_input))
    pytest.fail(f"the flow never finished: {seen}")


#: One walk per branch of the site flow (D8 §5.1): every step it can show.
SITE_WALKS: dict[str, tuple[dict[str, Any], dict[str, dict[str, Any]]]] = {
    "full_no": (
        {"country": "NO"},
        {
            "user": {"next_step_id": "full"},
            "name": {"name": "Hjemme"},
            # Tensio prices the day/night charge itself, levies included, so
            # neither is offered (D-0430, D-0523); both screens are walked on the
            # price-only branch below.
            "modifiers": {
                "modifiers": [
                    k
                    for k in modifiers.keys()  # noqa: SIM118
                    if k not in ("export_price", "tou_schedule", "levy")
                ]
            },
            # Every add-on ticked: the required fields with no default (D8 §9
            # 21 (a)) each need one row or one number to get past their step.
            "modifier_fixed_price": {"price": 40},
            "modifier_subsidy_threshold": {"threshold": 90},
            "modifier_cumulative_tier": {"tiers": [{"price": 100}]},
            "modifier_day_type": {"rates": [{"type": "weekday", "price": 50}]},
            "modifier_tou_schedule": {"periods": [{"price": 60}]},
            "export": {"mode": "spot_minus"},
            "carriers": {"carriers": [str(c) for c in Carrier if c is not Carrier.ELECTRICITY]},
            "tariff": {"preset": "no/tensio-ts"},
        },
    ),
    "full_not_listed": (
        {"country": "NO"},
        {
            "user": {"next_step_id": "full"},
            "name": {"name": "Hjemme"},
            "tariff": {"preset": "unknown"},
            # The Norwegian template: the steps from the household's bill (D2 §6).
            "tariff_steps": {"fee_1": 150, "fee_2": 250, "fee_3": 420},
        },
    ),
    "full_custom": (
        {"country": "NO"},
        {
            "user": {"next_step_id": "full"},
            "name": {"name": "Hjemme"},
            "tariff": {"preset": "custom"},
        },
    ),
    "full_rolling": (
        {"country": "BE"},
        {
            "user": {"next_step_id": "full"},
            "name": {"name": "Hjemme"},
            "tariff": {"preset": "be/fluvius-imewo"},
        },
    ),
    "full_contracted": (
        {"country": "ES"},
        {
            "user": {"next_step_id": "full"},
            "name": {"name": "Hjemme"},
            # ES has no Nord Pool area, so the default source is fixed - and its
            # price is required, no default (D8 §9 21 (a)).
            "prices_fixed": {"price": 100},
            "tariff": {"preset": "es/2_0td"},
        },
    ),
    "price_only_entity": (
        {"country": "NO", "time_zone": ""},
        {
            "user": {"next_step_id": "price_only"},
            "name": {"name": "Hjemme"},
            "timezone": {"timezone": "Europe/Oslo"},
            "prices": {"source": "entity"},
        },
    ),
    "price_only_fixed": (
        {"country": "NO"},
        {
            "user": {"next_step_id": "price_only"},
            "name": {"name": "Hjemme"},
            "prices": {"source": "fixed"},
            # Required, no default (D8 §9 21 (a)): nothing to derive it from.
            "prices_fixed": {"price": 100},
            "modifiers": {"modifiers": ["vat", "tou_schedule", "levy"]},
            "modifier_levy": {"amount": 7},
            "modifier_tou_schedule": {"periods": [{"price": 60}]},
        },
    ),
    "fuse_only": (
        {"country": "NO"},
        {"user": {"next_step_id": "fuse_only"}, "name": {"name": "Hjemme"}},
    ),
}


def _phone(hass: HomeAssistant) -> None:
    """Add a phone with the companion app; its notify service is labelled by its name (HUB-15)."""
    entry = MockConfigEntry(
        domain="mobile_app", title="Karis telefon", data={"device_name": "Karis telefon"}
    )
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mobile_app", "kari-phone")},
        name="Karis telefon",
    )
    hass.services.async_register("notify", "mobile_app_karis_telefon", lambda call: None)


@pytest.mark.usefixtures("ams_meter", "nordpool_entry", "persons")
@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("walk", list(SITE_WALKS))
async def test_18c_every_site_step_renders_data_only_in_the_language(
    hass: HomeAssistant, language: str, walk: str
) -> None:
    """Every branch of the site flow, both languages: R1, R2, HUB-7, 11–15."""
    environment, answers = SITE_WALKS[walk]
    hass.config.language = language
    hass.config.currency = "NOK" if environment["country"] == "NO" else "EUR"
    hass.config.country = environment["country"]
    hass.config.time_zone = environment.get("time_zone", "Europe/Oslo")
    _phone(hass)
    # The grid companies' own names are data, in their own languages (D2 §6).
    names = {"Hjemme", "NO3"} | {loader.load_raw(name)["name"] for name in preset_names()}
    # So are the time zones' own names (`Swift_Current`, `Port_of_Spain`).
    names |= set(zoneinfo.available_timezones())

    result = dict(
        await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    )
    result = await _walk(
        hass,
        result,
        configure=hass.config_entries.flow.async_configure,
        language=language,
        section_path="config",
        answers=answers,
        names=names,
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert "description" not in (result["data"]["tariff"] or {}), "HUB-12: nothing stored"


#: The reference house's appliances, one per type it has (D9 §5.10).
HOUSE_LOADS = (
    "ev",
    "loop_bath_1",
    "loop_living",
    "tank",
    "heat_pump",
    "radiator_bed_1",
    "dishwasher",
    "sauna",
)


async def _subentry(
    hass: HomeAssistant,
    entry_id: str,
    kind: str,
    *,
    language: str,
    answers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result = dict(
        await hass.config_entries.subentries.async_init(
            (entry_id, kind), context={"source": SOURCE_USER}
        )
    )
    return await _walk(
        hass,
        result,
        configure=hass.config_entries.subentries.async_configure,
        language=language,
        section_path=f"config_subentries.{kind}",
        answers=answers,
        names=set(),
    )


async def _reconfigure(
    hass: HomeAssistant, site: MockConfigEntry, subentry_id: str, language: str
) -> None:
    subentry = site.subentries[subentry_id]
    result = dict(await site.start_subentry_reconfigure_flow(hass, subentry_id))
    result = await _walk(
        hass,
        result,
        configure=hass.config_entries.subentries.async_configure,
        language=language,
        section_path=f"config_subentries.{subentry.subentry_type}",
        answers={},
        names=set(),
    )
    assert result["type"] is FlowResultType.ABORT, result
    await hass.async_block_till_done()


@pytest.mark.parametrize("language", LANGUAGES)
async def test_18c_every_subentry_step_renders_data_only_in_the_language(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse, language: str
) -> None:
    """The load flow over every appliance of the reference house, then circuit, group, room.

    LOAD-1, LOAD-2, NEW-3, NEW-4 - and, with the house built, R4: every select,
    enum sensor and event entity of every appliance has its states translated.
    """
    hass.config.language = language
    ids: dict[str, str] = {}
    for load_id in HOUSE_LOADS:
        device_id = charger.loads[load_id].device_id
        assert device_id is not None
        result = await _subentry(
            hass,
            site.entry_id,
            SUBENTRY_LOAD,
            language=language,
            answers={
                "user": {"type": charger.loads[load_id].kind},
                "device": {"device": device_id},
                "review": {"name": load_id},
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY, result
        await hass.async_block_till_done()
        ids[load_id] = next(s.subentry_id for s in site.subentries.values() if s.title == load_id)

    for kind, name, members in (
        (SUBENTRY_CIRCUIT, "Garasjen", ["ev", "sauna"]),
        (SUBENTRY_GROUP, "Badegulvene", ["loop_bath_1", "loop_living"]),
        (SUBENTRY_ZONE, "Stuen", ["loop_living", "heat_pump"]),
    ):
        result = await _subentry(
            hass,
            site.entry_id,
            kind,
            language=language,
            answers={"user": {"name": name, "members": [ids[m] for m in members]}},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY, result
        await hass.async_block_till_done()

    for subentry_id in [ids["ev"], ids["tank"]] + [
        s.subentry_id for s in site.subentries.values() if s.subentry_type != SUBENTRY_LOAD
    ]:
        await _reconfigure(hass, site, subentry_id, language)

    untranslated: list[str] = []
    for entry in er.async_get(hass).entities.values():
        if entry.platform != DOMAIN or entry.translation_key is None:
            continue
        state = hass.states.get(entry.entity_id)
        if state is None:
            continue
        prefix = f"entity.{entry.domain}.{entry.translation_key}"
        if entry.domain == "select" or (
            entry.domain == "sensor" and state.attributes.get("device_class") == "enum"
        ):
            values, path = state.attributes.get("options", []), f"{prefix}.state"
        elif entry.domain == "event":
            values = state.attributes.get("event_types", [])
            path = f"{prefix}.state_attributes.event_type.state"
        else:
            continue
        untranslated.extend(
            f"{path}.{value}" for value in values if not _has(language, f"{path}.{value}")
        )
    assert not untranslated, sorted(set(untranslated))


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("type_key", device_types.keys())
def test_18c_every_load_type_explains_itself_in_words(type_key: str, language: str) -> None:
    """NEW-3: the review of all eight types - labels, choices, yes/no, numbers, the shadow."""
    device_type = device_types.get(type_key)
    ctx = QCtx(area=None, device_name="Apparat", phases=3)
    answers = device_type.questionnaire.validate({}, ctx)
    derived = device_type.derive(answers, ctx)
    text = explanation_text(text_for(language), derived, answers, type_key)
    assert not re.search(r"\b[a-z]+_[a-z_0-9]+\b", text), f"a raw key in {text!r}"
    assert "None" not in text
    assert_in_language(text, language, {"Apparat"}, f"{type_key} review")


MARKETS = tuple(name for name in preset_names() if market_golden(name) is not None)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("name", MARKETS)
def test_18c_every_market_renders_a_table_the_household_can_recognise(
    name: str, language: str
) -> None:
    """HUB-12, D2 §6: the golden's own words in en, Norwegian and comma decimals in nb."""
    data = market_golden(name)
    assert data is not None
    at = date.fromisoformat(f"{data['period_key']}-15")
    table = tariff_table(text_for(language), loader.summarize(shipped_spec(name), at=at))
    assert_in_language(table, language, set(), name)
    if language == "en":
        for fragment in data["describes"]:
            assert fragment in table, f"{name}: {fragment!r} missing from {table!r}"


def test_18c_numbers_money_and_lists_follow_the_language() -> None:
    """R1: "1 200 kr", "0,79", "25,1 kW" in nb; "1,200 kr", "0.79", "25.1 kW" in en."""
    nb, en = text_for("nb"), text_for("en")
    assert nb.money(Decimal("1200"), "NOK") == "1\u00a0200 kr"
    assert en.money(Decimal("1200"), "NOK") == "1,200 kr"
    assert (nb.number(0.79), en.number(0.79)) == ("0,79", "0.79")
    assert (nb.kw(25.1), en.kw(25.1)) == ("25,1 kW", "25.1 kW")
    assert nb.minor_per_kwh(Decimal("0.3604"), "NOK") == "36,04 øre/kWh"
    assert nb.join(["A", "B", "C"]) == "A, B og C"
    assert en.join(["A", "B"]) == "A and B"
    assert sorted(["Å", "Ø", "Æ", "Z", "A"], key=nb.sort_key) == ["A", "Z", "Æ", "Ø", "Å"]


def test_18c_grid_companies_sort_in_the_alphabet_with_the_escape_hatches_last() -> None:
    """HUB-11: operator names as data, A–Å in nb, "not listed" and "enter it myself" pinned last."""
    presets = [("x/o", "Østlandsnett"), ("x/a", "Årdal Energi"), ("x/e", "Elvia"), ("x/g", "Agder")]
    labels = [option["label"] for option in preset_options(text_for("nb"), presets)]
    assert labels == [
        "Agder",
        "Elvia",
        "Østlandsnett",
        "Årdal Energi",
        "Finner ikke mitt nettselskap",
        "Legg inn selv",
    ]


def test_18c_the_target_options_are_assembled_in_the_language() -> None:
    """HUB-13, ENT-2: "Automatisk", then "Trinn N · range · fee"; values `step_<i>`."""
    version = loader.load("no/tensio-ts").version_at(date(2026, 9, 19))
    nb = target_options(text_for("nb"), version)
    assert nb[0] == {"value": "auto", "label": "Automatisk"}
    assert nb[2] == {"value": "step_1", "label": "Trinn 2 · 2–5 kW · 233 kr/mnd"}
    assert nb[-1] == {"value": "step_14", "label": "Trinn 15 · Over 500 kW · 21 473 kr/mnd"}  # noqa: RUF001 - nb groups thousands with a no-break space
    en = target_options(text_for("en"), version)
    assert en[-1]["label"] == "Step 15 · Above 500 kW · 21,473 kr/month"


# --------------------------------------------------------------------------- #
# 18d - the household's words, never the design's
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("language", LANGUAGES)
def test_18d_no_string_uses_a_design_word(language: str) -> None:
    """Rule 7 over every string: flows, entities, repairs, actions and errors (U.3)."""
    for path, value in STRINGS[language].items():
        assert_household_words(value, language, set(), path)


@pytest.mark.parametrize("language", LANGUAGES)
def test_18d_no_notification_uses_a_design_word(language: str) -> None:
    """The same scan over `notifications.py`'s dictionaries (NEW-5), and the brand spelled."""
    texts = NOTIFICATION_TEXTS[language]
    assert texts.keys() == NOTIFICATION_TEXTS["en"].keys()
    for category, (title, body) in texts.items():
        for part, text in (("title", title), ("body", body)):
            assert_household_words(text, language, set(), f"notifications.{category}.{part}")
            assert "powerplan" not in text, f"notifications.{category}.{part}: {text!r}"
        if language == "nb":
            shown = PLACEHOLDER.sub(" ", f"{title}\n{body}")
            assert_in_language(shown, language, set(), f"notifications.{category}")


def test_18d_the_design_words_are_caught() -> None:
    """The scan itself: each glossary row's old word is found, its household word is not."""
    for language, caught, allowed in (
        (
            "en",
            "The site sheds a load in safe mode",
            "The home pauses an appliance in fallback mode",
        ),
        (
            "nb",
            "Anlegget kutter en last i sikker modus",
            "Hjemmet setter et apparat på pause i nødmodus",
        ),
        ("en", "The zone's carrier and modifier", "The IANA time zone"),
        ("nb", "Sonens bærer, taket og kursen", "Tidssonen, sikringskursen og satsen"),
    ):
        assert DESIGN_WORDS[language].search(caught), caught
        assert not DESIGN_WORDS[language].search(allowed), allowed


#: Steps that are not a question by design: the reviews (INV-67) and the load's
#: questions, titled "Om {name}" (review LOAD-5).
NOT_A_QUESTION = frozenset({"review", "reconfigure_review", "questions"})


@pytest.mark.parametrize("language", LANGUAGES)
def test_15_every_step_is_titled_as_a_question(language: str) -> None:
    """D8 §5.15 rule 1, HUB-6: one question per screen, in the household's words."""
    document = DOCUMENTS[language]
    steps = {f"config.{key}": step for key, step in document["config"]["step"].items()}
    for kind, flow in document["config_subentries"].items():
        steps |= {f"{kind}.{key}": step for key, step in flow["step"].items()}
    statements = sorted(
        f"{where}: {step.get('title')}"
        for where, step in steps.items()
        if where.rsplit(".", 1)[1] not in NOT_A_QUESTION
        and not str(step.get("title", "")).endswith("?")
    )
    assert not statements, statements
