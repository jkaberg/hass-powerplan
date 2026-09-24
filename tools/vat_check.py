#!/usr/bin/env python3
"""Compare every EU country module's VAT with the Commission's TEDB (D13 §9.1, O21).

`uv run python tools/vat_check.py [--at YYYY-MM-DD] [--response FILE]` asks TEDB's
open SOAP service (no key) for each member state's rates on the day, reads the
household-electricity rate - the reduced `SUPPLY_ELECTRICITY` rate where one is
listed, else the standard - and prints one markdown row per country whose module
says otherwise. A monthly CI step puts the table in the job summary; it warns and
never fails: a difference is read at the national authority before any module
changes, never copied blind (§9.1). `--response` reads a saved answer instead.

TEDB is known to be wrong for three countries (IE, CY and MT give the standard
rate) - those rows are expected while TEDB says what it says today, and flagged
the moment it says anything else.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.powerplan.core.tariffs import countries  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ENDPOINT = "https://ec.europa.eu/taxation_customs/tedb/ws/"
ACTION = "urn:ec.europa.eu:taxud:tedb:services:v1:VatRetrievalService/RetrieveVatRates"
SERVICE = "urn:ec.europa.eu:taxud:tedb:services:v1:IVatRetrievalService"
TYPES = f"{SERVICE}:types"

#: What TEDB says where §9.1 found it wrong, as captured.
KNOWN: Mapping[str, Decimal] = {
    "IE": Decimal("0.23"),
    "CY": Decimal("0.19"),
    "MT": Decimal("0.18"),
}


def request(day: date, codes: Sequence[str]) -> bytes:
    """Return the SOAP request for `codes`' rates on `day`, electricity only."""
    states = "".join(f"<t:isoCode>{code}</t:isoCode>" for code in codes)
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:s="{SERVICE}" xmlns:t="{TYPES}"><soapenv:Header/><soapenv:Body>'
        f"<s:retrieveVatRatesReqMsg><t:memberStates>{states}</t:memberStates>"
        f"<t:situationOn>{day.isoformat()}</t:situationOn>"
        "<t:categories><t:identifier>SUPPLY_ELECTRICITY</t:identifier></t:categories>"
        "</s:retrieveVatRatesReqMsg></soapenv:Body></soapenv:Envelope>"
    ).encode()


def fetch(day: date, codes: Sequence[str]) -> bytes:
    """Ask TEDB (CI only: nothing in the integration calls this)."""
    call = urllib.request.Request(
        ENDPOINT,
        data=request(day, codes),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{ACTION}"'},
    )
    with urllib.request.urlopen(call, timeout=60) as answer:
        body: bytes = answer.read()
    return body


def parse(body: bytes) -> dict[str, Decimal]:
    """Return each member state's household-electricity rate as a fraction."""
    standard: dict[str, Decimal] = {}
    electricity: dict[str, Decimal] = {}
    for row in ET.fromstring(body).iter(f"{{{TYPES}}}vatRateResults"):
        state = row.findtext(f"{{{TYPES}}}memberState") or ""
        value = row.findtext(f"{{{TYPES}}}rate/{{{TYPES}}}value")
        if value is None:
            continue
        rate = Decimal(value) / 100
        kind = row.findtext(f"{{{TYPES}}}type")
        category = row.findtext(f"{{{TYPES}}}category/{{{TYPES}}}identifier")
        # An exemption (Germany's 0 % on certain photovoltaic power) is not the
        # household's rate, and a regional row is a module's zone, not its nation.
        reduced = row.findtext(f"{{{TYPES}}}rate/{{{TYPES}}}type") == "REDUCED_RATE"
        if kind == "STANDARD":
            standard[state] = rate
        elif category == "SUPPLY_ELECTRICITY" and reduced:
            electricity[state] = rate
    return standard | electricity


def differences(
    tedb: Mapping[str, Decimal], day: date
) -> list[tuple[str, Decimal, Decimal | None]]:
    """Return `(country, TEDB's rate, the module's)` wherever they disagree unexpectedly."""
    found: list[tuple[str, Decimal, Decimal | None]] = []
    for code in countries.codes():
        module = countries.get(code)
        assert module is not None
        if module.tedb is None or module.tedb not in tedb:
            continue
        theirs = tedb[module.tedb]
        ours = module.vat_at(day)
        if theirs != ours and KNOWN.get(code) != theirs:
            found.append((code, theirs, ours))
    return found


def _percent(rate: Decimal | None) -> str:
    return "none" if rate is None else f"{(rate * 100).normalize():f} %"


def report(rows: Sequence[tuple[str, Decimal, Decimal | None]], day: date) -> str:
    """Return the markdown the CI step writes to the job summary."""
    if not rows:
        return f"Every EU country module agrees with TEDB on {day} (IE, CY, MT as §9.1 notes)."
    lines = [
        f"{len(rows)} country module(s) differ from TEDB on {day} – read the authority first:",
        "",
        "| country | TEDB | module |",
        "|---|---|---|",
    ]
    lines += [f"| {code} | {_percent(theirs)} | {_percent(ours)} |" for code, theirs, ours in rows]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the report; always exit 0 - this warns, it never fails a build."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--at", type=date.fromisoformat, default=date.today())
    parser.add_argument("--response", type=Path, help="a saved TEDB answer, instead of asking")
    args = parser.parse_args(argv)
    codes = sorted(
        module.tedb
        for code in countries.codes()
        if (module := countries.get(code)) is not None and module.tedb is not None
    )
    body = args.response.read_bytes() if args.response else fetch(args.at, codes)
    print(report(differences(parse(body), args.at), args.at))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
