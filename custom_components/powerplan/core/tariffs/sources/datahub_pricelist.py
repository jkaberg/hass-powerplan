"""Denmark's grid tariffs as Energinet's Datahub lists them (D13 §5.11; T1a).

[DatahubPricelist](https://www.energidataservice.dk/tso-electricity/DatahubPricelist)
is Energi Data Service's open dataset of every charge each grid company has
registered: a household's C tariff (`ChargeType` D03, `Note` "Nettarif C") is 24
hourly prices, excl. VAT, per `ValidFrom`/`ValidTo` - the next season's
included as soon as the company registers it. `elpris_dk` takes its future
seasons from here (D-0571) and the canary compares the two (§5.7). Pure.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote

from ..household import EnergyPeriod, EnergyVersion
from ..model import TimeFilter

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["DATASET", "HOURS", "hourly", "owned", "query", "versions"]

DATASET: Final = "https://api.energidataservice.dk/dataset/DatahubPricelist"
HOURS: Final = 24
_TARIFF: Final = "D03"


def query(code: str, since: date) -> str:
    """Return the dataset's URL for a charge code from `since`, every owner's.

    Not by owner: Datahub spells owners its own way ("L-Net A/S" where elpris.dk
    has "L-NET", "Konstant Net A/S - 151" for "KONSTANT Net A/S"), so `owned`
    picks the company's rows (D-0682).
    """
    wanted = f'{{"ChargeTypeCode":["{code}"],"ChargeType":["{_TARIFF}"]}}'
    return (
        f"{DATASET}?filter={quote(wanted)}&start={since.isoformat()}&limit=100&sort=ValidFrom%20asc"
    )


def owned(records: Sequence[Mapping[str, Any]], owner: str, area: str) -> list[Mapping[str, Any]]:
    """Return the rows of the company that owns `area`: the code's only owner, or its namesake.

    A code two companies use (Elinord's and Læsø's 43300) goes to the owner whose
    name is elpris.dk's, case and punctuation aside, the one suffixed with the
    area first ("Konstant Net A/S - 151"); no match, no rows.
    """
    owners = {str(row.get("ChargeOwner")) for row in records}
    if len(owners) > 1:
        wanted = _plain(owner)
        named = {name for name in owners if _plain(name).startswith(wanted)}
        suffixed = {name for name in named if _plain(name) == wanted + area}
        owners = suffixed or (named if len(named) == 1 else set())
    return [row for row in records if str(row.get("ChargeOwner")) in owners]


def _plain(name: str) -> str:
    return "".join(char for char in name.casefold() if char.isalnum())


def versions(
    records: Sequence[Mapping[str, Any]], code: str, extra: Decimal = Decimal(0)
) -> list[tuple[EnergyVersion, date | None]]:
    """Return each record of `code` as an energy version and the day after its last.

    `extra` is added to every hour - Energinet's own per-kWh tariffs, which are
    not in a grid company's rows.
    """
    found: list[tuple[EnergyVersion, date | None]] = []
    for record in sorted(records, key=lambda row: str(row.get("ValidFrom"))):
        if record.get("ChargeType") != _TARIFF or record.get("ChargeTypeCode") != code:
            continue
        since = datetime.fromisoformat(str(record["ValidFrom"])).date()
        until = record.get("ValidTo")
        prices = [
            Decimal(str(record.get(f"Price{hour + 1}") or record.get("Price1") or 0)) + extra
            for hour in range(HOURS)
        ]
        found.append(
            (
                hourly(since, prices),
                None if not until else datetime.fromisoformat(str(until)).date(),
            )
        )
    return found


def hourly(since: date, prices: Sequence[Decimal]) -> EnergyVersion:
    """Return 24 hourly prices as periods: the commonest price is the fallback."""
    counts: dict[Decimal, int] = {}
    for price in prices:
        counts[price] = counts.get(price, 0) + 1
    fallback = max(counts, key=lambda price: (counts[price], -prices.index(price)))
    periods: list[EnergyPeriod] = []
    for price in dict.fromkeys(prices):
        if price == fallback:
            continue
        hours = tuple(
            (hour * 60, (hour + 1) * 60) for hour, value in enumerate(prices) if value == price
        )
        periods.append(EnergyPeriod(when=TimeFilter(hours=_joined(hours)), price=price))
    return EnergyVersion(valid_from=since, periods=tuple(periods), fallback=fallback)


def _joined(hours: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    """Join adjacent hours: 17–18, 18–19 → 17–19; 23–24 is written as ending at 0."""
    joined: list[tuple[int, int]] = []
    for start, end in hours:
        if joined and joined[-1][1] == start:
            joined[-1] = (joined[-1][0], end)
        else:
            joined.append((start, end))
    return tuple((start, end % (HOURS * 60)) for start, end in joined)
