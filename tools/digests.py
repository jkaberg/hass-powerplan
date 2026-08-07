"""Compare every simulation's result with a reference revision's (D9 §9 13).

A speed change - a memo, a fast path, a compiled build - may change how long a
simulation takes and nothing else. This runs the scenario suite and the smoke
benchmark's determinism test on this tree and on a reference revision, each
recording the digest of every result it computes (`POWERPLAN_DIGESTS`, see
`tests/scenarios/cache.py`), and fails on any entry that moved or went missing.

Usage::

    uv run python tools/digests.py                  # against main
    uv run python tools/digests.py --against b6fd20c

The reference is exported once per commit to `.cache/digests/<sha>/`, with its
own simulation cache, so a second comparison against the same commit is warm.
Both trees run with this checkout's environment. A reference from before the
recorder existed gets this tree's `tests/scenarios/cache.py`: the cache decides
nothing a simulation computes, so the reference's results stay its own.

Nothing pins the hash seed: a result that depends on it is a bug the diff
should show, not hide.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORK = REPO_ROOT / ".cache" / "digests"

#: What is compared: every cached scenario fixture and the smoke benchmark.
SUITE = ("tests/scenarios", "tests/benchmark/test_benchmark.py")
#: The PR suite's selection plus `bench`: the smoke determinism test is the one
#: `bench` test, and its cached run is the benchmark result being compared.
MARKERS = "not perf and not backtest and not e2e"
_RECORDER = Path("tests/scenarios/cache.py")


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True).stdout


def reference_tree(rev: str) -> Path:
    """Return a clean export of `rev`, made on first use and kept for the next run."""
    sha = _git("rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    tree = WORK / sha
    if not tree.exists():
        partial = WORK / f"{sha}.partial"
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(_git("archive", sha)), mode="r:") as archive:
            archive.extractall(partial, filter="data")
        partial.rename(tree)
    recorder = tree / _RECORDER
    if "POWERPLAN_DIGESTS" not in recorder.read_text(encoding="utf-8"):
        shutil.copyfile(REPO_ROOT / _RECORDER, recorder)
    return tree


def _start(tree: Path, out: Path, workers: str) -> subprocess.Popen[bytes]:
    out.unlink(missing_ok=True)
    env = {**os.environ, "POWERPLAN_DIGESTS": str(out)}
    command = [
        sys.executable,
        "-m",
        "pytest",
        *SUITE,
        "-m",
        MARKERS,
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    command += ["-n", workers, "--dist=loadgroup"]
    log = out.with_suffix(".log").open("wb")
    return subprocess.Popen(command, cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT)


def compare(ours: dict[str, str], theirs: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return `(failures, notes)`: moved or missing entries fail, new ones are noted."""
    failures = [
        f"moved    {label}"
        for label in sorted(ours.keys() & theirs.keys())
        if ours[label] != theirs[label]
    ]
    failures += [f"missing  {label}" for label in sorted(theirs.keys() - ours.keys())]
    if not theirs:
        failures.append("the reference recorded nothing")
    notes = [f"new      {label}" for label in sorted(ours.keys() - theirs.keys())]
    return failures, notes


def main(argv: list[str] | None = None) -> int:
    """Run both trees, print the difference, and fail on any moved result."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--against", default="main", help="the reference revision (default: main)")
    parser.add_argument("--workers", default="auto", help="pytest-xdist workers per tree")
    args = parser.parse_args(argv)

    reference = reference_tree(args.against)
    ours_file = WORK / "current.json"
    theirs_file = WORK / f"{reference.name}.json"
    runs = {
        "this tree": _start(REPO_ROOT, ours_file, args.workers),
        f"reference {reference.name[:12]}": _start(reference, theirs_file, args.workers),
    }
    status = 0
    for label, run in runs.items():
        code = run.wait()
        print(f"{label}: pytest exit {code}")
        status = status or code
    if status:
        print(f"a test run failed; its log is beside {ours_file.relative_to(REPO_ROOT)}")
        return status

    ours = json.loads(ours_file.read_text(encoding="utf-8"))
    theirs = json.loads(theirs_file.read_text(encoding="utf-8"))
    failures, notes = compare(ours, theirs)
    for line in [*failures, *notes]:
        print(line)
    print(f"{len(ours)} results compared, {len(failures)} differ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
