"""The digest recorder and the side-by-side comparison (D9 §9 13).

`tools/digests.py` is how a speed change proves it moved no result: it runs the
simulations on two trees with `POWERPLAN_DIGESTS` set and compares the files.
These tests hold the two halves it relies on - that every result passing through
`cached()` is recorded, hit or miss, and that the comparison fails on a result
that moved or vanished.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from tests.scenarios import cache
from tools.digests import compare

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class _Result:
    digest: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Record into a scratch file, with the simulation cache on, in a scratch folder.

    The cache is switched on here, not inherited: the nightly run sets
    `POWERPLAN_SIM_CACHE=off` for the whole suite, and a cache-hit test must not
    depend on that.
    """
    out = tmp_path / "digests.json"
    monkeypatch.setenv("POWERPLAN_DIGESTS", str(out))
    monkeypatch.setenv("POWERPLAN_SIM_CACHE", "on")
    monkeypatch.setenv("POWERPLAN_SIM_CACHE_DIR", str(tmp_path / "sims"))
    return out


def test_an_uncached_result_is_recorded_under_its_module_and_name(
    recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the cache off the result is still recorded - the nightly run compares too."""
    monkeypatch.setenv("POWERPLAN_SIM_CACHE", "off")
    assert cache.cached(__file__, "one", lambda: _Result("a")) == _Result("a")
    assert json.loads(recorded.read_text(encoding="utf-8")) == {"test_digests::one": _sha("a")}


def test_a_tuple_records_every_digest_in_it_and_nothing_else(recorded: Path) -> None:
    """A fixture that returns `(result, trail)` is compared on what carries a digest."""
    cache.cached(__file__, "pair", lambda: (_Result("a"), object(), _Result("b")))
    assert json.loads(recorded.read_text(encoding="utf-8")) == {"test_digests::pair": _sha("a\nb")}


def test_a_cache_hit_is_recorded_as_well_as_a_miss(recorded: Path) -> None:
    """A warm reference tree still records every result it returns."""
    cache.cached(__file__, "hit", lambda: _Result("x"))
    recorded.unlink()

    def recompute() -> _Result:
        pytest.fail("a stored result was computed again")

    assert cache.cached(__file__, "hit", recompute) == _Result("x")
    assert json.loads(recorded.read_text(encoding="utf-8")) == {"test_digests::hit": _sha("x")}


def test_nothing_is_recorded_without_the_variable_or_without_a_digest(
    recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary run writes nothing, and a result with no digest has nothing to write."""
    cache.cached(__file__, "plain", lambda: 42)
    monkeypatch.delenv("POWERPLAN_DIGESTS")
    cache.cached(__file__, "unasked", lambda: _Result("y"))
    assert not recorded.exists()


def test_a_moved_or_missing_result_fails_and_a_new_one_is_only_noted() -> None:
    """A speed change may add a scenario; it may not move or lose one."""
    failures, notes = compare(
        {"same": "1", "moved": "9", "added": "5"}, {"same": "1", "moved": "2", "gone": "3"}
    )
    assert failures == ["moved    moved", "missing  gone"]
    assert notes == ["new      added"]


def test_a_reference_that_recorded_nothing_fails() -> None:
    """An empty reference would make every comparison pass, so it fails instead."""
    failures, _ = compare({"a": "1"}, {})
    assert failures == ["the reference recorded nothing"]
