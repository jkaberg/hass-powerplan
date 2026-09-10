"""Spain's 2.0TD tolls and charges from REE's open PVPC file (D13 §5.10; T1a).

`api.esios.ree.es/archives/70/download_json?date=` (no token) gives each hour's
PVPC terms in €/MWh; `TEUPCB` is the energy term of the tolls and charges -
national, the same for every distributor - so one working day's file names all
three 2.0TD periods: P1 (10–14, 18–22), P2 (08–10, 14–18, 22–24), P3 (00–08,
and weekends and national holidays all day), Circular 3/2020 art. 7 as amended.
Every hour of the day must carry its period's one value, or the file fails
closed. The contracted kW per period stays the household's (the ES template's
`ContractedPower`, asked); the power terms are the ES module's (O22). Pure.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ..household import EXCL, EnergyPeriod, EnergyVersion, GridTariff, Provenance
from ..model import ContractedPower, HolidayMode, PeriodLimit, TariffVersion, TimeFilter
from .base import Fetched, Operator, QualityError, Question, slug

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["KEY", "PERIODS", "operators", "parse", "url", "working_day"]

KEY: Final = "esios"
PAGE: Final = "https://www.esios.ree.es"
ATTRIBUTION: Final = "Red Eléctrica (ESIOS)"
_WEEKDAYS: Final = (0, 1, 2, 3, 4)
#: 2.0TD's energy periods by hour on a working day.
PERIODS: Final = {
    "P1": ((10, 14), (18, 22)),
    "P2": ((8, 10), (14, 18), (22, 24)),
    "P3": ((0, 8),),
}
_MWH: Final = Decimal(1000)


def url(day: date) -> str:
    """Return the PVPC file of one day."""
    return f"https://api.esios.ree.es/archives/70/download_json?date={day.isoformat()}"


def working_day(today: date) -> date:
    """Return the last Monday–Friday before `today`: its file names all three periods."""
    day = today - timedelta(days=1)
    while day.weekday() >= 5:  # noqa: PLR2004 - Saturday
        day -= timedelta(days=1)
    return day


def operators() -> list[Operator]:
    """Return the one national tariff: 2.0TD is the same for every distributor."""
    return [Operator("2.0td", "Peajes y cargos 2.0TD (nacional)")]


def _period_of(hour: int) -> str:
    return next(key for key, spans in PERIODS.items() if any(a <= hour < b for a, b in spans))


def _terms(document: bytes) -> dict[str, Decimal]:
    try:
        rows = json.loads(document)["PVPC"]
    except (ValueError, KeyError, TypeError) as err:
        msg = f"{KEY}: not the PVPC file: {err}"
        raise QualityError(msg) from err
    found: dict[str, set[Decimal]] = {key: set() for key in PERIODS}
    for row in rows:
        hour = int(str(row["Hora"]).split("-")[0])
        found[_period_of(hour)].add(Decimal(str(row["TEUPCB"]).replace(",", ".")))
    if any(len(values) != 1 for values in found.values()):
        msg = f"{KEY}: the day's TEUPCB is not one value per 2.0TD period: {found}"
        raise QualityError(msg)
    return {key: values.pop() / _MWH for key, values in found.items()}


def _filter(spans: tuple[tuple[int, int], ...]) -> TimeFilter:
    return TimeFilter(
        weekdays=_WEEKDAYS,
        hours=tuple((a * 60, (b * 60) % 1440) for a, b in spans),
        holidays=HolidayMode.EXCLUDE,
    )


def parse(document: bytes, *, fetched: date, answers: Mapping[str, Any]) -> Fetched:
    """Return 2.0TD's energy term per period and the contract's two powers, asked."""
    terms = _terms(document)
    questions = (
        Question("p1_kw", 4.6, "Your contracted power for P1 (punta y llano), kW, from your bill."),
        Question("p2_kw", 4.6, "Your contracted power for P2 (valle), kW, from your bill."),
    )
    since = date(fetched.year, 1, 1)
    key = slug("es", "2.0td", KEY)
    power = ContractedPower(
        limits=(
            PeriodLimit(
                when=TimeFilter(
                    weekdays=_WEEKDAYS, hours=((480, 1440),), holidays=HolidayMode.EXCLUDE
                ),
                limit_kw=float(answers.get("p1_kw", 4.6)),
            ),
            PeriodLimit(when=None, limit_kw=float(answers.get("p2_kw", 4.6))),
        ),
        on_exceed="trip",
    )
    grid = GridTariff(
        operator="Peajes y cargos 2.0TD",
        product="2.0TD",
        provenance=Provenance(
            source=KEY, url=PAGE, fetched=fetched, attribution=ATTRIBUTION, tier="T1a"
        ),
        currency="EUR",
        basis=EXCL,
        capacity=(
            TariffVersion(
                valid_from=since,
                version_id=f"{key}@{since.isoformat()}",
                rules=(power,),
                verified=fetched.isoformat(),
                source_url=PAGE,
            ),
        ),
        energy=(
            EnergyVersion(
                valid_from=since,
                periods=tuple(
                    EnergyPeriod(_filter(PERIODS[key_]), terms[key_], key_) for key_ in ("P1", "P2")
                ),
                fallback=terms["P3"],
            ),
        ),
        capacity_id=key,
        operator_key="2.0td",
        product_key="2.0td",
    )
    return Fetched(grid=grid, questions=tuple(q for q in questions if q.key not in answers))
