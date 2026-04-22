"""Shared builders for the D11 accounting tests (D9 §3).

Nothing here starts Home Assistant: `tests/core/` is the pure half of the suite.

The price curves are assembled here from the shapes in `tests/builders/curves.py` -
the NO3 day with its negative hour, and the flat Norgespris day - rather than
composed through D1: what is under test is the arithmetic *over* a curve, and D1's
composition has its own tests. The numbers are still the project's own fixtures,
so a golden computed here is computed from the same prices every other test uses.

`drive()` is the only thing that knows how D7 will assemble a `ClosedSlot`, so a
test reads as a trace of kWh, a curve and an assertion.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.accounting import (
    Accounting,
    AccountingConfig,
    CloseCtx,
    ClosedSlot,
    CurvePair,
    LoadParams,
    ShadowCtx,
    SlotConfidence,
    StoreKind,
)
from custom_components.powerplan.core.metering import (
    AnchorKind,
    ClosedWindow,
    LoadEnergySource,
    LoadSlot,
)
from custom_components.powerplan.core.model import (
    Carrier,
    Confidence,
    Demand,
    Direction,
    Mode,
    PriceCurve,
    Slot,
    Urgency,
)
from custom_components.powerplan.core.tariffs import Evaluator, NoPeak
from tests.builders.curves import NO3_SHAPE, NORGESPRIS
from tests.core.tariffs.conftest import evaluator, no_tariff

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

OSLO = ZoneInfo("Europe/Oslo")
NOK = "NOK"

#: An ordinary winter day in the middle of the heating season (D9 §3).
ORDINARY = date(2026, 12, 3)


def local(*args: int, tz: tzinfo = OSLO) -> datetime:
    """Build a local wall-clock instant in the site's zone."""
    return datetime(*args, tzinfo=tz)


def no3(hour: int) -> Decimal:
    """Return the NO3-shaped spot price for a local hour, NOK/kWh (hour 13 is negative)."""
    return Decimal(NO3_SHAPE[hour])


def flat(_hour: int) -> Decimal:
    """Return the Norgespris price - the same in every slot (D1 §5.7)."""
    return NORGESPRIS


def curve(
    first: date = ORDINARY,
    days: int = 1,
    *,
    shape: Callable[[int], Decimal] = no3,
    minutes: int = 60,
    tz: tzinfo = OSLO,
    currency: str = NOK,
    direction: Direction = Direction.IMPORT,
    confidence: Confidence = Confidence.KNOWN,
    carrier: Carrier = Carrier.ELECTRICITY,
) -> PriceCurve:
    """Build a curve over `days` local days, priced by `shape(local hour)`.

    Slot starts are UTC-aligned, as D1's are, so a DST day holds 92 or 100 quarter
    slots and the repeated autumn hour is two slots with distinct UTC starts.
    """
    start = datetime.combine(first, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    end = datetime.combine(first + timedelta(days=days), datetime.min.time(), tzinfo=tz).astimezone(
        UTC
    )
    slots: list[Slot] = []
    cursor = start
    step = timedelta(minutes=minutes)
    while cursor < end:
        total = shape(cursor.astimezone(tz).hour)
        slots.append(
            Slot(
                start=cursor,
                end=cursor + step,
                total=total,
                components={"spot": total},
                confidence=confidence,
            )
        )
        cursor += step
    return PriceCurve(
        carrier=carrier,
        direction=direction,
        currency=currency,
        slots=tuple(slots),
        built_at=start,
        sources=("test",),
    )


def pair(import_curve: PriceCurve | None = None, export: PriceCurve | None = None) -> CurvePair:
    """Build the `(import, export)` pair of one carrier."""
    return CurvePair(
        import_curve=import_curve if import_curve is not None else curve(), export_curve=export
    )


def load_slot(
    load_id: str,
    start: datetime,
    kwh: float,
    *,
    minutes: int = 60,
    confidence: str = "exact",
) -> LoadSlot:
    """Build the `LoadSlot` D3's `LoadMeter` closes (D3 §4)."""
    return LoadSlot(
        load_id=load_id,
        start_utc=start,
        minutes=minutes,
        kwh=kwh,
        source=(LoadEnergySource.POWER if confidence == "exact" else LoadEnergySource.ESTIMATED),
        confidence=confidence,  # type: ignore[arg-type]
    )


def closed_slot(
    start: datetime,
    *,
    minutes: int = 60,
    import_kwh: float | None = None,
    export_kwh: float = 0.0,
    loads: Mapping[str, float] | None = None,
    confidence: SlotConfidence = SlotConfidence.EXACT,
    load_confidence: Mapping[str, str] | None = None,
    uncontrolled_kwh: float = 0.0,
    window_closed: ClosedWindow | None = None,
) -> ClosedSlot:
    """Build the `ClosedSlot` D7 assembles from D3 (D11 §4).

    `import_kwh` defaults to the loads plus `uncontrolled_kwh`, which is what a
    consistent house looks like and what makes the site identity of §9 10 a real
    assertion rather than an arranged one.
    """
    rows = dict(loads or {})
    marks = dict(load_confidence or {})
    return ClosedSlot(
        start_utc=start,
        minutes=minutes,
        import_kwh=(
            import_kwh if import_kwh is not None else sum(rows.values()) + uncontrolled_kwh
        ),
        export_kwh=export_kwh,
        site_confidence=confidence,
        loads={
            load_id: load_slot(
                load_id, start, kwh, minutes=minutes, confidence=marks.get(load_id, "exact")
            )
            for load_id, kwh in rows.items()
        },
        window_closed=window_closed,
    )


def window(start: datetime, kwh: float, *, window_min: int = 60) -> ClosedWindow:
    """Build the `ClosedWindow` D3 hands D2 when a tariff window ends."""
    return ClosedWindow(
        start_utc=start,
        window_min=window_min,
        kwh=kwh,
        avg_kw=kwh / (window_min / 60.0),
        anchor_kind=AnchorKind.REGISTER_LATCHED,
        degraded=False,
        confidence="exact",
    )


def demand(*, wants: bool, required_kwh: float | None = None, max_w: float = 11000.0) -> Demand:
    """Build the `Demand` the EV's shadow reads its plug-in edge from (D4 §4)."""
    return Demand(
        wants=wants,
        required_kwh=required_kwh,
        deadline=None,
        min_w=0.0,
        max_w=max_w,
        urgency=Urgency.NORMAL if wants else Urgency.NONE,
        comfort=None,
        price_sensitive=True,
        reason="test",
    )


def shadow_ctx(**kwargs: Any) -> ShadowCtx:
    """Build a `ShadowCtx`; `params` defaults to a load with no store model."""
    kwargs.setdefault("params", LoadParams(kind=StoreKind.NONE))
    return ShadowCtx(**kwargs)


def config(**kwargs: Any) -> AccountingConfig:
    """Build an `AccountingConfig` for the reference house (NOK, Europe/Oslo)."""
    kwargs.setdefault("currency", NOK)
    kwargs.setdefault("tz", OSLO)
    return AccountingConfig(**kwargs)


def no_peak() -> Evaluator:
    """Return an evaluator for a site with no capacity component (DK, UK, DE)."""
    return evaluator(NoPeak())


def tensio() -> Evaluator:
    """Return an evaluator for the Norwegian grammar: 60 min, daily max, top 3."""
    return evaluator(no_tariff())


@dataclass
class Site:
    """One site under test: the accounting, its curves and its tariff."""

    accounting: Accounting
    tariff: Evaluator
    curves: dict[Carrier, CurvePair] = field(default_factory=dict)
    loads: dict[str, ShadowCtx] = field(default_factory=dict)
    tz: tzinfo = OSLO

    def ctx(self, **overrides: Any) -> CloseCtx:
        """Return the `CloseCtx` D7 hands `close_slot`."""
        return CloseCtx(
            curves=overrides.get("curves", self.curves),
            tariff=self.tariff,
            history=self.tariff.history,
            loads=overrides.get("loads", self.loads),
            tz=self.tz,
        )

    def close(self, slot: ClosedSlot, **overrides: Any) -> Any:
        """Close one slot and return the report."""
        return self.accounting.close_slot(slot, self.ctx(**overrides))

    def with_load(self, load_id: str, ctx: ShadowCtx, *, at: datetime | None = None) -> Site:
        """Register a load and open its shadow, as D7's `on_load_added` does."""
        self.loads[load_id] = ctx
        self.accounting.on_load_added(
            load_id,
            ctx.params.kind,
            ctx.level_now,
            at if at is not None else datetime(2026, 1, 1, tzinfo=UTC),
            ctx,
        )
        return self

    def ctx_for(self, load_id: str, **overrides: Any) -> None:
        """Replace one load's shadow context - a new target, a new level, a new mode."""
        self.loads[load_id] = replace(self.loads[load_id], **overrides)


def site(
    *,
    tariff: Evaluator | None = None,
    import_curve: PriceCurve | None = None,
    export: PriceCurve | None = None,
    cfg: AccountingConfig | None = None,
) -> Site:
    """Build a site with one electricity carrier and no loads yet."""
    return Site(
        accounting=Accounting(cfg if cfg is not None else config()),
        tariff=tariff if tariff is not None else no_peak(),
        curves={Carrier.ELECTRICITY: pair(import_curve, export)},
    )


def hours(first: datetime, count: int) -> tuple[datetime, ...]:
    """Return `count` consecutive hourly slot starts from `first`."""
    return tuple(first + timedelta(hours=index) for index in range(count))


def price_at(import_curve: PriceCurve, start: datetime) -> Decimal:
    """Return the curve's own price for a slot - what a golden must agree with."""
    slot = import_curve.price_at(start)
    assert slot is not None, f"the curve does not cover {start.isoformat()}"
    return slot.total


def money_sum(values: Sequence[Decimal]) -> Decimal:
    """Sum exact amounts, so a golden's arithmetic is the test's own."""
    return sum(values, Decimal(0))


@pytest.fixture
def oslo() -> tzinfo:
    """Return the reference house's zone."""
    return OSLO


@pytest.fixture
def modes() -> type[Mode]:
    """Return the mode vocabulary, so a test need not import it."""
    return Mode
