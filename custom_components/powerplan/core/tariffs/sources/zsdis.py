"""Slovakia's HDO switching times from Západoslovenská distribučná (D13 §5.10; T4).

ZSDIS's page "Časy prepínania nízkej a vysokej tarify" carries every household
tariff programme (the old HDO code, 0.2.2 on the meter's display) as a
JavaScript literal, `household_rates`: per code its low-tariff (`nt`) intervals,
weekday and weekend flags, and what the code is for (water heating, storage
heating). No key, the page any visitor loads.

The copy is the grid's switching (D4 §5.16, G14): every household code as a
`SwitchedWindow`, and the house meter's own code as the grid's two-rate energy
charge - its NT windows at the NT price, the rest at the VT price, both asked
from the bill (the page states no prices, D-0604). Some older receivers run on
winter time all year (the page's own warning): asked, and then the windows are
read on standard time (G11). Pure.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from ..household import EXCL, EnergyPeriod, EnergyVersion, GridTariff, Provenance, SwitchedWindow
from ..model import NoPeak, TariffVersion, TimeFilter
from .base import Fetched, Operator, Product, QualityError, Question, slug

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["KEY", "PAGE", "codes", "operators", "parse", "windows"]

KEY: Final = "zsdis"
PAGE: Final = "https://www.zsdis.sk/Uvod/Online-sluzby/Casy-prepinania-nizkej-a-vysokej-tarify"
ATTRIBUTION: Final = "Západoslovenská distribučná, a. s."
_LITERAL: Final = re.compile(r"household_rates\s*=\s*(\[.*?\])\s*;", re.DOTALL)
_STRING: Final = re.compile(r"'((?:[^'\\]|\\.)*)'")
_KEY: Final = re.compile(r"([{,]\s*)([A-Za-z_]\w*)\s*:")
_WEEKDAYS: Final = (0, 1, 2, 3, 4)
_WEEKEND: Final = (5, 6)


def _minutes(clock: str) -> int:
    hours, minutes = (int(part) for part in clock.split(":"))
    return (hours * 60 + minutes) % (24 * 60)


def codes(page: bytes) -> list[dict[str, Any]]:
    """Return the page's household programmes: the JS literal read as JSON."""
    found = _LITERAL.search(page.decode("utf-8", "replace"))
    if found is None:
        msg = f"{KEY}: the page no longer carries household_rates"
        raise QualityError(msg)
    strings: list[str] = []

    def keep(match: re.Match[str]) -> str:
        strings.append(match.group(1))
        return f'"\x00{len(strings) - 1}\x00"'

    text = _STRING.sub(keep, found.group(1))
    text = _KEY.sub(r'\1"\2":', text)
    text = re.sub(r'"\x00(\d+)\x00"', lambda m: json.dumps(strings[int(m.group(1))]), text)
    try:
        rows = json.loads(text)
    except ValueError as err:
        msg = f"{KEY}: household_rates is not the literal it was: {err}"
        raise QualityError(msg) from err
    if not rows:
        msg = f"{KEY}: no household programme"
        raise QualityError(msg)
    return list(rows)


def windows(
    row: Mapping[str, Any], clock: Literal["local", "standard"] = "local"
) -> tuple[TimeFilter, ...]:
    """Return one programme's low-tariff windows."""
    out: list[TimeFilter] = []
    for interval in row["intervals"]:
        if str(interval.get("t_type")) != "nt":
            continue
        weekday, weekend = bool(interval.get("weekday")), bool(interval.get("weekend"))
        days = None if weekday and weekend else _WEEKDAYS if weekday else _WEEKEND
        span = (_minutes(str(interval["t_from"])), _minutes(str(interval["t_to"])))
        out.append(TimeFilter(weekdays=days, hours=(span,), clock=clock))
    return tuple(out)


def _meaning(row: Mapping[str, Any]) -> str:
    meanings = {str(i.get("meaning") or "") for i in row["intervals"]} - {""}
    return " / ".join(sorted(meanings)) or f"HDO {row['code']}"


def operators(page: bytes) -> list[Operator]:
    """Return ZSDIS with each household programme as a product: the meter's own code."""
    products = tuple(
        Product(str(row["code"]), f"{row['code']} – {_meaning(row)}")
        for row in sorted(codes(page), key=lambda row: int(row["code"]))
    )
    return [Operator("zsdis", ATTRIBUTION, products=products)]


def parse(page: bytes, code: str, *, fetched: date, answers: Mapping[str, Any]) -> Fetched:
    """Return the house meter's two rates on its code's windows, and every code's windows."""
    rows = {str(row["code"]): row for row in codes(page)}
    if code not in rows:
        msg = f"{KEY}: no household programme {code}"
        raise QualityError(msg)
    questions = [
        Question(
            "vt_price", 0.0, "The grid's high-tariff (VT) price per kWh excl. VAT, from your bill."
        ),
        Question(
            "nt_price", 0.0, "The grid's low-tariff (NT) price per kWh excl. VAT, from your bill."
        ),
        Question(
            "winter_time",
            False,
            "Does your HDO receiver stay on winter time all year? ZSDIS warns some do.",
        ),
    ]
    clock: Literal["local", "standard"] = "standard" if answers.get("winter_time") else "local"
    since = date(fetched.year, 1, 1)
    nt = Decimal(str(answers.get("nt_price", 0)))
    energy = EnergyVersion(
        valid_from=since,
        periods=tuple(
            EnergyPeriod(when=when, price=nt, name="nt") for when in windows(rows[code], clock)
        ),
        fallback=Decimal(str(answers.get("vt_price", 0))),
    )
    key = slug("sk", "zsdis", code, KEY)
    grid = GridTariff(
        operator=ATTRIBUTION,
        product=f"HDO {code}",
        provenance=Provenance(
            source=KEY, url=PAGE, fetched=fetched, attribution=ATTRIBUTION, tier="T4"
        ),
        currency="EUR",
        basis=EXCL,
        capacity=(
            TariffVersion(
                valid_from=since,
                version_id=f"{key}@{since.isoformat()}",
                rules=(NoPeak(),),
                verified=fetched.isoformat(),
                source_url=PAGE,
            ),
        ),
        energy=(energy,),
        switched=tuple(
            SwitchedWindow(key=str(number), name=_meaning(row), windows=windows(row, clock))
            for number, row in sorted(rows.items(), key=lambda item: int(item[0]))
        ),
        capacity_id=key,
        operator_key="zsdis",
        product_key=code,
    )
    return Fetched(grid=grid, questions=tuple(q for q in questions if q.key not in answers))
