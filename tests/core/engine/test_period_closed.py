"""D2 §2 / D8 §5.6's `period_closed` event, emitted on the ledger's own edge.

D11's ledger month and D2's tariff period are the same calendar boundary for
every preset shipped so far (`period: month`, never `year`) - `Engine.plan()`
fires `period_closed` on the same `close.month_closed` edge as `month_closed`
(`design/DECISIONS.md`), reusing D2's own `bill()` for the actual and the
counterfactual figure rather than recomputing anything D11 does not already
compute (INV-52, INV-69).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.engine import Engine, EventKind
from custom_components.powerplan.core.metering import AnchorKind, ClosedWindow
from custom_components.powerplan.core.tariffs import AUTO
from tests.core.engine.conftest import evaluator, site, window_meter

OSLO = ZoneInfo("Europe/Oslo")


def _window(local: datetime, kwh: float, *, window_min: int = 60) -> ClosedWindow:
    start = local.astimezone(OSLO).astimezone(ZoneInfo("UTC"))
    return ClosedWindow(
        start_utc=start,
        window_min=window_min,
        kwh=kwh,
        avg_kw=kwh / (window_min / 60),
        anchor_kind=AnchorKind.REGISTER_LATCHED,
        degraded=False,
        confidence="exact",
    )


def test_period_closed_fires_with_a_fee_pair_and_the_capacity_savings() -> None:
    """A December that peaked higher uncontrolled than controlled closes with positive savings."""
    tariff = evaluator()
    cfg = site()
    engine = Engine(cfg, window_meter(cfg), tariff, ())

    # The controlled house: a 4.5 kW December evening peak.
    tariff.record_window(_window(datetime(2026, 12, 15, 18, 0, tzinfo=OSLO), 4.5))
    # The counterfactual (uncontrolled) house: an 8.5 kW peak the same evening.
    tariff.record_counterfactual(_window(datetime(2026, 12, 15, 18, 0, tzinfo=OSLO), 8.5))
    # Touch January to roll December over and freeze it (D2 §5.9's `_touch`).
    tariff.ceiling_kwh(datetime(2027, 1, 1, 0, 30, tzinfo=OSLO), AUTO, 0.0, 0.0)

    event = engine._period_closed_event("2026-12")

    assert event is not None
    assert event.kind is EventKind.PERIOD_CLOSED
    assert event.data["period"] == "2026-12"
    assert event.data["metric_kw"] > 0.0
    fee = Decimal(event.data["fee"])
    cf_fee = Decimal(event.data["counterfactual_fee"])
    savings = Decimal(event.data["capacity_savings"])
    assert cf_fee >= fee, "the uncontrolled house peaked higher, so it never costs less"
    assert savings == cf_fee - fee
    assert savings > 0


def test_period_closed_is_none_for_an_unrecognisable_key() -> None:
    """A key that is not `YYYY-MM` - belt and braces - yields no event rather than raising."""
    tariff = evaluator()
    cfg = site()
    engine = Engine(cfg, window_meter(cfg), tariff, ())

    assert engine._period_closed_event("not-a-month") is None
