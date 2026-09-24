"""Every safety invariant has a marked test (D9 §5.6).

The safety set is HLD §7.5, parsed, not copied. Each id is one parametrised case,
and it fails when no test carries `@pytest.mark.inv` for that id.
"""

from __future__ import annotations

import pytest

from tools.inv_report import parse_declared_invs, parse_safety_invs, scan_inv_markers

SAFETY = parse_safety_invs()
MARKERS = scan_inv_markers()


def test_safety_set_is_not_empty() -> None:
    """Guard the guard: HLD §7.5 must still parse into invariants."""
    assert len(SAFETY) >= 40, SAFETY


def test_every_safety_inv_is_declared_in_the_hld() -> None:
    """A safety id that the HLD never defines is a typo in §7.5."""
    assert not set(SAFETY) - parse_declared_invs()


@pytest.mark.parametrize("inv_id", SAFETY)
def test_safety_inv_has_a_marked_test(inv_id: str) -> None:
    """Each safety INV is covered by a marked test."""
    if not MARKERS.get(inv_id):
        pytest.fail(
            f'{inv_id} (HLD §7.5) has no @pytest.mark.inv("{inv_id}") test. '
            "Mark the test that guards it."
        )
