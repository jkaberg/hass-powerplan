"""A content-addressed cache of simulation results (D9 §5.13).

A scenario is a pure function of its inputs - D9 §9 8 asserts it byte-identical
across runs - so a result computed once is the result every later run would
compute, until one of those inputs changes. The key is therefore a SHA-256 over
everything a simulation can read:

* every file under `custom_components/powerplan/core/` (code and preset data);
* every `.py` under `tests/` that is not a test module - the simulators, the
  builders, the runner, the catalogue, the benchmark houses and years, and the
  conftests they import;
* the test module that asks, since its fixture decides what is run and kept;
* `uv.lock`, `pyproject.toml` and the Python version.

Change any of them and every key changes; a PR that touches `core/` recomputes
everything, one that touches only a flow recomputes nothing. A hit returns the
stored object, a miss runs and stores it. A lock per key means parallel xdist
workers and `spawn`ed processes compute each key once.

`POWERPLAN_SIM_CACHE=off` bypasses the cache, and the nightly run always does.
The cache lives in `.cache/powerplan-sims/` (git-ignored), or wherever
`POWERPLAN_SIM_CACHE_DIR` points - CI restores it from `main`.

`POWERPLAN_DIGESTS=<file>` also records the digest of every result that passes
through here, cached or not, into that JSON file. `tools/digests.py` runs the
suite that way on this tree and on a reference revision and fails on any
difference: the side-by-side check a speed change must pass (D9 §9 13).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import pickle
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_CORE: Final = REPO_ROOT / "custom_components" / "powerplan" / "core"
_TESTS: Final = REPO_ROOT / "tests"
_FILES: Final = (REPO_ROOT / "uv.lock", REPO_ROOT / "pyproject.toml")

_sources: str | None = None


def enabled() -> bool:
    """Return whether results are cached (`POWERPLAN_SIM_CACHE=off` says no)."""
    return os.environ.get("POWERPLAN_SIM_CACHE", "on").lower() not in {"off", "0", "false", "no"}


def cache_dir() -> Path:
    """Return where results are kept."""
    raw = os.environ.get("POWERPLAN_SIM_CACHE_DIR")
    return Path(raw) if raw else REPO_ROOT / ".cache" / "powerplan-sims"


def _inputs() -> list[Path]:
    paths = [
        path for path in _CORE.rglob("*") if path.is_file() and "__pycache__" not in path.parts
    ]
    paths += [
        path
        for path in _TESTS.rglob("*.py")
        if not path.name.startswith("test_") and "__pycache__" not in path.parts
    ]
    paths += [path for path in _FILES if path.exists()]
    return sorted(paths)


def source_digest() -> str:
    """Return the SHA-256 over every input a simulation can read, once per process."""
    global _sources  # noqa: PLW0603 - one digest per process, computed on first use
    if _sources is None:
        digest = hashlib.sha256(sys.version.encode())
        for path in _inputs():
            digest.update(str(path.relative_to(REPO_ROOT)).encode())
            digest.update(path.read_bytes())
        _sources = digest.hexdigest()
    return _sources


def key(module: str, name: str) -> str:
    """Return the cache key of result `name` asked for by test module `module`."""
    digest = hashlib.sha256(source_digest().encode())
    path = Path(module)
    digest.update(path.name.encode())
    digest.update(path.read_bytes())
    digest.update(name.encode())
    return digest.hexdigest()


def cached[T](module: str, name: str, run: Callable[[], T]) -> T:
    """Return `run()`'s result for `name`, from the cache when the inputs have not changed.

    `module` is the asking test module's `__file__`. A result that cannot be
    pickled is returned uncached; a stored result that cannot be read is
    recomputed. Neither is ever an error: the cache only ever saves time.
    """
    if not enabled():
        return _recorded(module, name, run())
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    entry = folder / f"{key(module, name)}.pickle"
    with (folder / f"{entry.stem}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if entry.exists():
            try:
                with entry.open("rb") as handle:
                    return _recorded(module, name, pickle.load(handle))
            except OSError, pickle.PickleError, EOFError, AttributeError, ImportError:
                _LOGGER.warning("simulation cache: %s unreadable, recomputing", entry.name)
        result = _recorded(module, name, run())
        try:
            payload = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
        except pickle.PickleError, TypeError, AttributeError:
            _LOGGER.warning("simulation cache: %s not picklable, not stored", name)
            return result
        partial = entry.with_suffix(".partial")
        partial.write_bytes(payload)
        partial.replace(entry)
        return result


def _digest_of(result: object) -> str | None:
    """Return the SHA-256 over every `digest` in `result` or its tuple, `None` with none."""
    parts = [result] + (list(result) if isinstance(result, tuple) else [])
    found = [part.digest for part in parts if isinstance(getattr(part, "digest", None), str)]
    if not found:
        return None
    return hashlib.sha256("\n".join(found).encode()).hexdigest()


def _recorded[T](module: str, name: str, result: T) -> T:
    """Return `result`, its digest recorded first when `POWERPLAN_DIGESTS` names a file.

    The entry is `<module stem>::<name>`; a result with no digest is not
    recorded. The file is locked, so parallel workers record safely.
    """
    target = os.environ.get("POWERPLAN_DIGESTS")
    digest = _digest_of(result)
    if not target or digest is None:
        return result
    path = Path(target)
    with path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        recorded = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        recorded[f"{Path(module).stem}::{name}"] = digest
        path.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n", "utf-8")
    return result
