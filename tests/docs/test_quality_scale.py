"""D14 §9 10: every `docs-*` rule of the quality scale names its page, or says why not.

A rule is `done` with the page that meets it named in its comment, and that
page written; `exempt` with a reason; or `todo` while a page D14 §3.1 maps it
to is still pending. The last `todo` goes in DOC.4, and v1.0 needs none.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from tests.docs.pages import INTEGRATION, is_pending, written

#: D14 §3.1's "HA `docs-*` rule" column: the pages that meet each rule.
RULE_PAGES: dict[str, tuple[str, ...]] = {
    "docs-high-level-description": ("README.md", "how-it-works.md"),
    "docs-use-cases": ("README.md",),
    "docs-installation-instructions": ("install.md",),
    "docs-removal-instructions": ("install.md",),
    "docs-installation-parameters": ("get-started.md", "setup.md"),
    "docs-configuration-parameters": (
        "setup.md",
        "appliances/README.md",
        "circuits-groups-rooms.md",
        "tariffs.md",
    ),
    "docs-supported-devices": ("appliances/README.md", "devices.md", "prices.md"),
    "docs-supported-functions": ("entities.md", "strategies.md", "dashboard.md", "daily-use.md"),
    "docs-actions": ("actions.md",),
    "docs-triggers": ("events.md",),
    "docs-conditions": (),
    "docs-examples": ("examples.md",),
    "docs-data-update": ("how-it-works.md",),
    "docs-known-limitations": ("limitations.md",),
    "docs-troubleshooting": ("troubleshooting.md",),
}


def _rules() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((INTEGRATION / "quality_scale.yaml").read_text("utf-8"))
    return {key: value for key, value in loaded["rules"].items() if key.startswith("docs-")}


def test_10_every_docs_rule_is_mapped() -> None:
    """The quality scale's `docs-*` rules are the ones D14 §3.1 maps."""
    assert set(_rules()) == set(RULE_PAGES)


@pytest.mark.parametrize("rule", sorted(RULE_PAGES))
def test_10_every_docs_rule_names_its_page_or_says_why_not(rule: str) -> None:
    """`done` names a written page; `exempt` gives a reason; `todo` waits on a pending page."""
    value = _rules()[rule]
    status = value if isinstance(value, str) else value["status"]
    comment = "" if isinstance(value, str) else value.get("comment", "")
    pages = RULE_PAGES[rule]
    if status == "done":
        named = [page for page in written() if f"docs/{page}" in comment]
        assert named, f"{rule}: done, but its comment names no written page ({pages})"
    elif status == "exempt":
        assert comment.strip(), f"{rule}: exempt without a reason"
    else:
        assert status == "todo", status
        assert any(is_pending(page) for page in pages), f"{rule}: todo, but {pages} are written"
