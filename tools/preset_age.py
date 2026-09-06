#!/usr/bin/env python3
"""List the shipped facts verified more than six months ago (D9 §3, PLAN R12, D13 §12.2).

`uv run python tools/preset_age.py [--at YYYY-MM-DD] [--months 6]` prints one
markdown row per stale version: the file, the version, the date its source was
last read and the source to read again. A CI step puts the table in the job
summary; it warns and never fails - a stale table is a reason to re-read the
operator's sheet, not to block a merge (D2 §2, PLAN §7 dec. 21).

Templates and `custom` carry no prices and are never listed. The country modules'
dated rates - VAT and levies, national law (D13 §9.1, INV-70) - are aged the same
way, and a rate whose announced end (`until`) falls inside the window is listed
too: the law says it stops, and no successor is written yet.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.powerplan.core.tariffs import countries  # noqa: E402
from custom_components.powerplan.core.tariffs.rules import loader  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Sequence

#: PLAN R12: a release follows each 1 January and 1 July, so six months is a cycle.
STALE_MONTHS = 6


@dataclass(frozen=True, slots=True)
class Stale:
    """One version whose source has not been read for too long."""

    name: str
    valid_from: str
    verified: date
    source_url: str
    #: Why it is listed when not for its age: an announced end with no successor.
    note: str = ""


def shipped() -> list[str]:
    """Return every shipped rule file's load name under `rules/<cc>/`."""
    return sorted(
        path.relative_to(loader.HERE).with_suffix("").as_posix()
        for path in loader.HERE.glob("*/*.json")
    )


def _months_before(day: date, months: int) -> date:
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, min(day.day, 28))


def stale(at: date, months: int = STALE_MONTHS) -> list[Stale]:
    """Return the versions whose `verified` is more than `months` before `at`."""
    cutoff = _months_before(at, months)
    found: list[Stale] = []
    for name in shipped():
        raw = loader.load_raw(name)
        if raw.get("template"):
            continue
        for version in raw["versions"]:
            verified = date.fromisoformat(str(version.get("verified", raw.get("verified"))))
            if verified < cutoff:
                found.append(
                    Stale(
                        name=name,
                        valid_from=version["valid_from"],
                        verified=verified,
                        source_url=str(version.get("source_url", raw.get("source_url"))),
                    )
                )
    found += _stale_rates(at, cutoff, months)
    return found


def _stale_rates(at: date, cutoff: date, months: int) -> list[Stale]:
    """Return the country modules' rates read before `cutoff` or ending within `months`."""
    horizon = _months_before(at, -months)
    found: list[Stale] = []
    for code in countries.codes():
        module = countries.get(code)
        assert module is not None
        for rate in module.dated():
            ending = rate.until is not None and rate.until <= horizon
            if rate.verified < cutoff or ending:
                found.append(
                    Stale(
                        name=f"countries/{code.lower()}",
                        valid_from="—" if rate.valid_from is None else rate.valid_from.isoformat(),
                        verified=rate.verified,
                        source_url=rate.source,
                        note=f"ends {rate.until}" if ending else "",
                    )
                )
    return found


def report(rows: Sequence[Stale], at: date, months: int) -> str:
    """Return the markdown the CI step writes to the job summary."""
    if not rows:
        return f"No shipped fact was verified more than {months} months before {at}."
    lines = [
        f"{len(rows)} shipped fact(s) verified more than {months} months before {at}, or ending:",
        "",
        "| preset | version | verified | source |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{row.name}` | {row.valid_from} | {row.verified}{f' ({row.note})' if row.note else ''} "
        f"| {row.source_url} |"
        for row in rows
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the report; always exit 0 - this warns, it never fails a build."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--at", type=date.fromisoformat, default=date.today())
    parser.add_argument("--months", type=int, default=STALE_MONTHS)
    args = parser.parse_args(argv)
    print(report(stale(args.at, args.months), args.at, args.months))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
