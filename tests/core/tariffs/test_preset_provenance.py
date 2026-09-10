"""D2 §9 20, 21, 22 - shipped presets carry verified facts only (PLAN §7 dec. 21).

Every version of every file under `presets/<cc>/` names the operator's or the
regulator's own document, the date it was read, nothing assumed, and starts no
later than it was read. A national rule whose numbers are each household's own is
a **template**: it ships without them and refuses to bill until the flow fills
them from the household's bill. A company with two tariff areas ships two files.
"""

from __future__ import annotations

import copy
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import Evaluator
from custom_components.powerplan.core.tariffs.rules import loader
from tests.builders.presets import fixture_preset, fixture_raw
from tests.core.tariffs.conftest import NO_HOLIDAYS, OSLO, closed, golden, load_any, preset_names

SHIPPED = [name for name in preset_names() if "/" in name]
TEMPLATES = [name for name in SHIPPED if load_any(name).get("template")]
PRESETS = [name for name in SHIPPED if name not in TEMPLATES]


# --------------------------------------------------------------------------- #
# 20 - provenance
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", PRESETS)
def test_20_every_shipped_version_names_its_source_and_the_day_it_was_read(name: str) -> None:
    """A source, a date, nothing assumed, and never a table before it is in force."""
    raw = load_any(name)
    assert not raw.get("assumed"), name
    for version in raw["versions"]:
        verified = version.get("verified", raw.get("verified"))
        assert version.get("source_url", raw.get("source_url")), (name, version["valid_from"])
        assert verified is not None, (name, version["valid_from"])
        assert not version.get("assumed"), (name, version["valid_from"])
        assert date.fromisoformat(version["valid_from"]) <= date.fromisoformat(verified)


def _tensio() -> dict[str, Any]:
    return copy.deepcopy(fixture_raw("no/tensio-ts"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda raw: raw["versions"][-1].update(assumed="a guess"), "no assumed"),
        (lambda raw: raw.update(assumed="a guess"), "no assumed"),
        (
            lambda raw: (raw.pop("source_url"), raw["versions"][-1].pop("source_url")),
            "source_url",
        ),
        (
            lambda raw: (raw.update(verified=None), raw["versions"][-1].update(verified=None)),
            "verified",
        ),
        (lambda raw: raw["versions"][-1].update(valid_from="2026-10-01"), "after verified"),
    ],
)
def test_20_a_shipped_file_breaking_any_of_the_four_fails_to_load(
    change: Any, message: str
) -> None:
    """The loader enforces the rule for every file under `presets/<cc>/` (D2 §2)."""
    raw = _tensio()
    change(raw)
    with pytest.raises(loader.PresetError, match=message):
        loader.validate(raw, source="no/tensio-ts.json", shipped=True)
    # A household's own copy is not held to provenance (D2 §6) - though a number
    # with neither a date nor an `assumed` sentence stays refused everywhere.
    if message in ("no assumed", "source_url"):
        loader.validate(raw, source="entry")


def test_20_only_custom_and_templates_ship_without_prices() -> None:
    """`custom` and the templates are the only files that make no claim about a bill."""
    unpriced = sorted(
        name for name in preset_names() if name == "custom" or load_any(name).get("template")
    )
    assert unpriced == ["custom", "es/2_0td", "nl/connection", "no/template"]
    assert TEMPLATES == ["es/2_0td", "nl/connection", "no/template"]


def test_20_a_non_template_may_not_leave_a_number_to_fill() -> None:
    """A null price is a template's privilege: anywhere else it is a broken file."""
    raw = _tensio()
    raw["versions"][-1]["peak"]["pricing"]["steps"] = None
    with pytest.raises(loader.PresetError, match="only a template"):
        loader.validate(raw, source="test")


# --------------------------------------------------------------------------- #
# 21 - templates
# --------------------------------------------------------------------------- #


def test_21_a_template_refuses_to_evaluate_until_it_is_filled() -> None:
    """The Norwegian rule without steps bills nothing - it is the household's to fill."""
    with pytest.raises(loader.TemplateError):
        loader.load("no/template")
    with pytest.raises(loader.TemplateError):
        loader.from_raw(loader.load_raw("es/2_0td"))


def test_21_a_filled_template_equals_the_same_grammar_written_by_hand() -> None:
    """The household's numbers, no source, `assumed` saying whose they are (D2 §9 21)."""
    rows = [(2.0, 150.0), (5.0, 250.0), (10.0, 420.0), (None, 585.0)]
    filled = loader.fill_template(loader.load_raw("no/template"), steps=rows)
    spec = loader.from_raw(filled)

    by_hand = copy.deepcopy(loader.load_raw("no/template"))
    del by_hand["template"]
    by_hand.pop("source_url")
    by_hand["verified"] = None
    by_hand["assumed"] = "from the household's bill"
    by_hand["versions"][0]["peak"]["pricing"]["steps"] = [
        [2.0, 150.0, "0–2 kW"],
        [5.0, 250.0, "2–5 kW"],
        [10.0, 420.0, "5–10 kW"],
        [None, 585.0, "over 10 kW"],
    ]
    assert filled == by_hand
    assert spec.source_url is None
    assert spec.assumed == "from the household's bill"
    version = spec.versions[0]
    assert version.source_url is None
    assert version.peak is not None
    assert (version.peak.n, version.peak.distinct_days, version.peak.window_min) == (3, True, 60)


def test_21_the_filled_template_bills_the_household_step() -> None:
    """The completed template is an ordinary tariff: the golden month lands in its step."""
    rows = [(2.0, 150.0), (5.0, 250.0), (10.0, 420.0), (None, 585.0)]
    spec = loader.from_raw(loader.fill_template(loader.load_raw("no/template"), steps=rows))
    ev = Evaluator(spec, tz=OSLO, calendar=NO_HOLIDAYS)
    for row in golden("no.tensio-ts.household")["windows"]:
        ev.record_window(closed(datetime.fromisoformat(row["local"]), row["kwh"]))
    assert ev.level().name == "2–5 kW"
    assert ev.level().fee == Money(Decimal("250"), "NOK")


def test_21_a_contracted_template_takes_the_households_kw() -> None:
    """ES's two periods and NL's connection are the household's contract (D2 §6)."""
    spanish = loader.from_raw(loader.fill_template(loader.load_raw("es/2_0td"), limits=[3.45, 6.9]))
    contracted = spanish.versions[0].contracted
    assert contracted is not None
    assert [limit.limit_kw for limit in contracted.limits] == [3.45, 6.9]
    with pytest.raises(ValueError, match="shorter"):
        loader.fill_template(loader.load_raw("es/2_0td"), limits=[3.45])


# --------------------------------------------------------------------------- #
# 22 - tariff areas
# --------------------------------------------------------------------------- #


def test_22_tensio_ts_and_tn_classify_one_month_alike_and_bill_their_own_sheet() -> None:
    """Two areas, one tariff model: the same month lands in the same step at two prices."""
    rows = golden("no.tensio-ts.household")["windows"]
    fees = {}
    for name in ("no/tensio-ts", "no/tensio-tn"):
        ev = Evaluator(fixture_preset(name), tz=OSLO, calendar=NO_HOLIDAYS)
        for row in rows:
            ev.record_window(closed(datetime.fromisoformat(row["local"]), row["kwh"]))
        assert ev.level().name == "2–5 kW", name
        fees[name] = ev.level().fee
    # tensio.no, 2026-07-01: TS (south) 2–5 kW 233, TN (north) 2–5 kW 284 NOK/month.
    assert fees == {
        "no/tensio-ts": Money(Decimal("233"), "NOK"),
        "no/tensio-tn": Money(Decimal("284"), "NOK"),
    }


@pytest.mark.parametrize(
    ("retired", "successor"),
    [
        ("no/generic-top3", "custom"),
        ("fi/energiavirasto-2026", "custom"),
        ("be/fluvius", "custom"),
        ("dk/nopeak", "custom"),
    ],
)
def test_a_retired_file_names_what_its_sites_run_on(retired: str, successor: str) -> None:
    """A site set up on a removed file keeps running and is told to reconfigure (D2 §8)."""
    assert loader.successor(retired) == successor
    assert loader.load(successor).versions
    with pytest.raises(loader.PresetError):
        loader.load_raw(retired)


@pytest.mark.parametrize("name", ["no/tensio", "no/tensio-ts", "be/fluvius-imewo", "se/ellevio"])
def test_a_companys_file_left_the_integration(name: str) -> None:
    """INV-70, TS.6: no company's prices ship; the migration knows where each is fetched."""
    from custom_components.powerplan.storage import (  # noqa: PLC0415
        FETCHED_FILES,
        NO_CAPACITY_FILES,
    )

    with pytest.raises(loader.PresetError):
        loader.load_raw(name)
    assert name in FETCHED_FILES or name in NO_CAPACITY_FILES
