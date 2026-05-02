"""The scenario catalogue, phase 0's rows (D9 §5.3).

`reference_winter_day`, `flat_price_night`, `dst_autumn`, `dst_spring` and
`price_outage_48h`, plus D11's two - `savings_vs_twin` (with its twin) and
`observe_calibration`. Each is a `Scenario` plus the expectations
`tests/scenarios/test_phase0.py` and `test_accounting.py` assert. The rows that
need groups, presence, cycles or a second market arrive with the work packages
that enable them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.model import Mode
from tests.builders.houses import house, no_peak, tensio
from tests.scenarios.runner import Fault, Scenario
from tests.sim.prices import FLAT, SPOT_LIKE

OSLO = ZoneInfo("Europe/Oslo")

#: A Tuesday in January 2027 - spot prices, the heating season, both Tensio
#: versions loaded (D9 §5.9's year). 16:17:17 local: the car arrives soon after.
WINTER_START = datetime(2027, 1, 12, 16, 17, 17, tzinfo=OSLO)

#: An October evening under Norgespris (flat until 2026-12-31).
FLAT_START = datetime(2026, 10, 6, 18, 2, 17, tzinfo=OSLO)

#: The DST days of D9 §5.9's year, started the evening before.
DST_AUTUMN_START = datetime(2026, 10, 24, 20, 3, 17, tzinfo=OSLO)
DST_SPRING_START = datetime(2027, 3, 27, 20, 3, 17, tzinfo=OSLO)

#: A November week, the 48 h outage in the middle of it.
OUTAGE_START = datetime(2026, 11, 9, 19, 4, 17, tzinfo=OSLO)
OUTAGE_DAYS = (date(2026, 11, 10), date(2026, 11, 11))


def reference_winter_day() -> Scenario:
    """Return a cold weekday: EV to 80 % by 07:30, tank ready by 06:30, bathrooms warm."""
    return Scenario(
        name="reference_winter_day",
        house=lambda: house(
            day=WINTER_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.35,
            slab_start_c=24.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
        ),
        start=WINTER_START,
        days=1.0,
    )


def flat_price_night() -> Scenario:
    """Return the Norgespris night: every slot costs the same, so nothing may churn (INV-32)."""
    return Scenario(
        name="flat_price_night",
        house=lambda: house(day=FLAT_START.date(), price_kind=FLAT, ev_soc=0.40, slab_start_c=24.0),
        start=FLAT_START,
        days=0.6,
    )


def dst_autumn() -> Scenario:
    """Return the 25-hour day: 25 windows, none missing, none doubled."""
    return Scenario(
        name="dst_autumn",
        house=lambda: house(
            day=DST_AUTUMN_START.date(), price_kind=FLAT, ev_soc=0.45, slab_start_c=24.0
        ),
        start=DST_AUTUMN_START,
        days=1.25,
    )


def dst_spring() -> Scenario:
    """Return the 23-hour day - the household's Easter week: 23 windows, plans without gaps."""
    return Scenario(
        name="dst_spring",
        house=lambda: house(
            day=DST_SPRING_START.date(), price_kind=SPOT_LIKE, ev_soc=0.45, slab_start_c=24.0
        ),
        start=DST_SPRING_START,
        days=1.25,
    )


def price_outage_48h() -> Scenario:
    """Return two days without prices: the synthesised floor plans on, hysteresis doubled."""
    return Scenario(
        name="price_outage_48h",
        house=lambda: house(
            day=OUTAGE_START.date(), price_kind=FLAT, ev_soc=0.40, slab_start_c=24.0
        ),
        start=OUTAGE_START,
        days=2.5,
        faults=tuple(Fault(kind="price_outage", day=d) for d in OUTAGE_DAYS),
    )


#: D11's month: `reference_winter_day`'s house from the first of its month, thirty
#: days inside January so the ledger's month is the run (D9 §5.3, D11 §9 17).
TWIN_START = WINTER_START.replace(day=1)
TWIN_DAYS = 30.0
#: Five local days of `observe` - D11 §5.5 asks for three before it trusts a shadow.
OBSERVE_DAYS = 5.0
OBSERVE_START = WINTER_START


def _winter_house(**overrides: object):
    return house(
        day=TWIN_START.date(),
        price_kind=SPOT_LIKE,
        ev_soc=0.35,
        slab_start_c=24.0,
        tank_top_c=62.0,
        tank_bottom_c=50.0,
        **overrides,  # type: ignore[arg-type]
    )


def savings_vs_twin() -> Scenario:
    """Return the controlled month: the winter house under Tensio, thirty days."""
    return Scenario(
        name="savings_vs_twin",
        house=_winter_house,
        start=TWIN_START,
        days=TWIN_DAYS,
    )


def savings_twin() -> Scenario:
    """Return the twin: the same month with every load `always` on a `NoPeak` site.

    No plan, no ceiling, no stage - the loads run on their own targets and the
    car charges at plug-in (HLD §10 decision 8). Its actual bill is what D11's
    counterfactual claims to predict.
    """
    return Scenario(
        name="savings_twin",
        house=lambda: _winter_house(strategy="always", tariff=no_peak(tensio())),
        start=TWIN_START,
        days=TWIN_DAYS,
        target_kw=None,
    )


def observe_calibration() -> Scenario:
    """Return five days with every load in `observe`: the shadows against the house itself."""
    return Scenario(
        name="observe_calibration",
        house=lambda: house(
            day=OBSERVE_START.date(),
            price_kind=SPOT_LIKE,
            ev_soc=0.35,
            slab_start_c=24.0,
            tank_top_c=62.0,
            tank_bottom_c=50.0,
        ),
        start=OBSERVE_START,
        days=OBSERVE_DAYS,
        modes=Mode.OBSERVE,
    )


PHASE0 = (reference_winter_day, flat_price_night, dst_autumn, dst_spring, price_outage_48h)
ACCOUNTING = (savings_vs_twin, savings_twin, observe_calibration)
#: The twin's month, for pricing its windows under the tariff the controlled house pays.
TWIN_END = TWIN_START + timedelta(days=TWIN_DAYS)
