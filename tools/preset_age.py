#!/usr/bin/env python3
"""List the shipped preset versions verified more than six months ago (D9 §3, PLAN R12).

`uv run python tools/preset_age.py [--at YYYY-MM-DD] [--months 6]` prints one
markdown row per stale version: the file, the version, the date its source was
last read and the source to read again. A CI step puts the table in the job
summary; it warns and never fails - a stale table is a reason to re-read the
operator's sheet, not to block a merge (D2 §2, PLAN §7 dec. 21).

Templates and `custom` carry no prices and are never listed.
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


def shipped() -> list[str]:
    """Return every shipped preset's load name under `presets/<cc>/`."""
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
    return found


def report(rows: Sequence[Stale], at: date, months: int) -> str:
    """Return the markdown the CI step writes to the job summary."""
    if not rows:
        return f"No shipped preset version was verified more than {months} months before {at}."
    lines = [
        f"{len(rows)} shipped preset version(s) verified more than {months} months before {at}:",
        "",
        "| preset | version | verified | source |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{row.name}` | {row.valid_from} | {row.verified} | {row.source_url} |" for row in rows
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
