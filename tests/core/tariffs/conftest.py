"""Shared fixtures for the D2 tariff tests (D9 §3).

Nothing here starts Home Assistant: `tests/core/` is the pure half of the suite
(D9 §3). Three helpers carry every test in this directory:

* `closed()` builds a `ClosedWindow` exactly as D3's meter hands one over, from a
  local wall-clock instant - because a tariff is a local-time object keyed in UTC
  (D2 §2) and a test that writes UTC by hand hides every DST question.
* `spec()` wraps one grammar root in a one-version `TariffSpec`. The presets for
  SE/FI/BE/ES/US/AU are WP4.3, so the tests for their grammar (D2 §9 3, 4, 8, 9,
  12) build it inline here rather than shipping files this WP does not own.
* `Holidays` is the site's calendar. D1's `HolidayCalendar` is a `Protocol` and so
  is D2's, so a set of dates satisfies both.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.metering import AnchorKind, ClosedWindow
from custom_components.powerplan.core.model import Money
from custom_components.powerplan.core.tariffs import (
    AUTO,
    Evaluator,
    Grammar,
    PeakTariff,
    Step,
    StepTable,
    Target,
    TariffSpec,
    TariffVersion,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

OSLO = ZoneInfo("Europe/Oslo")
MADRID = ZoneInfo("Europe/Madrid")
BRUSSELS = ZoneInfo("Europe/Brussels")
PHOENIX = ZoneInfo("America/Phoenix")

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "presets"

#: Tensio's 2026-01-01 household table (D2 §6). The one step table used
#: wherever a test needs a Norwegian one without loading a preset file.
NO_STEPS = StepTable(
    steps=(
        Step(2.0, Money(Decimal(137), "NOK"), "0–2 kW"),
        Step(5.0, Money(Decimal(244), "NOK"), "2–5 kW"),
        Step(10.0, Money(Decimal(416), "NOK"), "5–10 kW"),
        Step(15.0, Money(Decimal(613), "NOK"), "10–15 kW"),
        Step(20.0, Money(Decimal(812), "NOK"), "15–20 kW"),
        Step(None, Money(Decimal(1200), "NOK"), "over 20 kW"),
    )
)


@dataclass(frozen=True)
class Holidays:
    """The site's holiday calendar (D1 §4's `HolidayCalendar`, structurally)."""

    days: frozenset[date] = field(default_factory=frozenset)

    def is_holiday(self, day: date) -> bool:
        """Return whether `day` is a public holiday."""
        return day in self.days

    def name(self, day: date) -> str | None:
        """Return a name for the holiday, or `None` on an ordinary day."""
        return "holiday" if day in self.days else None


NO_HOLIDAYS = Holidays(frozenset({date(2026, 5, 1), date(2026, 5, 17), date(2026, 12, 25)}))
#: 2026-01-01 (Año Nuevo) and 2026-01-06 (Reyes) are the two ES 2.0TD holidays
#: the P1/P2 test needs; 2.0TD treats every national holiday as P2 (D2 §2).
ES_HOLIDAYS = Holidays(frozenset({date(2026, 1, 1), date(2026, 1, 6)}))


def no_tariff(**overrides: Any) -> PeakTariff:
    """Return the Norwegian grammar: 60 min, daily max, mean of the top 3 days."""
    fields: dict[str, Any] = {
        "window_min": 60,
        "eligible": None,
        "weights": (),
        "per_day": "max",
        "per_period": "mean_top_n",
        "n": 3,
        "distinct_days": True,
        "period": "month",
        "pricing": NO_STEPS,
    }
    fields.update(overrides)
    return PeakTariff(**fields)


def spec(
    *grammar: Grammar,
    currency: str = "NOK",
    preset_id: str = "test.inline",
    valid_from: date = date(2020, 1, 1),
) -> TariffSpec:
    """Wrap `grammar` in a one-version `TariffSpec`, as the loader would."""
    return TariffSpec(
        id=preset_id,
        name=preset_id,
        currency=currency,
        country=None,
        operator=None,
        source_url=None,
        verified=None,
        assumed="built inline by a test",
        versions=(
            TariffVersion(
                valid_from=valid_from,
                version_id=f"{preset_id}@{valid_from.isoformat()}",
                grammar=tuple(grammar),
            ),
        ),
    )


def evaluator(
    *grammar: Grammar,
    tz: tzinfo = OSLO,
    calendar: Holidays | None = None,
    currency: str = "NOK",
    target: Target = AUTO,
    risk: float | None = None,
) -> Evaluator:
    """Build an `Evaluator` over one inline grammar root."""
    return Evaluator(
        spec(*grammar, currency=currency),
        tz=tz,
        calendar=calendar if calendar is not None else Holidays(),
        target=target,
        risk=risk,
    )


def closed(
    local: datetime,
    kwh: float,
    *,
    window_min: int = 60,
    tz: tzinfo = OSLO,
    confidence: str = "exact",
    degraded: bool = False,
    fold: int = 0,
) -> ClosedWindow:
    """Build the `ClosedWindow` D3 hands over for a local wall-clock window start.

    `fold` picks which of the two repeated autumn hours is meant (D2 §2, INV-7).
    """
    start = local.replace(tzinfo=tz, fold=fold).astimezone(ZoneInfo("UTC"))
    return ClosedWindow(
        start_utc=start,
        window_min=window_min,
        kwh=kwh,
        avg_kw=kwh / (window_min / 60),
        anchor_kind=AnchorKind.REGISTER_LATCHED,
        degraded=degraded,
        confidence=confidence,  # type: ignore[arg-type]
    )


def record_days(
    ev: Evaluator,
    maxima: Mapping[str, float],
    *,
    hour: int = 18,
    tz: tzinfo = OSLO,
    confidence: str = "exact",
) -> None:
    """Record one 60-min window per ISO date, so each day's max is `maxima[day]`."""
    for day, kw in maxima.items():
        ev.record_window(
            closed(
                datetime.fromisoformat(f"{day}T{hour:02d}:00:00"),
                kw,
                tz=tz,
                confidence=confidence,
            )
        )


def record_golden(ev: Evaluator, rows: Iterable[Mapping[str, Any]], tz: tzinfo = OSLO) -> None:
    """Record the `windows` rows of a golden file."""
    for row in rows:
        ev.record_window(closed(datetime.fromisoformat(row["local"]), row["kwh"], tz=tz))


def golden(preset_id: str) -> dict[str, Any]:
    """Load the committed golden file for a preset (D9 §3)."""
    data: dict[str, Any] = json.loads((GOLDEN / f"{preset_id}.json").read_text(encoding="utf-8"))
    return data


def local(value: str, tz: tzinfo = OSLO) -> datetime:
    """Parse an ISO local wall-clock instant in the site's zone."""
    return datetime.fromisoformat(value).replace(tzinfo=tz)


def daily(ev: Evaluator, days: Sequence[str]) -> list[float]:
    """Return the weighted daily maxima the history holds for `days`."""
    return [ev.history.days[date.fromisoformat(day)].max_weighted_kw for day in days]


@pytest.fixture
def oslo() -> tzinfo:
    """Return the reference house's zone."""
    return OSLO
