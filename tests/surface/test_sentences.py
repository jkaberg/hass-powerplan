"""D8 §9 19 and §5.15's entities table for the home device.

An entity's name and state read as a sentence ("Effekttrinn denne måneden:
2–5 kW"): every site entity in its normal state has a translated, non-unknown
state - except a `timestamp` sensor, a button and the event entity, whose state
HA owns (H2) - and every name is a translation in both languages. Beside it,
the rows of the entities table the home device carries: names by the tariff's
window length (NEW-9), kW for the allowance through `suggested_unit_of_measurement`
(ENT-10, H6), the price's ISO unit at two decimals (ENT-14, H9), `stage` numeric
and diagnostic (ENT-7, S3), savings always "Beregnet" (ENT-15, S4), the next risky
window on the peak warning (ENT-12, S5), the last decision as a code (ENT-21), and
the device's manufacturer, model, `model_id` and `sw_version` (BR-3).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.entity import (
    SITE_MANUFACTURER,
    git_commit,
    window_translation_key,
)
from custom_components.powerplan.sensor import DECISION_STATES, WINDOW_NAMED, decision_state
from tests.runtime.conftest import SITE_ENTRY_ID, advance, site_data

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime
    from tests.runtime.conftest import FakeMeter

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "powerplan"
LANGUAGES = ("en", "nb")
#: The states Home Assistant owns: a moment, not a word (D8 §5.15 H2).
HA_OWNED = frozenset({"button", "event"})
#: A site with no production bound has nothing to say in these two (D8 §5.5);
#: the money is priced from a load's first metered slot (`Engine._close_slots`),
#: and this site has no appliance - `test_f10_…` reads them known, on a charger site.
NOT_ON_THIS_SITE = frozenset({"production", "surplus", "cost", "savings"})


def _strings(language: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (INTEGRATION / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )
    return loaded


STRINGS = {language: _strings(language) for language in LANGUAGES}


def _entity(language: str, domain: str, key: str) -> dict[str, Any]:
    table: dict[str, Any] = STRINGS[language]["entity"].get(domain, {}).get(key, {})
    return table


def _site_entries(hass: HomeAssistant) -> list[er.RegistryEntry]:
    return [
        entry
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN and entry.config_entry_id == SITE_ENTRY_ID
    ]


def _state(hass: HomeAssistant, platform: str, key: str) -> Any:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{DOMAIN}_{SITE_ENTRY_ID}_{key}"
    )
    assert entity_id is not None, (platform, key)
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def _enable_everything(hass: HomeAssistant, site: MockConfigEntry) -> None:
    """Enable the entities a new site leaves off, so the check sees every one."""
    registry = er.async_get(hass)
    for entry in _site_entries(hass):
        if entry.disabled_by is not None:
            registry.async_update_entity(entry.entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(site.entry_id)
    await hass.async_block_till_done()


# --------------------------------------------------------------------------- #
# §9 19 - a sentence in both languages, never unknown in the normal state
# --------------------------------------------------------------------------- #


async def test_19_every_site_entity_reads_as_a_translated_known_state(
    hass: HomeAssistant, site: MockConfigEntry, meter: FakeMeter, freezer: FrozenDateTimeFactory
) -> None:
    """Every entity of the home device, enabled, after the first slot has priced."""
    await _enable_everything(hass, site)
    for minute in range(17):  # 09:59 → 10:16: the 10:00–10:15 slot closes and prices
        meter.set_power(1_500.0 + minute)
        await advance(hass, freezer, 60)
    runtime: Runtime = site.runtime_data
    await runtime.run_tick("test")
    await hass.async_block_till_done()

    unknown: list[str] = []
    untranslated: list[str] = []
    for entry in _site_entries(hass):
        key = entry.translation_key
        assert key is not None, f"{entry.entity_id} has no translation key"
        for language in LANGUAGES:
            name = _entity(language, entry.domain, key).get("name")
            if not name:
                untranslated.append(f"{language}: entity.{entry.domain}.{key}.name")
        assert (
            _entity("en", entry.domain, key)["name"] != _entity("nb", entry.domain, key)["name"]
        ), f"entity.{entry.domain}.{key}.name is English in nb"

        state = hass.states.get(entry.entity_id)
        assert state is not None, f"{entry.entity_id} was not enabled"
        suffix = entry.unique_id.removeprefix(f"{DOMAIN}_{SITE_ENTRY_ID}_")
        if entry.domain in HA_OWNED or suffix in NOT_ON_THIS_SITE:
            continue
        if state.attributes.get("device_class") == "timestamp" and suffix == "next_peak_warning":
            continue  # nothing risky coming is HA's own unknown (H2, S5)
        if state.state in ("unknown", "unavailable"):
            unknown.append(f"{entry.entity_id} = {state.state}")
            continue
        closed = (
            entry.domain in ("select", "binary_sensor", "switch")
            or state.attributes.get("device_class") == "enum"
        )
        if closed:
            for language in LANGUAGES:
                words = _entity(language, entry.domain, key).get("state", {})
                if state.state not in words:
                    untranslated.append(
                        f"{language}: entity.{entry.domain}.{key}.state.{state.state}"
                    )
    assert not unknown, unknown
    assert not untranslated, untranslated


@pytest.mark.parametrize("language", LANGUAGES)
def test_19_every_closed_state_of_the_home_device_is_translated(language: str) -> None:
    """The states a live site shows only now and then: the decision, the meter, the flags."""
    closed: dict[tuple[str, str], tuple[str, ...]] = {
        ("sensor", "reasons"): DECISION_STATES,
        ("sensor", "meter_health"): ("ok", "degraded", "stale"),
        ("sensor", "price_source_health"): ("ok", "stale", "dead"),
        ("switch", "active"): ("on", "off"),
        ("binary_sensor", "peak_warning"): ("on", "off"),
        ("binary_sensor", "prices_tomorrow"): ("on", "off"),
        ("binary_sensor", "meter_stale"): ("on", "off"),
        ("binary_sensor", "meter_degraded"): ("on", "off"),
        **{("binary_sensor", f"meter_seam_{m}"): ("on", "off") for m in (15, 30, 60)},
    }
    missing = [
        f"entity.{domain}.{key}.state.{value}"
        for (domain, key), values in closed.items()
        for value in values
        if value not in _entity(language, domain, key).get("state", {})
    ]
    assert not missing, missing


# --------------------------------------------------------------------------- #
# The window's length is in the name (NEW-9, ENT-8, ENT-9)
# --------------------------------------------------------------------------- #

WINDOW_WORDS = {
    "en": {60: "this hour", 30: "this half-hour", 15: "this quarter-hour"},
    "nb": {60: "denne timen", 30: "denne halvtimen", 15: "dette kvarteret"},
}


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("window_min", [15, 30, 60])
def test_19_names_that_mention_the_window_follow_window_min(language: str, window_min: int) -> None:
    """Names mention "denne timen" only for an hourly window; a 15-min market reads "dette kvarteret"."""
    for key in ("window_used", "window_projected", "ceiling"):
        translation_key = window_translation_key(key, window_min)
        assert translation_key == f"{key}_{window_min}"
        name = _entity(language, "sensor", translation_key)["name"]
        assert WINDOW_WORDS[language][window_min] in name, (language, key, name)
        others = [words for m, words in WINDOW_WORDS[language].items() if m != window_min]
        assert not any(words in name for words in others), name
    assert set(WINDOW_NAMED) >= {"window_used", "window_projected", "ceiling", "next_peak_warning"}
    for key in WINDOW_NAMED:
        assert _entity(language, "sensor", window_translation_key(key, window_min))["name"]
    assert _entity(language, "binary_sensor", window_translation_key("meter_seam", window_min))[
        "name"
    ]


async def test_19_a_quarter_hour_market_names_its_window_by_the_quarter(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """Fluvius measures 15 minutes: the translation key follows; the unique id does not (INV-50)."""
    data = site_data(hass, tariff="be/fluvius")
    entry = MockConfigEntry(domain=DOMAIN, title="Test site", entry_id=SITE_ENTRY_ID, data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.build.cfg.window_min == 15

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{SITE_ENTRY_ID}_window_used"
    )
    assert entity_id is not None, "the unique id keeps the key, not the window"
    registered = registry.async_get(entity_id)
    assert registered is not None
    assert registered.translation_key == "window_used_15"
    assert entity_id == "sensor.test_site_usage_this_quarter_hour"


async def test_19_an_hourly_market_names_its_window_by_the_hour(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """Tensio measures hours: "Usage this hour", "Target this hour", "Next risky hour"."""
    registry = er.async_get(hass)
    for key, name in (
        ("window_used", "Usage this hour"),
        ("window_projected", "Expected usage this hour"),
        ("ceiling", "Target this hour"),
        ("next_peak_warning", "Next risky hour"),
    ):
        state = _state(hass, "sensor", key)
        assert state.attributes["friendly_name"] == f"Test site {name}", state
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{DOMAIN}_{SITE_ENTRY_ID}_{key}"
        )
        assert entity_id is not None
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.translation_key == f"{key}_60"


# --------------------------------------------------------------------------- #
# Units and precision (ENT-8, 9, 10, 14)
# --------------------------------------------------------------------------- #


def _sensor_options(hass: HomeAssistant, key: str, domain: str = "sensor") -> dict[str, Any]:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{SITE_ENTRY_ID}_{key}"
    )
    assert entity_id is not None
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    return dict(entry.options.get(domain, {}))


async def test_the_allowance_is_in_kw_with_one_decimal_on_a_new_site(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """ENT-10: `suggested_unit_of_measurement` kW, applied when the entity is registered (H6)."""
    state = _state(hass, "sensor", "allowance")
    assert state.attributes["unit_of_measurement"] == "kW"
    assert state.attributes["device_class"] == "power"
    assert _sensor_options(hass, "allowance", "sensor.private") == {
        "suggested_unit_of_measurement": "kW"
    }
    assert _sensor_options(hass, "allowance")["suggested_display_precision"] == 1
    assert state.attributes["friendly_name"] == "Test site Power available now"


async def test_energy_and_price_precisions_and_the_price_unit(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """Window energy two decimals, the target one; the price `NOK/kWh` at two (H9)."""
    assert _sensor_options(hass, "window_used")["suggested_display_precision"] == 2
    assert _sensor_options(hass, "window_projected")["suggested_display_precision"] == 2
    assert _sensor_options(hass, "ceiling")["suggested_display_precision"] == 1
    assert _sensor_options(hass, "price")["suggested_display_precision"] == 2
    price = _state(hass, "sensor", "price")
    assert price.attributes["unit_of_measurement"] == "NOK/kWh", "a new unit is a new series"
    assert price.attributes["friendly_name"] == "Test site Electricity price now"


# --------------------------------------------------------------------------- #
# stage, savings, the next risky window, the last decision
# --------------------------------------------------------------------------- #


async def test_stage_stays_numeric_renamed_and_diagnostic(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """ENT-7, S3: an enum would drop the state class and raise `state_class_removed` (H9)."""
    state = _state(hass, "sensor", "stage")
    assert state.state == "0"
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["friendly_name"] == "Test site Control level"
    entity_id = state.entity_id
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.entity_category == "diagnostic"


@pytest.mark.parametrize(
    ("language", "cost", "savings"),
    [
        ("en", "Cost this month", "Estimated savings this month"),
        ("nb", "Kostnad denne måneden", "Beregnet besparelse denne måneden"),
    ],
)
def test_savings_are_always_an_estimate_for_the_month(
    language: str, cost: str, savings: str
) -> None:
    """ENT-15, S4: a name cannot follow trial mode (H3), so it always says "Beregnet"."""
    assert _entity(language, "sensor", "site_cost")["name"] == cost
    assert _entity(language, "sensor", "site_savings")["name"] == savings


async def test_the_site_money_sensors_use_their_own_names(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """The home's cost and savings; a load's own keep theirs (the unique ids unchanged)."""
    assert _state(hass, "sensor", "cost").attributes["friendly_name"] == "Test site Cost this month"
    assert (
        _state(hass, "sensor", "savings").attributes["friendly_name"]
        == "Test site Estimated savings this month"
    )
    assert _state(hass, "sensor", "plan").attributes["friendly_name"] == "Test site Planned usage"


async def test_the_next_risky_window_is_on_the_peak_warning(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """ENT-11, ENT-12, S5: "Nærmer seg målet" with `next_window_start`; the sensor is diagnostic."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test site",
        entry_id=SITE_ENTRY_ID,
        data=site_data(hass, target_kw=1.0),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime: Runtime = entry.runtime_data
    assert runtime.snapshot is not None
    assert runtime.snapshot.warnings, "1.5 kW under a 1 kW ceiling warns"

    warning = _state(hass, "binary_sensor", "peak_warning")
    assert warning.state == "on"
    start = datetime.fromisoformat(warning.attributes["next_window_start"])
    assert start == min(
        w.window_start for w in runtime.snapshot.warnings if w.window_start is not None
    )
    for language, words in (("en", "Approaching target"), ("nb", "Nærmer seg målet")):
        assert _entity(language, "binary_sensor", "peak_warning")["state"]["on"] == words
    for language, words in (("en", "Below target"), ("nb", "Under målet")):
        assert _entity(language, "binary_sensor", "peak_warning")["state"]["off"] == words

    registry = er.async_get(hass)
    next_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{SITE_ENTRY_ID}_next_peak_warning"
    )
    assert next_id is not None
    registered = registry.async_get(next_id)
    assert registered is not None
    assert registered.entity_category == "diagnostic"
    await hass.config_entries.async_unload(entry.entry_id)


_ULID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")


async def test_the_last_decision_is_a_code_never_a_trail_line(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """ENT-21: an `enum` of decisions; the trail, which names loads by id, is data."""
    await _enable_everything(hass, site)
    state = _state(hass, "sensor", "reasons")
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == list(DECISION_STATES)
    assert state.state in DECISION_STATES
    assert not _ULID.search(state.state)
    assert isinstance(state.attributes["trail"], list)
    assert state.attributes["friendly_name"] == "Test site Last decision"
    snapshot = site.runtime_data.snapshot
    assert snapshot is not None
    assert decision_state(snapshot) == state.state == "normal"


# --------------------------------------------------------------------------- #
# The home device (BR-3)
# --------------------------------------------------------------------------- #


def _site_device(hass: HomeAssistant) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SITE_ENTRY_ID), SITE_ENTRY_ID
    )
    assert device is not None
    return device


async def test_20_the_home_device_names_its_maker_model_and_version(
    hass: HomeAssistant, meter: FakeMeter
) -> None:
    """Manufacturer "PowerPlan", the path in words in the system language, the version."""
    hass.config.language = "nb"
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test site", entry_id=SITE_ENTRY_ID, data=site_data(hass)
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device = _site_device(hass)
    assert device.manufacturer == SITE_MANUFACTURER == "PowerPlan"
    assert device.model == "Pris og effekt", "assembled from the translations (H4)"
    assert device.model_id == "full"
    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
    assert device.sw_version is not None
    assert device.sw_version.split(" ", 1)[0] == manifest["version"]
    commit = git_commit(INTEGRATION)
    if commit is not None:
        assert device.sw_version == f"{manifest['version']} ({commit})"
    await hass.config_entries.async_unload(entry.entry_id)


def test_the_commit_is_read_from_a_checkout_and_a_worktree(tmp_path: Path) -> None:
    """F-9: the version names the commit where a `.git` is found; nothing where none is."""
    sha = "0123456789abcdef0123456789abcdef01234567"
    other = "89abcdef0123456789abcdef0123456789abcdef"

    checkout = tmp_path / "checkout"
    component = checkout / "custom_components" / "powerplan"
    component.mkdir(parents=True)
    git = checkout / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text(f"{sha}\n")
    assert git_commit(component) == sha[:7]

    # A packed ref, no loose one.
    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text(f"# pack-refs\n{other} refs/heads/main\n")
    assert git_commit(component) == other[:7]

    # A detached HEAD.
    (git / "HEAD").write_text(f"{sha}\n")
    assert git_commit(component) == sha[:7]

    # A worktree: `.git` is a file naming its own gitdir, whose refs live in the common dir.
    tree = tmp_path / "tree"
    tree_component = tree / "custom_components" / "powerplan"
    tree_component.mkdir(parents=True)
    gitdir = git / "worktrees" / "tree"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/wp\n")
    (gitdir / "commondir").write_text("../..\n")
    (git / "refs" / "heads" / "wp").write_text(f"{other}\n")
    (tree / ".git").write_text(f"gitdir: {gitdir}\n")
    assert git_commit(tree_component) == other[:7]

    # Installed from a release, or bind-mounted alone: nothing to read.
    bare = tmp_path / "config" / "custom_components" / "powerplan"
    bare.mkdir(parents=True)
    assert git_commit(bare) is None
