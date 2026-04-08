"""D2 §9 1, 10 - the shipped NO presets against their golden files, and versions.

The golden files (`tests/golden/presets/<id>.json`, D9 §3) carry the source of
every number: the step tables come from the DSOs' own price pages or from D2 §6,
the window series is the row of the table effektstyring verified against
twelve months of recorder history, and each expected metric is the arithmetic
written out. A golden mismatch is either a preset change - which arrives with a
new source in the same PR - or a bug in the evaluator.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    AUTO,
    ContractedPower,
    Evaluator,
    Linear,
    PeakTariff,
    PeriodLimit,
    Step,
    StepTable,
    Target,
    TariffSpec,
    TariffVersion,
    Tiers,
    TimeFilter,
    WeightRule,
)
from custom_components.powerplan.core.tariffs.presets import loader
from tests.core.tariffs.conftest import (
    NO_HOLIDAYS,
    OSLO,
    Holidays,
    closed,
    golden,
    local,
    no_tariff,
    record_days,
    spec,
)

PRESETS = {
    "no.generic-top3": "no/generic-top3",
    "no.tensio.household": "no/tensio",
    "no.elvia.household": "no/elvia",
}
SHIPPED = (*PRESETS.values(), "custom")


def _ev(preset: str, **kwargs: Any) -> Evaluator:
    return Evaluator(loader.load(preset), tz=OSLO, calendar=NO_HOLIDAYS, **kwargs)


def _record(ev: Evaluator, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        ev.record_window(closed(datetime.fromisoformat(row["local"]), row["kwh"]))


# --------------------------------------------------------------------------- #
# 1 - golden per preset
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("preset_id", sorted(PRESETS))
def test_1_golden_month_metric_level_and_fee(preset_id: str) -> None:
    """The golden month: metric, level, fee and the distinct-days rule."""
    data = golden(preset_id)
    ev = _ev(data["preset"])
    _record(ev, data["windows"])

    want = data["expected"]
    level = ev.level()
    assert ev.metric() == pytest.approx(want["metric_kw"], abs=1e-6)
    assert ev.metric() != pytest.approx(want["not_metric_kw"], abs=1e-3)
    assert level.index == want["level_index"]
    assert level.name == want["level_name"]
    assert level.fee == Money(Decimal(want["fee"]), want["currency"])
    assert level.confidence == want["confidence"]
    assert level.metric_kw == pytest.approx(want["metric_kw"], abs=1e-6)

    _record(ev, data["then_add"])
    after = data["expected_after"]
    level = ev.level()
    assert ev.metric() == pytest.approx(after["metric_kw"], abs=1e-6)
    assert level.index == after["level_index"]
    assert level.name == after["level_name"]
    assert level.fee == Money(Decimal(after["fee"]), after["currency"])

    top = next(item for item in ev.advice() if item.key == "top_entries")
    assert [[day, pytest.approx(kw)] for day, kw in top.params["entries"]] == [
        [day, pytest.approx(kw)] for day, kw in after["top_entries"]
    ]


@pytest.mark.parametrize("preset_id", sorted(PRESETS))
def test_1_golden_bill_matches_the_level(preset_id: str) -> None:
    """`bill()` prices the golden month at the same fee the level names (D2 §9 18's half that is not WP0.10)."""
    data = golden(preset_id)
    ev = _ev(data["preset"])
    _record(ev, data["windows"] + data["then_add"])
    after = data["expected_after"]

    bill = ev.bill(ev.period(local("2026-09-15T12:00:00")))
    assert bill.capacity_fee == Money(Decimal(after["fee"]), after["currency"])
    assert bill.metric_kw == pytest.approx(after["metric_kw"], abs=1e-6)
    assert bill.level.name == after["level_name"]
    assert bill.windows_priced == len(data["windows"]) + len(data["then_add"])
    assert bill.period.key == data["month"]


@pytest.mark.parametrize("preset_id", sorted(PRESETS))
def test_1_golden_ceilings(preset_id: str) -> None:
    """The ceiling at the golden's checkpoints, with the free ride where it applies."""
    data = golden(preset_id)
    rows = data["windows"] + data["then_add"]
    for check in data["checkpoints"]:
        ev = _ev(data["preset"])
        _record(ev, rows[: check["after_windows"]])
        now = local(check["at_local"])

        ceiling = ev.ceiling_kwh(now, AUTO, check["risk"], check["eps_kwh"])
        assert ceiling.kwh == pytest.approx(check["ceiling_kwh"], abs=1e-6), check["_"]
        assert ceiling.reason == check["reason"], check["_"]
        assert ceiling.free_ride is check["free_ride"], check["_"]
        assert ceiling.slack_kwh == pytest.approx(check["slack_kwh"], abs=1e-6), check["_"]
        assert ceiling.eligible is True
        assert ceiling.weight == 1.0
        assert ev.target_w_at(now, AUTO) == pytest.approx(check["target_kw"] * 1000.0)


@pytest.mark.inv("INV-52")
def test_1b_tensio_second_version_bills_the_same_month_on_the_2027_table() -> None:
    """The same five-day month recorded in January 2027 is billed on the 2027 version."""
    data = golden("no.tensio.household")
    second = data["second_version"]
    ev = _ev(data["preset"])
    for row in data["windows"] + data["then_add"]:
        moved = row["local"].replace("2026-09-0", "2027-01-0")
        ev.record_window(closed(datetime.fromisoformat(moved), row["kwh"]))

    level = ev.level()
    assert ev.metric() == pytest.approx(second["metric_kw"], abs=1e-6)
    assert level.name == second["level_name"]
    assert level.fee == Money(Decimal(second["fee"]), "NOK")
    assert ev.active_version().valid_from == date.fromisoformat(second["valid_from"])


def test_1c_one_hour_at_18_87_kwh_buys_the_month() -> None:
    """February 2026: one hour at 18.87 kWh moved the month a whole step.

    effektstyring README §1: "In February 2026 one hour at 18.87 kWh lifted the
    top-3 mean from ~9.2 to 12.44 and moved the month from step 3 to step 4."
    The two surviving days are reconstructed to reproduce that published pair of
    means exactly (9.30 + 9.15 + 9.15) / 3 = 9.200 and
    (18.87 + 9.30 + 9.15) / 3 = 12.440; the load-bearing facts are the single
    hour and the step it bought (PLAN §6 R5).
    """
    ev = _ev("no/tensio")
    record_days(ev, {"2026-02-03": 9.30, "2026-02-07": 9.15, "2026-02-11": 9.15})

    before = ev.level()
    assert ev.metric() == pytest.approx(9.200, abs=1e-6)
    assert before.name == "5–10 kW"
    assert before.fee == Money(Decimal(416), "NOK")

    ev.record_window(closed(datetime(2026, 2, 14, 18), 18.87))
    after = ev.level()
    assert ev.metric() == pytest.approx(12.440, abs=1e-6)
    assert after.name == "10–15 kW"
    assert after.fee == Money(Decimal(613), "NOK")
    assert after.fee.amount - before.fee.amount == Decimal(197)


# --------------------------------------------------------------------------- #
# 10 - versions (INV-52)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-52")
def test_10a_version_switch_prices_december_and_january_apart() -> None:
    """December is billed on the 2026 table, January on the 2027 one.

    The benchmark year straddles 1 January (PLAN §7 dec. 14), so this is the
    switch every nightly run crosses.
    """
    ev = _ev("no/tensio")
    record_days(ev, {"2026-12-04": 9.15, "2026-12-09": 8.00, "2026-12-15": 7.00})
    december = ev.bill(ev.period(local("2026-12-20T12:00:00")))

    record_days(ev, {"2027-01-05": 9.15, "2027-01-09": 8.00, "2027-01-15": 7.00})
    january = ev.bill(ev.period(local("2027-01-20T12:00:00")))

    assert december.metric_kw == pytest.approx(january.metric_kw, abs=1e-9)
    assert december.capacity_fee == Money(Decimal(416), "NOK")
    assert january.capacity_fee == Money(Decimal(441), "NOK")
    assert december.version_id.endswith("2026-01-01")
    assert january.version_id.endswith("2027-01-01")


@pytest.mark.inv("INV-52")
def test_10b_period_spanning_two_versions_prices_each_windows_share() -> None:
    """A version that starts mid-month: each half of the period pays its own table.

    Tensio's own versions start on 1 January, so a *monthly* period never spans
    two of them. A DSO that changes prices mid-month does exist (Tensio itself
    moved on 1 July 2026), and D2 §5.10 says each window's contribution is priced
    under the version valid at its start: the fee is therefore the duration-
    weighted blend, while the level is classified on the current version.
    """

    def table(cheap: int, dear: int) -> StepTable:
        return StepTable(
            steps=(
                Step(10.0, Money(Decimal(cheap), "NOK"), "0–10 kW"),
                Step(None, Money(Decimal(dear), "NOK"), "over 10 kW"),
            )
        )

    two_versions = TariffSpec(
        id="test.midmonth",
        name="mid-month change",
        currency="NOK",
        country=None,
        operator=None,
        source_url=None,
        verified=None,
        assumed="a test fixture",
        versions=(
            TariffVersion(
                valid_from=date(2026, 9, 1),
                version_id="test.midmonth@2026-09-01",
                grammar=(no_tariff(pricing=table(400, 800)),),
            ),
            TariffVersion(
                valid_from=date(2026, 9, 16),
                version_id="test.midmonth@2026-09-16",
                grammar=(no_tariff(pricing=table(500, 900)),),
            ),
        ),
    )
    ev = Evaluator(two_versions, tz=OSLO, calendar=Holidays())
    record_days(ev, {"2026-09-04": 6.0, "2026-09-10": 6.0, "2026-09-20": 6.0})

    bill = ev.bill(ev.period(local("2026-09-20T12:00:00")))
    # September has 30 local days; the first version covers 1-15 (15 days), the
    # second 16-30 (15 days): 400 x 15/30 + 500 x 15/30 = 450 NOK.
    assert bill.capacity_fee.amount == pytest.approx(Decimal(450))
    assert bill.version_id.count("+") == 1
    assert bill.level.name == "0–10 kW"
    assert ev.level().fee == Money(Decimal(500), "NOK")


# --------------------------------------------------------------------------- #
# the loader is a boundary (D2 §2, §6)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", SHIPPED)
def test_every_shipped_preset_validates_and_loads(name: str) -> None:
    """Each file this WP ships passes its own schema and parses into the grammar."""
    spec = loader.load(name)
    assert spec.versions
    assert list(spec.versions) == sorted(spec.versions, key=lambda v: v.valid_from)
    for version in spec.versions:
        assert version.verified is not None or version.assumed, version.version_id
        assert version.grammar


def test_custom_preset_is_a_no_peak_site() -> None:
    """`custom` starts from "no capacity component" and lets the flow describe one."""
    ev = Evaluator(loader.load("custom"), tz=OSLO, calendar=Holidays())
    level = ev.level()
    assert level.kind == "none"
    assert level.fee is None
    ceiling = ev.ceiling_kwh(local("2026-09-05T20:00:00"), AUTO, 0.5, 0.3)
    assert ceiling.kwh == float("inf")
    assert ceiling.eligible is False


def test_elvia_carries_the_energy_components_for_d1() -> None:
    """A preset may carry a `tou_schedule` for D1 to pre-fill; D2 never uses it (D2 §6)."""
    version = loader.load("no/elvia").versions[-1]
    periods = version.energy_components["tou_schedule"]["periods"]
    assert [period["name"] for period in periods] == ["dag", "natt"]
    assert periods[0]["price"] == Decimal("0.4640")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"versions": []}, "minItems"),
        ({"id": "NO Tensio!"}, "pattern"),
        ({"nettleie": 1}, "additionalProperties"),
        ({"currency": "kroner"}, "pattern"),
    ],
)
def test_schema_rejects_a_malformed_preset(mutation: dict[str, Any], message: str) -> None:
    """The schema is enforced, not decorative."""
    raw = dict(_MINIMAL)
    raw.update(mutation)
    with pytest.raises(loader.PresetError, match=message):
        loader.validate(raw, source="test")


def test_unsorted_versions_are_rejected() -> None:
    """D2 §2: overlapping or unsorted versions fail validation."""
    raw = dict(_MINIMAL)
    raw["versions"] = [_MINIMAL["versions"][0], dict(_MINIMAL["versions"][0])]
    with pytest.raises(loader.PresetError, match="ascending"):
        loader.validate(raw, source="test")


def test_a_version_without_a_source_must_say_it_is_assumed() -> None:
    """Never silent: `verified: null` obliges an `assumed` sentence."""
    raw = dict(_MINIMAL)
    version = dict(_MINIMAL["versions"][0])
    version.pop("assumed")
    raw["versions"] = [version]
    with pytest.raises(loader.PresetError, match="assumed"):
        loader.validate(raw, source="test")


def test_a_step_table_needs_one_open_ended_last_step() -> None:
    """An open-ended step anywhere but last, or none at all, is a broken table."""
    raw = dict(_MINIMAL)
    version = dict(_MINIMAL["versions"][0])
    peak = dict(version["peak"])
    peak["pricing"] = {"steps": [[None, 100, "any"], [10, 200, "0–10 kW"]]}
    version["peak"] = peak
    raw["versions"] = [version]
    with pytest.raises(loader.PresetError, match="open-ended"):
        loader.validate(raw, source="test")


def test_changing_the_period_needs_a_history_policy() -> None:
    """D2 §5.10: month -> rolling without `history_policy` is refused."""
    raw = dict(_MINIMAL)
    second = {
        "valid_from": "2027-01-01",
        "verified": None,
        "assumed": "a test fixture",
        "peak": dict(_MINIMAL["versions"][0]["peak"], period="rolling_months", rolling_months=12),
    }
    raw["versions"] = [_MINIMAL["versions"][0], second]
    with pytest.raises(loader.PresetError, match="history_policy"):
        loader.validate(raw, source="test")


def test_render_plain_language_describes_the_norwegian_rule() -> None:
    """The flow shows this before it saves (D2 §6, INV-67)."""
    text = loader.render_plain_language(loader.load("no/tensio"), at=date(2026, 9, 19))
    assert "three highest hours" in text
    assert "three different days" in text
    assert "up to 2 kW 137 NOK" in text
    assert "over 20 kW 1200 NOK" in text
    assert "Tensio" in text


def test_render_plain_language_describes_a_no_peak_site() -> None:
    """A `NoPeak` preset says so in one sentence rather than rendering an empty table."""
    text = loader.render_plain_language(loader.load("custom"))
    assert "no capacity" in text


_MINIMAL: dict[str, Any] = {
    "id": "test.minimal",
    "name": "minimal",
    "currency": "NOK",
    "versions": [
        {
            "valid_from": "2026-01-01",
            "verified": None,
            "assumed": "a test fixture",
            "peak": {
                "window_min": 60,
                "per_day": "max",
                "per_period": "mean_top_n",
                "n": 3,
                "period": "month",
                "pricing": {"steps": [[10, 400, "0–10 kW"], [None, 800, "over 10 kW"]]},
            },
        }
    ],
}


def test_target_below_the_reached_step_is_still_honoured() -> None:
    """A user may aim below what the month reached; D2 §6 warns, D2 §5 obeys."""
    ev = _ev("no/tensio")
    record_days(ev, {"2026-09-04": 9.15, "2026-09-09": 8.0, "2026-09-15": 7.0})
    ceiling = ev.ceiling_kwh(local("2026-09-20T12:00:00"), Target("step", step_index=1), 0.0, 0.3)
    assert ceiling.kwh == pytest.approx(4.7)


def test_render_plain_language_describes_the_other_shapes() -> None:
    """Every grammar root renders, not only the Norwegian one (D2 §6, INV-67)."""
    finnish = loader.render_plain_language(
        spec(
            PeakTariff(
                window_min=60,
                eligible=None,
                weights=(),
                per_day="all",
                per_period="max",
                period="month",
                pricing=Linear(price_per_kw=Money(Decimal("2.50"), "EUR"), free_kw=8.0),
            ),
            currency="EUR",
        )
    )
    assert "single highest hour" in finnish
    assert "2.50 EUR per kW per month" in finnish
    assert "first 8 kW free" in finnish

    tiered = loader.render_plain_language(
        spec(
            PeakTariff(
                window_min=30,
                eligible=TimeFilter(weekdays=(0, 1, 2, 3, 4), hours=((14 * 60, 19 * 60),)),
                weights=(WeightRule(when=TimeFilter(hours=((22 * 60, 6 * 60),)), weight=0.5),),
                per_day="max",
                per_period="max",
                period="year",
                pricing=Tiers(
                    bands=((10.0, Money(Decimal(4), "USD")), (None, Money(Decimal(2), "USD")))
                ),
            ),
            currency="USD",
        )
    )
    assert "highest daily half-hour each year" in tiered
    assert "marginal by band" in tiered
    assert "between 14:00 and 19:00" in tiered
    assert "counts 0.5×" in tiered

    spanish = loader.render_plain_language(
        spec(
            ContractedPower(
                limits=(PeriodLimit(when=None, limit_kw=4.6),), on_exceed="trip", tolerance_pct=0.1
            ),
            currency="EUR",
        )
    )
    assert "no capacity component" in spanish
    assert "limited to 4.6 kW" in spanish
    assert "trips the supply" in spanish


@pytest.mark.parametrize(
    "pricing",
    [
        {"linear": {"price_per_kw": 40.5, "min_kw": 2.5}},
        {"tiers": [[10, 4], [None, 2]]},
    ],
)
def test_validate_accepts_the_other_pricing_shapes(pricing: dict[str, Any]) -> None:
    """The schema takes all three shapes; WP4.3's files change no code (D2 §2)."""
    raw = dict(_MINIMAL)
    version = dict(_MINIMAL["versions"][0])
    version["peak"] = dict(version["peak"], pricing=pricing)
    raw["versions"] = [version]
    loader.validate(raw, source="test")
