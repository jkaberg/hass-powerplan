"""D9 §9 8, 9 and 11 - the reference benchmark's own tests.

The fast half runs on every PR: the year's shape, the sources, the baseline
machinery and the controlled/uncontrolled split. The slow half - two `smoke`
runs byte-identical - is marked `bench` and runs where the tier does.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pytest

from tests.benchmark import baseline as baselines
from tests.benchmark.houses import nordic_detached as house_spec
from tests.benchmark.run import TIERS, run_benchmark
from tests.benchmark.year import YEAR_DAYS, SyntheticYear, y2026_27
from tests.builders.houses import ALL_LOADS, nordic_detached
from tests.scenarios.cache import cached
from tests.scenarios.runner import Scenario, run_scenario
from tests.sim.base import quarter_slots

HOURS_IN_THE_YEAR = 8760
AUTUMN_DST_SLOTS = 100
SPRING_DST_SLOTS = 92
ORDINARY_SLOTS = 96


@pytest.fixture(scope="module")
def year() -> SyntheticYear:
    """Return the v1 year."""
    return y2026_27()


def test_the_year_has_365_days_and_exactly_8760_hours(year: SyntheticYear) -> None:
    """The autumn +1 and the spring −1 cancel (D9 §9 8)."""
    start = datetime.combine(year.start, datetime.min.time(), tzinfo=year.tz)
    end = datetime.combine(year.end, datetime.min.time(), tzinfo=year.tz)
    assert year.days == YEAR_DAYS == 365
    assert (end - start) == timedelta(hours=HOURS_IN_THE_YEAR)


def test_the_dst_days_have_100_and_92_quarter_slots(year: SyntheticYear) -> None:
    """Slot length is a property of the slot (D1, INV-7)."""
    assert len(quarter_slots(date(2026, 10, 25), year.tz)) == AUTUMN_DST_SLOTS
    assert len(quarter_slots(date(2027, 3, 28), year.tz)) == SPRING_DST_SLOTS
    assert len(quarter_slots(date(2027, 1, 12), year.tz)) == ORDINARY_SLOTS


def test_the_year_carries_every_regime_and_fault_d9_names(year: SyntheticYear) -> None:
    """Flat → spot on 1 January, two negative days, one outage, faults on known dates."""
    kinds = [regime.kind for regime in year.price_regimes]
    assert kinds.count("outage") == 2
    assert kinds.count("negative_days") == 2
    assert "flat" in kinds
    assert "spot_like" in kinds
    fault_kinds = {fault.kind for fault in year.faults}
    assert fault_kinds == {"meter_stale", "ble_flap", "restart", "clock_jump", "price_outage"}
    assert sum(fault.kind == "meter_stale" for fault in year.faults) == 2
    assert sum(fault.kind == "restart" for fault in year.faults) == 12
    assert sum(fault.kind == "ble_flap" for fault in year.faults) >= 52
    weeks = year.faults_between(
        datetime(2026, 10, 5, tzinfo=year.tz), datetime(2026, 10, 12, tzinfo=year.tz)
    )
    assert {fault.kind for fault in weeks} == {"ble_flap"}


def test_every_generator_parameter_has_a_source(year: SyntheticYear) -> None:
    """A value without a source is a bug (D9 §2, §9 8)."""
    for name, source in {**year.sources, **house_spec.SOURCES}.items():
        assert isinstance(source, str), name
        assert source.strip(), name
    assert set(house_spec.CONTROLLED) <= set(ALL_LOADS)


def test_the_tiers_are_the_spans_d9_gives() -> None:
    """Smoke is two weeks, month is January 2027, full is the year (D9 §5.11)."""
    assert TIERS["smoke"] == ((date(2026, 10, 5), 7), (date(2027, 1, 11), 7))
    assert TIERS["month"] == ((date(2027, 1, 1), 31),)
    assert TIERS["full"] == ((date(2026, 7, 1), 365),)


# --------------------------------------------------------------------------- #
# D9 §9 9 - the baseline machinery
# --------------------------------------------------------------------------- #


def _document(**total: object) -> dict[str, object]:
    return {"total": {"over_target": 0, "fee": "400.00 NOK", "writes": 100, **total}}


def test_a_metric_outside_tolerance_fails_the_comparison_with_the_offending_row() -> None:
    """`not_worse` on money, `pct` on writes, `zero` enforced whatever the baseline said."""
    tolerances = {
        "total.over_target": baselines.Tolerance("not_worse"),
        "total.fee": baselines.Tolerance("not_worse"),
        "total.writes": baselines.Tolerance("pct", 10.0),
        "total.engine_failures": baselines.Tolerance("zero"),
        "perf.tick_p95_ms": baselines.Tolerance("abs", 2.0),
    }
    baseline = _document(engine_failures=0)
    good = baselines.compare(
        baseline, _document(engine_failures=0, writes=109), {"tick_p95_ms": 1.5}, tolerances
    )
    assert all(row.ok for row in good)

    worse = baselines.compare(
        baseline,
        _document(over_target=1, fee="401.00 NOK", writes=111, engine_failures=1),
        {"tick_p95_ms": 2.5},
        tolerances,
    )
    breached = {row.metric for row in worse if not row.ok}
    assert breached == {
        "total.over_target",
        "total.fee",
        "total.writes",
        "total.engine_failures",
        "perf.tick_p95_ms",
    }
    table = baselines.render(worse, title="t")
    assert "BREACH" in table
    assert "| total.fee | 400.00 NOK | 401.00 NOK | not_worse | BREACH |" in table


def test_without_a_baseline_only_zero_and_abs_can_fail() -> None:
    """The first run has nothing to be worse than, and still no failures allowed."""
    tolerances = {
        "total.fee": baselines.Tolerance("not_worse"),
        "total.engine_failures": baselines.Tolerance("zero"),
    }
    rows = baselines.compare(None, _document(engine_failures=2), {}, tolerances)
    verdicts = {row.metric: row.ok for row in rows}
    assert verdicts == {"total.fee": True, "total.engine_failures": False}


def test_every_committed_baseline_has_a_changelog_line() -> None:
    """A baseline update without a CHANGELOG line fails (D9 §9 9)."""
    for path in sorted(baselines.BASELINES.glob("*.json")):
        document = baselines.load(path.stem)
        assert document is not None
        build = document.get("build", "")
        assert build, f"{path.name} carries no build hash"
        assert baselines.changelog_mentions(build), (
            f"{path.name} was set at build {build}; design/benchmarks/CHANGELOG.md does not say why"
        )
        for tier_result in document.get("results", {}).values():
            for key, tolerance in document["tolerances"].items():
                assert tolerance["kind"] in ("zero", "not_worse", "not_less", "pct", "abs"), key
            assert tier_result["controlled_share"] <= 1.0


# --------------------------------------------------------------------------- #
# D9 §9 11 - uncontrolled loads run on their own logic
# --------------------------------------------------------------------------- #


def test_uncontrolled_loads_are_metered_and_the_share_matches_the_build(
    year: SyntheticYear,
) -> None:
    """A build that steers only the EV still sees the rest of the house on the meter."""
    start = datetime(2027, 1, 12, 17, 2, 17, tzinfo=year.tz)
    scenario = Scenario(
        name="ev_only",
        house=lambda: nordic_detached(
            start=year.start,
            price_regimes=year.price_regimes,
            weather_events=year.weather_events,
            controlled=frozenset({"ev"}),
        ),
        start=start,
        days=0.05,
    )
    result = run_scenario(scenario)
    house = result.house
    assert house is not None
    assert set(house.passive) == set(ALL_LOADS) - {"ev"}
    assert [load.load_id for load in house.loads] == ["ev"]
    assert result.controlled_share == pytest.approx(1 / len(ALL_LOADS))
    assert result.engine_failures == 0
    # The passive floors, tank and heat pump drew power the site meter integrated.
    passive_kwh = sum(getattr(sim, "energy_in_kwh", 0.0) for sim in house.passive.values())
    assert passive_kwh > 0.0
    assert house.meter.true_import_kwh - 100_000.0 >= passive_kwh * 0.9


# --------------------------------------------------------------------------- #
# D9 §9 8 - determinism, at the tier that runs on every PR
# --------------------------------------------------------------------------- #


@pytest.mark.bench
def test_smoke_runs_byte_identically_twice() -> None:
    """Two runs, same seed → the same `BenchmarkResult` (D9 §9 8).

    The two runs go at once rather than one after the other (D9 §5.13, T.1a):
    each span of each run is its own `spawn`ed process, so the threads here only
    wait, and every span runs in a fresh interpreter with its own hash seed - a
    set iterated in hash order would show. The first run comes from the
    simulation cache when the sources are unchanged (T.1b): it was produced by
    an earlier process from byte-identical inputs, so comparing it with a fresh
    run is the same check across time as well as across processes. The second
    run is never cached.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        stored = pool.submit(
            cached,
            __file__,
            "smoke",
            lambda: run_benchmark("nordic_detached", y2026_27(), tier="smoke"),
        )
        fresh = pool.submit(run_benchmark, "nordic_detached", y2026_27(), tier="smoke")
        first, second = stored.result(), fresh.result()
    assert first.digest == second.digest
