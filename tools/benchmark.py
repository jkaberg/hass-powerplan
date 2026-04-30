"""The reference benchmark from the command line (D9 §5.9, §5.11).

    uv run python tools/benchmark.py --tier smoke --compare
    uv run python tools/benchmark.py --tier month --compare --out /tmp/month.json
    uv run python tools/benchmark.py --tier smoke --update-baseline --since WP0.11

Runs `<house> × <year>` at a tier through the same engine Home Assistant runs,
prints the metric table a PR description carries, compares it with the
committed baseline (`tests/benchmark/baselines/<house>.json`) and exits 1 on a
breach. `--update-baseline` rewrites the tier's baseline; the line in
`design/benchmarks/CHANGELOG.md` that explains it is yours to add, and a test
fails without it (D9 §9 9). Tolerances are never loosened here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.benchmark import baseline as baselines  # noqa: E402 - the repo root first
from tests.benchmark.run import TIERS, run_benchmark  # noqa: E402
from tests.benchmark.year import y2026_27  # noqa: E402

YEARS = {"y2026_27": y2026_27}


def build_hash() -> str:
    """Return the short git hash of the working tree's HEAD, or `unknown`."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=10,
        )
    except OSError, subprocess.SubprocessError:
        return "unknown"
    return out.stdout.strip() or "unknown"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser (D9 §6)."""
    parser = argparse.ArgumentParser(
        prog="tools/benchmark.py", description=__doc__.split("\n\n")[1]
    )
    parser.add_argument(
        "--house", default="nordic_detached", help="a house under tests/benchmark/houses"
    )
    parser.add_argument("--year", default="y2026_27", choices=sorted(YEARS))
    parser.add_argument("--tier", default="smoke", choices=sorted(TIERS))
    parser.add_argument("--seed", type=int, help="override the year's seed")
    parser.add_argument(
        "--compare", action="store_true", help="compare with the baseline; exit 1 on a breach"
    )
    parser.add_argument(
        "--update-baseline", action="store_true", help="rewrite this tier's baseline"
    )
    parser.add_argument(
        "--since", default="", help="the WP that sets the baseline (with --update-baseline)"
    )
    parser.add_argument("--out", type=Path, help="write the full result and the table as JSON here")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one tier and report."""
    args = build_parser().parse_args(argv)
    build = build_hash()
    result = run_benchmark(
        args.house, YEARS[args.year](), tier=args.tier, seed=args.seed, build=build
    )
    document = baselines.load(args.house)
    tier_baseline = None if document is None else document.get("results", {}).get(args.tier)
    tolerances = (
        {key: baselines.Tolerance.from_dict(raw) for key, raw in document["tolerances"].items()}
        if document is not None and "tolerances" in document
        else dict(baselines.DEFAULT_TOLERANCES)
    )
    rows = baselines.compare(tier_baseline, result.as_dict(), result.perf, tolerances)
    print(
        baselines.render(
            rows,
            title=(
                f"benchmark {args.house} × {result.year} · {args.tier} · build {build} · "
                f"controlled {result.controlled_share:.0%} · "
                f"{result.perf['ticks']} ticks in {result.perf['wall_s']} s "
                f"({result.perf['ticks_per_s']} ticks/s)"
            ),
        )
    )
    print()
    print(_months_table(result))

    if args.out is not None:
        args.out.write_text(
            json.dumps(
                {
                    "result": result.as_dict(),
                    "perf": result.perf,
                    "rows": [
                        {
                            "metric": row.metric,
                            "baseline": row.baseline,
                            "now": row.now,
                            "tolerance": row.tolerance.as_dict(),
                            "ok": row.ok,
                        }
                        for row in rows
                    ],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    if args.update_baseline:
        document = document or {
            "house": args.house,
            "year": result.year,
            "tolerances": {key: tol.as_dict() for key, tol in tolerances.items()},
            "results": {},
        }
        document["results"][args.tier] = {
            **result.as_dict(),
            "perf": result.perf,
            "since": args.since or document.get("since", ""),
            "set_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        document["build"] = build
        document["since"] = args.since or document.get("since", "")
        path = baselines.save(args.house, document)
        print(
            f"baseline written: {path.relative_to(ROOT)} — add a line to design/benchmarks/CHANGELOG.md"
        )
        return 0
    if args.compare and tier_baseline is None:
        print(f"no {args.tier} baseline for {args.house}: nothing to compare against")
    breached = [row for row in rows if not row.ok]
    if args.compare and breached:
        print(f"{len(breached)} metric(s) outside tolerance")
        return 1
    return 0


def _months_table(result: object) -> str:
    months = getattr(result, "months", {})
    lines = [
        "| month | windows | over target | max kWh | kWh | comfort viol. min | deadline misses | writes | fee | level |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, row in sorted(months.items()):
        lines.append(
            f"| {key} | {row.windows} | {row.over_target} | {row.max_window_kwh:.2f} | {row.kwh:.1f} | "
            f"{row.comfort_violation_min:.1f} | {row.deadline_misses} | {row.writes} | "
            f"{row.fee or '—'} | {row.level or '—'} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
