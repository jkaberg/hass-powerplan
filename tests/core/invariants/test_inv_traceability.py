"""Every safety invariant has a marked test, or is on the pending list (D9 §5.6).

The safety set is HLD §7.5, parsed, not copied. Each id is one parametrised
case and it fails in both directions:

* marked nowhere and not on the pending list → the invariant has no test;
* on the pending list *and* marked → the list is stale, remove the line.

`tests/core/invariants/traceability_pending.txt` is therefore a ratchet: it is
seeded with the whole safety set at WP0.1 and every later WP deletes the lines
its tests cover. It can only shrink.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.inv_report import parse_declared_invs, parse_safety_invs, scan_inv_markers

PENDING_FILE = Path(__file__).with_name("traceability_pending.txt")

SAFETY = parse_safety_invs()
MARKERS = scan_inv_markers()


def _pending() -> set[str]:
    """Return the INV ids that are allowed to have no test yet."""
    entries: set[str] = set()
    for raw in PENDING_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            entries.add(line)
    return entries


def test_safety_set_is_not_empty() -> None:
    """Guard the guard: HLD §7.5 must still parse into invariants."""
    assert len(SAFETY) >= 40, SAFETY


def test_every_safety_inv_is_declared_in_the_hld() -> None:
    """A safety id that the HLD never defines is a typo in §7.5."""
    assert not set(SAFETY) - parse_declared_invs()


def test_pending_list_holds_only_safety_invs() -> None:
    """The pending list is about HLD §7.5 and nothing else."""
    assert not _pending() - set(SAFETY)


@pytest.mark.parametrize("inv_id", SAFETY)
def test_safety_inv_has_a_marked_test(inv_id: str) -> None:
    """Each safety INV is either covered by a marked test or listed as pending."""
    tests = MARKERS.get(inv_id, ())
    pending = inv_id in _pending()

    if tests and pending:
        pytest.fail(
            f"{inv_id} is covered by {list(tests)} but is still listed in "
            f"{PENDING_FILE.name}. Delete its line in this PR."
        )
    if not tests and not pending:
        pytest.fail(
            f'{inv_id} (HLD §7.5) has no @pytest.mark.inv("{inv_id}") test. '
            "Mark the test that guards it, or add the id to "
            f"{PENDING_FILE.name} with the WP that will cover it."
        )
