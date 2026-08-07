"""D9 §5.3 - D11's two scenario rows: `savings_vs_twin` and `observe_calibration`.

The controlled month is run once and its twin - every load `always`, no
capacity axis - once more on the same house, the same weather, the same prices.
D11 never sees the twin: its counterfactual is the shadows' (D11 §5.3), and the
twin's real bill is what the counterfactual must land near. The twin's own
evaluator is `NoPeak`, so its windows are priced here under the tariff the
controlled house pays (D2 `bill`), which is the fee "no powerplan" would have
paid.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
from multiprocessing import get_context

import pytest

from custom_components.powerplan.core.accounting.shadow.base import StoreKind, shadow_for
from custom_components.powerplan.core.accounting_hook import params_of, store_kind_of
from custom_components.powerplan.core.tariffs.evaluator import Period
from tests.builders.houses import tensio
from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import ScenarioResult, run_scenario

pytestmark = pytest.mark.scenario

#: D9 §5.3: the counterfactual within ±10 % of the twin's actual cost.
TWIN_TOLERANCE = 0.10
#: D9 §5.3: `|savings| ≤ 5 %` of cost per load after five observe days.
OBSERVE_SAVINGS_SHARE = 0.05
#: D9 §5.3, D11 §5.5: calibration error below 0.10 on the house's own physics.
CALIBRATION_MAX = 0.10


def _run_named(name: str) -> ScenarioResult:
    """Run a named catalogue scenario.

    A plain top-level function so `ProcessPoolExecutor` can pickle a reference
    to it (perf, D-0321/WP6.1a).
    """
    return cached(__file__, name, lambda: run_scenario(getattr(catalogue, name)()))


@pytest.fixture(scope="module")
def _twin_pair() -> tuple[ScenarioResult, ScenarioResult]:
    """Run the controlled month and its twin concurrently, not sequentially.

    Both are thirty days (`TWIN_DAYS`) through the whole engine, independent
    and pure - profiling found this pair alone accounts for nearly all of
    `tests/scenarios`' own wall time (D-0321/WP6.1a). Two processes, not
    threads: the run is CPU-bound and the GIL would serialise it right back.
    """
    # `spawn`, not the default `forkserver`: the latter's control channel is a
    # Unix socket, which `pytest-socket` (on by default under
    # `pytest-homeassistant-custom-component`) blocks even for loopback IPC.
    # Not `fork` either - Python itself warns that forking xdist's own
    # multi-threaded worker can deadlock a child; `spawn` starts each process
    # clean, at the cost of a re-import `run_scenario` (minutes) dwarfs.
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        controlled_future = pool.submit(_run_named, "savings_vs_twin")
        twin_future = pool.submit(_run_named, "savings_twin")
        return controlled_future.result(), twin_future.result()


@pytest.fixture(scope="module")
def controlled(_twin_pair: tuple[ScenarioResult, ScenarioResult]) -> ScenarioResult:
    """Return the controlled month's result, from the concurrent pair."""
    return _twin_pair[0]


@pytest.fixture(scope="module")
def twin(_twin_pair: tuple[ScenarioResult, ScenarioResult]) -> ScenarioResult:
    """Return the twin month's result, from the concurrent pair."""
    return _twin_pair[1]


@pytest.fixture(scope="module")
def observed() -> ScenarioResult:
    """Run the five observe days once for the module."""
    return cached(__file__, "observed", lambda: run_scenario(catalogue.observe_calibration()))


def _money(text: str | None) -> Decimal:
    assert text is not None
    return Decimal(text.split(" ")[0])


def _month() -> str:
    return catalogue.TWIN_START.strftime("%Y-%m")


def _period() -> Period:
    start = catalogue.TWIN_START.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(month=start.month % 12 + 1, year=start.year + (start.month == 12))
    return Period(start=start, end=end, key=_month())


def _twin_fee(twin: ScenarioResult) -> Decimal:
    """Price the twin's windows under Tensio: what the household pays without powerplan."""
    assert twin.house is not None
    return tensio().bill(_period(), twin.house.tariff.history).capacity_fee.amount


def _controlled_fee(controlled: ScenarioResult) -> Decimal:
    assert controlled.house is not None
    return controlled.house.tariff.bill(_period()).capacity_fee.amount


# --------------------------------------------------------------------------- #
# savings_vs_twin
# --------------------------------------------------------------------------- #


@pytest.mark.xdist_group(name="accounting_twin")
@pytest.mark.inv("INV-69")
def test_the_counterfactual_lands_within_ten_percent_of_the_twins_bill(
    controlled: ScenarioResult, twin: ScenarioResult
) -> None:
    """D11's `cost_counterfactual` (energy + capacity) vs the twin's actual energy + Tensio fee."""
    month = _month()
    assert set(controlled.accounting_months) == {month}, controlled.accounting_months
    assert set(twin.accounting_months) == {month}, twin.accounting_months
    cf_cost = _money(controlled.accounting_months[month]["cf_cost"])
    twin_actual = _money(twin.accounting_months[month]["energy_cost"]) + _twin_fee(twin)
    assert twin_actual > 0
    gap = abs(cf_cost - twin_actual) / twin_actual
    assert gap <= TWIN_TOLERANCE, (cf_cost, twin_actual, gap)


@pytest.mark.xdist_group(name="accounting_twin")
def test_the_site_saves_money_and_the_sign_is_right(
    controlled: ScenarioResult, twin: ScenarioResult
) -> None:
    """The controlled month costs less than the twin, and D11 reports the savings positive."""
    month = _month()
    row = controlled.accounting_months[month]
    controlled_actual = _money(row["cost"])
    twin_actual = _money(twin.accounting_months[month]["energy_cost"]) + _twin_fee(twin)
    assert controlled_actual < twin_actual, (controlled_actual, twin_actual)
    assert _money(row["savings"]) > 0, row
    # D11 §9 10's identity, exactly; the site's own `cf_cost − cost` agrees to the
    # 1 Wh the site slots are quantised to (D11 §5.2), 2 880 slots deep.
    assert _money(row["savings"]) == _money(row["energy_savings"]) + _money(row["capacity_savings"])
    assert abs(_money(row["cf_cost"]) - controlled_actual - _money(row["savings"])) <= Decimal(
        "0.50"
    )


@pytest.mark.xdist_group(name="accounting_twin")
@pytest.mark.inv("INV-52")
def test_capacity_savings_are_the_twins_fee_minus_the_controlled_fee(
    controlled: ScenarioResult, twin: ScenarioResult
) -> None:
    """One evaluator, one version, two histories: the fee difference is exact (D11 §5.4)."""
    month = _month()
    reported = _money(controlled.accounting_months[month]["capacity_savings"])
    expected = _twin_fee(twin) - _controlled_fee(controlled)
    assert reported == expected, (reported, expected)
    assert _money(controlled.accounting_months[month]["capacity_fee"]) == _controlled_fee(
        controlled
    )


@pytest.mark.xdist_group(name="accounting_twin")
def test_the_twin_has_no_capacity_axis(twin: ScenarioResult) -> None:
    """`NoPeak` + `always`: no capacity figure of its own, nothing counted over a target."""
    month = _month()
    row = twin.accounting_months[month]
    assert _money(row["capacity_fee"]) == 0
    assert _money(row["capacity_savings"]) == 0
    assert twin.over_target == 0
    assert _twin_fee(twin) > _money(row["capacity_fee"]), "priced under Tensio it would have paid"


# --------------------------------------------------------------------------- #
# observe_calibration
# --------------------------------------------------------------------------- #


def _with_shadow(result: ScenarioResult) -> list[str]:
    assert result.house is not None
    return [
        load.load_id for load in result.house.loads if shadow_for(store_kind_of(load)) is not None
    ]


@pytest.mark.xdist_group(name="accounting_observed")
@pytest.mark.inv("INV-63")
def test_five_observe_days_calibrate_every_shadow_and_change_no_parameter(
    observed: ScenarioResult,
) -> None:
    """`|savings| ≤ 5 %` of cost, `calibration_error < 0.10`, `ok`, and the loads untouched."""
    with_shadow = _with_shadow(observed)
    assert with_shadow, "nothing to calibrate"
    for load_id in with_shadow:
        row = observed.accounting_per_load[load_id]
        cost = abs(_money(row["cost"]))
        assert cost > 0, (load_id, row)
        assert abs(_money(row["savings"])) <= cost * Decimal(str(OBSERVE_SAVINGS_SHARE)), (
            load_id,
            row,
        )
        assert row["calibration_error"] is not None, (load_id, row)
        assert row["calibration_error"] < CALIBRATION_MAX, (load_id, row)
        assert row["savings_confidence"] == "ok", (load_id, row)
    # The parameters D11 read are the ones the house was built with: nothing was fitted.
    fresh = catalogue.observe_calibration().house()
    assert observed.house is not None
    for load in observed.house.loads:
        assert params_of(load) == params_of(fresh.load(load.load_id)), load.load_id
        assert load.store == fresh.load(load.load_id).store, load.load_id


@pytest.mark.xdist_group(name="accounting_observed")
def test_a_load_without_a_shadow_states_cost_and_no_savings(observed: ScenarioResult) -> None:
    """The tank is `none` until WP3.3: its cost is shown, its savings are zero and unstated."""
    assert observed.house is not None
    tank = observed.house.load("tank")
    assert store_kind_of(tank) is StoreKind.TANK
    assert shadow_for(StoreKind.TANK) is None
    row = observed.accounting_per_load["tank"]
    assert _money(row["cost"]) > 0
    assert _money(row["savings"]) == 0
    assert row["savings_confidence"] == "none"
    assert row["calibration_error"] is None


@pytest.mark.xdist_group(name="accounting_observed")
def test_observe_writes_nothing(observed: ScenarioResult) -> None:
    """Every load in `observe`: the engine commands no device for five days (D4 §5.2)."""
    assert sum(observed.writes.values()) == 0, observed.writes
