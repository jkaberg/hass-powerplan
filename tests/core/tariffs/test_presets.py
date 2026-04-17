"""D2 §9 1, 3, 4, 6, 8, 9, 10, 12 - every shipped preset against its golden file.

The golden files (`tests/golden/presets/<id>.json`, D9 §3) carry the source of
every number: the step tables come from the DSOs' own price pages or from D2 §6,
the NO window series is the row of the table effektstyring verified
against twelve months of recorder history, and each expected metric is the
arithmetic written out. A golden mismatch is either a preset change - which
arrives with a new source in the same PR - or a bug in the evaluator.

Two layers. The NO presets have their own tests, because their goldens also carry
ceiling checkpoints and a second version. Everything after them is generic and
runs over whatever is in `presets/`: WP4.3a's ten markets land there, and adding
an eleventh is a file plus a golden and no code at all (D2 §2). A preset with a
country and no golden fails, which is what makes "a golden per preset" a rule
rather than a habit.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.metering import ElectricalProfile, VoltageSystem
from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    AUTO,
    ContractedPower,
    Evaluator,
    HolidayMode,
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
    seed_from_bills,
)
from custom_components.powerplan.core.tariffs.presets import loader
from tests.core.tariffs.conftest import (
    NO_HOLIDAYS,
    OSLO,
    Holidays,
    closed,
    golden,
    local,
    market_golden,
    no_tariff,
    preset_names,
    preset_raw,
    record_days,
    spec,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import tzinfo

PRESETS = {
    "no.generic-top3": "no/generic-top3",
    "no.tensio.household": "no/tensio",
    "no.elvia.household": "no/elvia",
}
SHIPPED = preset_names()

#: The one shipped preset that makes no claim about anyone's bill, and so has no
#: golden: `custom` starts a site with no capacity component and lets the flow
#: describe one (D2 §6). Every other file must carry a golden - that is D2 §9 1.
NO_GOLDEN = ("custom",)

#: Every preset that claims something about a bill, by load name, discovered from
#: disk so a new market is a file plus a golden and nothing else (D2 §2).
MARKETS = tuple(name for name in SHIPPED if name not in NO_GOLDEN)


#: A single-phase 230 V supply with a 25 A main breaker. `limit_now_w` takes a
#: profile because a market that contracts in amps needs it (D2 §5.8); none of
#: the shipped presets does, so any profile answers the question.
ANY_PROFILE = ElectricalProfile(system=VoltageSystem.SINGLE_230, phases=1, main_fuse_a=25.0)


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


# --------------------------------------------------------------------------- #
# 1 - a golden per preset, for every market on disk (D2 §9 1)
# --------------------------------------------------------------------------- #
#
# The NO presets above are driven by their own tests because their golden also
# carries ceiling checkpoints and a second version. Everything below is the
# generic half, and it runs over whatever is in `presets/`: a market is a JSON
# file plus a golden, and adding one changes no code (D2 §2). WP4.3a's ten files
# land here, each with the source of every number in its golden's `sources`.


def _zone(data: Mapping[str, Any]) -> tzinfo:
    """Return the market's zone from the golden; the preset file must agree with it."""
    return ZoneInfo(data["tz"])


def _market_evaluator(data: Mapping[str, Any]) -> Evaluator:
    """Build the evaluator a market golden describes, seeded and recorded."""
    zone = _zone(data)
    calendar = Holidays(frozenset(date.fromisoformat(day) for day in data.get("holidays", ())))
    ev = Evaluator(loader.load(data["preset"]), tz=zone, calendar=calendar)
    bills = [(key, float(kw)) for key, kw in data.get("seed_bills", ())]
    if bills:
        seed_from_bills(ev, bills)
    for row in data.get("windows", ()):
        ev.record_window(
            closed(
                datetime.fromisoformat(row["local"]),
                row["kwh"],
                window_min=data["window_min"],
                tz=zone,
            )
        )
    return ev


def _expected_kind(want: Mapping[str, Any]) -> str:
    """Return "step" when the golden names an index, "none" when it prices nothing, else "kw"."""
    if want.get("fee") is None:
        return "none"
    return "step" if want.get("level_index") is not None else "kw"


@pytest.mark.parametrize("name", MARKETS)
def test_1_every_market_golden_reproduces_its_metric_level_and_fee(name: str) -> None:
    """D2 §9 1: the golden's synthetic period, hand-computed, on the shipped file."""
    data = market_golden(name)
    assert data is not None, f"{name} has a country but no golden (D2 §9 1)"
    assert data["preset"] == name
    want = data["expected"]
    ev = _market_evaluator(data)

    level = ev.level()
    assert level.kind == _expected_kind(want)
    assert ev.metric() == pytest.approx(want["metric_kw"], abs=1e-6)
    if want.get("fee") is None:
        # A market with no capacity component has no metric to report, and the
        # level says so rather than reporting a zero anyone could mistake for one.
        assert level.metric_kw is None
    else:
        assert level.metric_kw == pytest.approx(want["metric_kw"], abs=1e-6)
    if "not_metric_kw" in want:
        assert ev.metric() != pytest.approx(want["not_metric_kw"], abs=1e-3)
    if "level_name" in want:
        assert level.name == want["level_name"]
    if want.get("level_index") is not None:
        assert level.index == want["level_index"]
    if want.get("fee") is None:
        assert level.fee is None
    else:
        assert level.fee is not None
        assert float(level.fee.amount) == pytest.approx(float(str(want["fee"])), abs=1e-4)
        assert level.fee.currency == want["currency"]
    assert level.confidence == want["confidence"]
    assert level.missing_months == want.get("missing_months", 0)


@pytest.mark.parametrize("name", MARKETS)
def test_1_every_market_golden_bills_what_its_level_names(name: str) -> None:
    """`bill()` prices the golden's period at the fee the level names (D2 §5.3)."""
    data = market_golden(name)
    assert data is not None
    want = data["expected"]
    ev = _market_evaluator(data)

    bill = ev.bill(ev.period(local(data["at_local"], tz=_zone(data))))
    assert bill.period.key == data["period_key"]
    assert bill.metric_kw == pytest.approx(want["metric_kw"], abs=1e-6)
    if "windows_priced" in want:
        assert bill.windows_priced == want["windows_priced"]
    if want.get("bill_fee") is None:
        assert float(bill.capacity_fee.amount) == pytest.approx(0.0)
    else:
        assert float(bill.capacity_fee.amount) == pytest.approx(
            float(str(want["bill_fee"])), abs=1e-4
        )
        assert bill.capacity_fee.currency == want["currency"]
    assert bill.version_id.startswith(data["id"])


@pytest.mark.parametrize("name", MARKETS)
def test_every_market_renders_a_description_the_household_can_recognise(name: str) -> None:
    """D2 §6, INV-67: the metric and the first two bands, or the contracted limits.

    The flow shows this sentence before it saves. A preset whose own description
    does not name what it bills is a preset nobody can check against a bill.
    """
    data = market_golden(name)
    assert data is not None
    spec_ = loader.load(name)
    # The version the golden bills, not the newest: a preset whose 2027 prices are
    # already in the file would otherwise be described on numbers no golden checks.
    at = date.fromisoformat(f"{data['period_key']}-15")
    text = loader.render_plain_language(spec_, at=at)

    for fragment in data["describes"]:
        assert fragment in text, f"{name}: {fragment!r} missing from {text!r}"

    version = spec_.version_at(at)
    peak = version.peak
    if peak is not None:
        if isinstance(peak.pricing, StepTable):
            for step in peak.pricing.steps[:2]:
                assert step.name.split()[0] in text or f"{step.upper_kw:g} kW" in text
        elif isinstance(peak.pricing, Tiers):
            for _upto, price in peak.pricing.bands[:2]:
                assert f"{price.amount:f}" in text
        else:
            assert f"{peak.pricing.price_per_kw.amount:f}" in text
    if version.contracted is not None:
        for limit in version.contracted.limits:
            assert f"{limit.limit_kw:g} kW" in text


@pytest.mark.parametrize("name", MARKETS)
def test_every_market_preset_declares_its_zone_and_the_golden_uses_it(name: str) -> None:
    """D-0111: the market's clock is data in the file, never a literal in Python."""
    raw = preset_raw(name)
    data = market_golden(name)
    assert data is not None
    assert raw.get("country"), f"{name} needs a country"
    assert raw.get("tz"), f"{name} needs the market's IANA zone (D-0111)"
    assert raw["tz"] == data["tz"]
    assert ZoneInfo(raw["tz"])


# --------------------------------------------------------------------------- #
# 3, 4, 6, 8, 9, 12 - on the shipped preset files (D2 §9)
# --------------------------------------------------------------------------- #
#
# WP0.3 built each of these markets' grammar inline, because its preset file did
# not exist yet. The inline tests stay: they are the arithmetic. These are the
# same rules read off the files a household will actually pick in the flow, which
# is where a transcription error between the price page and the JSON shows up.
#
# D2 §9 7 (the ratchet) has no preset here on purpose: HLD §8 puts `ratchet` on
# US *commercial* tariffs and both shipped US presets are residential, so no file
# among the ten carries one. It stays covered inline in `test_metric.py`.


def test_3_ellevio_preset_weighs_the_night_before_the_daily_maximum() -> None:
    """D2 §9 3, on the file: a 10 kW night hour is a 5 kW entry; 6 kW by day beats it."""
    data = market_golden("se/ellevio")
    assert data is not None
    ev = _market_evaluator(data)
    zone = _zone(data)

    for day, kw in data["daily_maxima"].items():
        assert ev.history.days[date.fromisoformat(day)].max_weighted_kw == pytest.approx(kw), day
    for check in data["weights_at"]:
        assert ev.weight_now(local(check["local"], tz=zone)) == check["weight"], check["why"]


def test_3b_ellevio_preset_lifts_the_raw_ceiling_of_a_half_counted_hour() -> None:
    """A weight below one lets a night hour draw twice the target and weigh the same."""
    data = market_golden("se/ellevio")
    assert data is not None
    ev = _market_evaluator(data)
    zone = _zone(data)

    night = ev.ceiling_kwh(local("2026-03-25T23:30:00", tz=zone), AUTO, 0.0, 0.3)
    day = ev.ceiling_kwh(local("2026-03-25T18:30:00", tz=zone), AUTO, 0.0, 0.3)
    assert day.weight == 1.0
    assert night.weight == 0.5
    assert night.kwh == pytest.approx((day.kwh + 0.3) * 2 - 0.3)


@pytest.mark.inv("INV-52")
def test_3c_ellevio_drops_the_effect_charge_on_the_day_the_mandate_was_repealed() -> None:
    """Sweden repealed the effect mandate in June 2026; the file carries both versions.

    HLD §8's SE row ends "mandate repealed Jun 2026". A preset that only held the
    effect tariff would keep billing a household for a component its DSO stopped
    charging on 2026-06-01 - which is what versioning is for (D2 §5.10, INV-52).
    """
    spec_ = loader.load("se/ellevio")
    assert [version.valid_from for version in spec_.versions] == [
        date(2025, 1, 1),
        date(2026, 6, 1),
    ]
    assert spec_.version_at(date(2026, 5, 31)).peak is not None
    after = spec_.version_at(date(2026, 6, 1))
    assert after.peak is None
    assert after.contracted is None

    zone = ZoneInfo(preset_raw("se/ellevio")["tz"])
    ev = Evaluator(spec_, tz=zone, calendar=Holidays())
    ev.record_window(closed(datetime(2026, 7, 8, 18), 9.0, tz=zone))
    assert ev.level().kind == "none"
    assert ev.ceiling_kwh(local("2026-07-08T20:00:00", tz=zone), AUTO, 0.5, 0.3).kwh == float("inf")


def test_8_finnish_preset_deducts_the_first_eight_kilowatts() -> None:
    """D2 §9 8, on the file: 7 kW costs nothing, 9 kW costs one kW's worth."""
    data = market_golden("fi/energiavirasto-2026")
    assert data is not None
    zone = _zone(data)
    for case in data["deductible"]:
        ev = Evaluator(loader.load(data["preset"]), tz=zone, calendar=Holidays())
        ev.record_window(closed(datetime(2026, 3, 10, 18), case["kwh"], tz=zone))

        level = ev.level()
        assert ev.metric() == pytest.approx(case["kwh"]), case["why"]
        assert level.fee is not None
        assert float(level.fee.amount) == pytest.approx(case["fee"], abs=1e-6), case["why"]
        assert level.fee.currency == data["currency"]


def test_8b_the_finnish_deductible_is_the_versions_own_start_date() -> None:
    """Energiavirasto's määräys came into force on 2026-02-02, and the file says so."""
    spec_ = loader.load("fi/energiavirasto-2026")
    assert [version.valid_from for version in spec_.versions] == [date(2026, 2, 2)]
    peak = spec_.versions[0].peak
    assert peak is not None
    assert isinstance(peak.pricing, Linear)
    assert peak.pricing.free_kw == 8.0
    assert peak.window_min == 60
    assert peak.eligible is None


def _fluvius(seed: list[list[Any]]) -> tuple[Evaluator, Any]:
    """Build the Fluvius evaluator with `seed` months already on the bill."""
    data = market_golden("be/fluvius")
    assert data is not None
    zone = _zone(data)
    ev = Evaluator(loader.load(data["preset"]), tz=zone, calendar=Holidays())
    seed_from_bills(ev, [(key, float(kw)) for key, kw in seed])
    return ev, data


def test_9_fluvius_preset_floors_every_month_before_the_rolling_mean() -> None:
    """D2 §9 9, on the file: 1.0 and 2.6 kW bill on 2.55, never on max(1.8, 2.5).

    The wrong order is 0.05 kW cheaper every month for twelve months, and it is
    the same arithmetic - which is exactly why it needs a golden and not a review.
    """
    data = market_golden("be/fluvius")
    assert data is not None
    want = data["minimum"]
    ev, _ = _fluvius(want["seed_bills"])
    zone = _zone(data)
    ev.record_window(
        closed(
            datetime.fromisoformat(want["window"]["local"]),
            want["window"]["kwh"],
            window_min=data["window_min"],
            tz=zone,
        )
    )

    level = ev.level()
    assert ev.metric() == pytest.approx(want["metric_kw"], abs=1e-6)
    assert level.confidence == want["confidence"]
    assert level.missing_months == want["missing_months"]
    assert level.fee is not None
    assert float(level.fee.amount) == pytest.approx(want["fee"], abs=1e-4)
    assert float(level.fee.amount) != pytest.approx(want["not_fee"], abs=1e-3)


def test_6_fluvius_preset_rolls_the_oldest_month_out_of_the_average() -> None:
    """D2 §9 6, on the file: a thirteenth month drops the first one."""
    data = market_golden("be/fluvius")
    assert data is not None
    want = data["rolling"]
    ev, _ = _fluvius(want["seed_bills"])
    zone = _zone(data)

    for stage in ("twelve_months", "thirteenth_month"):
        step = want[stage]
        ev.record_window(
            closed(
                datetime.fromisoformat(step["window"]["local"]),
                step["window"]["kwh"],
                window_min=data["window_min"],
                tz=zone,
            )
        )
        assert ev.metric() == pytest.approx(step["metric_kw"], abs=1e-6), step["why"]
        if "confidence" in step:
            assert ev.level().confidence == step["confidence"]
            assert ev.level().missing_months == step["missing_months"]


def test_6b_fluvius_preset_is_a_quarter_hour_tariff_on_a_rolling_twelve() -> None:
    """The shape the file must carry for BE at all (HLD §8, D2 §4)."""
    peak = loader.load("be/fluvius").versions[-1].peak
    assert peak is not None
    assert peak.window_min == 15
    assert peak.period == "rolling_months"
    assert peak.rolling_months == 12
    assert peak.price_period_unit == "year"
    assert isinstance(peak.pricing, Linear)
    assert peak.pricing.min_kw == 2.5


@pytest.mark.parametrize("name", ["dk/nopeak", "uk/nopeak"])
def test_a_no_peak_market_never_puts_a_capacity_ceiling_on_the_house(name: str) -> None:
    """HLD §3: where nothing bills a peak, the tariff has no opinion in any season.

    Both files are a claim, not a placeholder: DK prices the grid by time of use
    and GB puts the DNO's costs in the unit rate, so in neither market is there a
    measured peak to defend. If that ever changes it changes as a new version.
    """
    data = market_golden(name)
    assert data is not None
    ev = _market_evaluator(data)
    zone = _zone(data)

    assert ev.level().kind == "none"
    assert ev.level().fee is None
    assert ev.limit_now_w(local(data["at_local"], tz=zone), ANY_PROFILE) is None
    for when in data["ceiling_is_infinite_at"]:
        ceiling = ev.ceiling_kwh(local(when, tz=zone), AUTO, 0.5, 0.3)
        assert ceiling.kwh == float("inf"), when
        assert ceiling.eligible is False, when
        assert ev.target_w_at(local(when, tz=zone), AUTO) == float("inf"), when


def test_the_danish_preset_carries_the_three_period_grid_tariff_for_d1() -> None:
    """DK's capacity answer is "none", but its time-of-use table is D1's (D2 §6)."""
    version = loader.load("dk/nopeak").versions[-1]
    periods = version.energy_components["tou_schedule"]["periods"]
    assert [period["name"] for period in periods] == ["spidslast", "hoejlast", "lavlast"]
    spids = periods[0]
    assert spids["hours"] == [[17 * 60, 21 * 60]]
    assert version.peak is None


@pytest.mark.parametrize("name", ["es/2_0td", "nl/connection"])
def test_12_contracted_presets_carry_the_limit_in_force_at_each_instant(name: str) -> None:
    """D2 §9 12, on the files: ES flips at 08:00 local and holidays fall to P2.

    NL is the other half of the same item: one limit, no clock. A connection is a
    fuse, so the limit at 03:00 is the limit at 19:00 - and a preset that carried
    a time filter it did not need would be the kind of copy that silently drifts.
    """
    data = market_golden(name)
    assert data is not None
    ev = _market_evaluator(data)
    zone = _zone(data)

    for case in data["limits"]:
        limit = ev.limit_now_w(local(case["local"], tz=zone), ANY_PROFILE)
        assert limit is not None, case["why"]
        assert limit.w == pytest.approx(case["limit_w"]), case["why"]
        assert limit.reason == "contracted_trip", case["why"]
        assert limit.tolerance_s == data["tolerance_s"], case["why"]
        assert limit.tolerance_w == pytest.approx(case["limit_w"] * data["tolerance_pct"]), case[
            "why"
        ]


@pytest.mark.parametrize("name", ["es/2_0td", "nl/connection"])
def test_12b_a_contracted_preset_has_no_capacity_ceiling_of_its_own(name: str) -> None:
    """ES and NL bill no measured peak: the breaker is the whole constraint (HLD §8)."""
    data = market_golden(name)
    assert data is not None
    ev = _market_evaluator(data)
    zone = _zone(data)

    assert ev.level().kind == "none"
    for when in data["ceiling_is_infinite_at"]:
        assert ev.ceiling_kwh(local(when, tz=zone), AUTO, 0.5, 0.3).kwh == float("inf"), when


def test_12c_the_spanish_preset_excludes_holidays_from_punta_and_nothing_else() -> None:
    """The P1 filter is the one place 2.0TD's holiday rule can live (D2 §2)."""
    contracted = loader.load("es/2_0td").versions[-1].contracted
    assert contracted is not None
    punta, valle = contracted.limits
    assert punta.when is not None
    assert punta.when.weekdays == (0, 1, 2, 3, 4)
    assert punta.when.hours == ((8 * 60, 24 * 60),)
    assert punta.when.holidays is HolidayMode.EXCLUDE
    assert valle.when is None, "P2 is the fall-through, so it carries no filter at all"
    assert contracted.on_exceed == "trip"


@pytest.mark.parametrize("name", ["us/aps-saver-choice-max", "us/srp-e27"])
def test_4_an_ineligible_window_never_enters_a_us_presets_metric(name: str) -> None:
    """D2 §9 4, on the files: the biggest window of the month can be worth nothing.

    Both US presets are eligibility-shaped: the demand charge reads only on-peak
    weekday afternoons, so an 8 kW evening is invisible and creates no day entry
    at all (D2 §5.1, D-0058). Getting this wrong does not misprice the month a
    little - it defends the wrong hours entirely.
    """
    data = market_golden(name)
    assert data is not None
    ev = _market_evaluator(data)
    zone = _zone(data)
    want = data["expected"]

    assert ev.metric() == pytest.approx(want["metric_kw"], abs=1e-9)
    assert ev.metric() != pytest.approx(want["not_metric_kw"], abs=1e-3)
    for day in data["no_day_entry_for"]:
        assert date.fromisoformat(day) not in ev.history.days, day

    for case in data["eligibility"]:
        when = local(case["local"], tz=zone)
        assert ev.eligible_now(when) is case["eligible"], case["why"]
        if not case["eligible"]:
            ceiling = ev.ceiling_kwh(when, AUTO, 0.5, 0.15)
            assert ceiling.kwh == float("inf"), case["why"]
            assert ceiling.eligible is False, case["why"]
            assert ceiling.weight == 0.0, case["why"]
            assert ev.target_w_at(when, AUTO) == float("inf"), case["why"]


@pytest.mark.parametrize("name", ["us/aps-saver-choice-max", "us/srp-e27"])
def test_4b_a_us_preset_lists_the_windows_d5_may_plan_into(name: str) -> None:
    """D5 asks a tariff which windows of a horizon are worth planning into (D2 §3)."""
    data = market_golden(name)
    assert data is not None
    want = data["eligible_windows"]
    ev = _market_evaluator(data)
    zone = _zone(data)

    windows = ev.eligible_windows(
        local(want["from_local"], tz=zone), local(want["to_local"], tz=zone)
    )
    assert len(windows) == want["count"], want["why"]
    assert all(weight == 1.0 for _start, _end, weight in windows)
    assert windows[0][0] == local(want["first_start"], tz=zone)
    assert windows[-1][1] == local(want["last_end"], tz=zone)


def test_4c_the_srp_preset_prices_its_demand_by_marginal_band() -> None:
    """SRP's three bands are `Tiers`, not `Linear`: 6.2 kW costs 35.70 + 63.904."""
    peak = loader.load("us/srp-e27").versions[-1].peak
    assert peak is not None
    assert peak.window_min == 30
    assert isinstance(peak.pricing, Tiers)
    assert [upto for upto, _price in peak.pricing.bands] == [3.0, 10.0, None]
    assert float(peak.pricing.fee_amount(2.0)) == pytest.approx(2.0 * 11.90)
    assert float(peak.pricing.fee_amount(6.2)) == pytest.approx(35.70 + 3.2 * 19.97)
    assert float(peak.pricing.fee_amount(12.0)) == pytest.approx(
        3.0 * 11.90 + 7.0 * 19.97 + 2.0 * 36.05
    )


def test_4d_the_ausgrid_preset_counts_every_day_and_only_two_seasons() -> None:
    """D2 §9 4 with no weekday filter: NSW's peak window runs weekends too.

    Two markets, two shapes of the same item - the US presets read weekday
    afternoons, Ausgrid reads 15:00-21:00 every day but only in June-August and
    November-March. A preset that copied the US weekday filter would make a
    Sunday evening free, which it is not.
    """
    data = market_golden("au/ausgrid-ea116")
    assert data is not None
    ev = _market_evaluator(data)
    zone = _zone(data)
    want = data["expected"]

    assert ev.metric() == pytest.approx(want["metric_kw"], abs=1e-9)
    assert ev.metric() != pytest.approx(want["not_metric_kw"], abs=1e-3)
    for case in data["eligibility"]:
        assert ev.eligible_now(local(case["local"], tz=zone)) is case["eligible"], case["why"]

    peak = loader.load("au/ausgrid-ea116").versions[-1].peak
    assert peak is not None
    assert peak.eligible is not None
    assert peak.eligible.weekdays is None, "every day, so no weekday restriction at all"
    assert peak.eligible.months == (1, 2, 3, 6, 7, 8, 11, 12)
    assert peak.price_period_unit == "year"


def test_4e_the_ausgrid_preset_lists_the_windows_d5_may_plan_into() -> None:
    """Twelve half-hours between 15:00 and 21:00, weights all 1.0 (D2 §3)."""
    data = market_golden("au/ausgrid-ea116")
    assert data is not None
    want = data["eligible_windows"]
    ev = _market_evaluator(data)
    zone = _zone(data)

    windows = ev.eligible_windows(
        local(want["from_local"], tz=zone), local(want["to_local"], tz=zone)
    )
    assert len(windows) == want["count"], want["why"]
    assert windows[0][0] == local(want["first_start"], tz=zone)
    assert windows[-1][1] == local(want["last_end"], tz=zone)


@pytest.mark.inv("INV-9")
def test_3d_the_free_ride_and_the_night_weight_compose_on_the_ellevio_file() -> None:
    """INV-9 on a weighted tariff: the free ride is derived, and a weight doubles it.

    Ellevio is the only shipped preset that is both `per_day = max` (so the free
    ride exists at all) and weighted, and the two interact: `to_kwh` divides by the
    weight, so the same costless slack is 7.70 kWh in an evening hour and 15.70 in
    a half-counted night hour. Nothing special-cases either - the ride falls out of
    `slack` and the doubling out of the weight (D2 §5.4, §5.6).
    """
    data = market_golden("se/ellevio")
    assert data is not None
    want = data["free_ride"]
    ev = _market_evaluator(data)
    zone = _zone(data)
    ev.record_window(
        closed(
            datetime.fromisoformat(want["today"]["local"]),
            want["today"]["kwh"],
            window_min=data["window_min"],
            tz=zone,
        )
    )

    assert ev.metric() == pytest.approx(want["metric_after_kw"], abs=1e-6)
    target = Target("kw", kw=want["target_kw"])
    slack = ev.slack_kw(local(want["checks"][0]["local"], tz=zone), want["target_kw"])
    assert slack == pytest.approx(want["slack_kw"], abs=1e-6)
    # The whole of INV-9 in one line: whatever the target says, the slack is never
    # below what today's own peak has already paid for.
    assert slack >= want["today_max_kw"] - 1e-6

    for check in want["checks"]:
        ceiling = ev.ceiling_kwh(
            local(check["local"], tz=zone), target, want["risk"], want["eps_kwh"]
        )
        assert ceiling.weight == check["weight"], check["why"]
        assert ceiling.kwh == pytest.approx(check["ceiling_kwh"], abs=1e-6), check["why"]
        assert ceiling.slack_kwh == pytest.approx(check["slack_kwh"], abs=1e-6), check["why"]
        assert ceiling.reason == check["reason"], check["why"]
        assert ceiling.free_ride is check["free_ride"], check["why"]
