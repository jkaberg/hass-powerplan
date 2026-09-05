"""The user pages as the tests read them (D14 §3.1, §5.5, §9).

What GitHub renders, parsed offline: a page's `<a name>` anchors and heading
slugs, the heading under each anchor, its links, its alerts and its
`<details>` folds. `pages_pending.txt` names the pages no WP has written yet
(the ratchet of §9 3): a check that needs a page skips it while it is pending.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
INTEGRATION = REPO_ROOT / "custom_components" / "powerplan"
PENDING_FILE = Path(__file__).with_name("pages_pending.txt")

#: D14 §3.1: every user page, by path under `docs/`.
APPLIANCE_PAGES = tuple(
    f"appliances/{key}.md"
    for key in (
        "appliance_cycle",
        "battery",
        "ev",
        "floor_heating",
        "generic_switch",
        "heat_pump",
        "radiator",
        "water_heater",
    )
)
PAGES: tuple[str, ...] = (
    "README.md",
    "install.md",
    "get-started.md",
    "setup.md",
    "appliances/README.md",
    *APPLIANCE_PAGES,
    "circuits-groups-rooms.md",
    "strategies.md",
    "devices.md",
    "tariffs.md",
    "prices.md",
    "entities.md",
    "actions.md",
    "events.md",
    "dashboard.md",
    "daily-use.md",
    "examples.md",
    "how-it-works.md",
    "capacity-tariffs.md",
    "savings.md",
    "troubleshooting.md",
    "limitations.md",
    "glossary.md",
)
KINDS = frozenset({"index", "start", "guide", "reference", "understand", "help"})

ANCHOR = re.compile(r'^<a name="([^"]+)"></a>\s*$')
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^(```|~~~)")
ALERT = re.compile(r"^>\s*\[!(\w+)\]\s*$")


@cache
def pending() -> frozenset[str]:
    """Return the pages no WP has written yet (`pages_pending.txt`, one per line)."""
    lines = PENDING_FILE.read_text("utf-8").splitlines()
    return frozenset(line.split("#")[0].strip() for line in lines if line.split("#")[0].strip())


def is_pending(page: str) -> bool:
    """Say whether `page` (a path under `docs/`) is still to be written."""
    return page in pending()


def written() -> tuple[str, ...]:
    """Return the pages a WP has written: every page of §3.1 that is not pending."""
    return tuple(page for page in PAGES if not is_pending(page))


def slug(heading: str) -> str:
    """Return GitHub's id for a heading: lowercase, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"<[^>]+>", "", heading)
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", text)
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


@dataclass(frozen=True)
class Page:
    """One parsed page."""

    path: str
    text: str

    @property
    def lines(self) -> list[str]:
        """Return the page's lines."""
        return self.text.splitlines()

    def prose(self) -> list[tuple[int, str]]:
        """Return the lines outside fenced code blocks, numbered from 1."""
        out: list[tuple[int, str]] = []
        fenced = False
        for number, line in enumerate(self.lines, 1):
            if FENCE.match(line.strip()):
                fenced = not fenced
                continue
            if not fenced:
                out.append((number, line))
        return out

    def headings(self) -> list[tuple[int, int, str]]:
        """Return `(line, level, text)` for every heading outside code."""
        return [
            (number, len(m.group(1)), m.group(2))
            for number, line in self.prose()
            if (m := HEADING.match(line))
        ]

    def anchors(self) -> dict[str, str | None]:
        """Return every `<a name>` and the heading under it (`None` when none follows)."""
        found: dict[str, str | None] = {}
        prose = self.prose()
        for index, (_, line) in enumerate(prose):
            if m := ANCHOR.match(line):
                following = next((text for _, text in prose[index + 1 :] if text.strip()), "")
                heading = HEADING.match(following)
                found[m.group(1)] = heading.group(2) if heading else None
        return found

    def ids(self) -> set[str]:
        """Return every id a URL fragment can land on: anchors and heading slugs."""
        seen: dict[str, int] = {}
        ids = set(self.anchors())
        for _, _, text in self.headings():
            base = slug(text)
            count = seen.get(base, 0)
            ids.add(base if count == 0 else f"{base}-{count}")
            seen[base] = count + 1
        return ids

    def links(self) -> list[tuple[int, bool, str, str]]:
        """Return `(line, is_image, text, target)` for every link outside code."""
        return [
            (number, bool(m.group(1)), m.group(2), m.group(3))
            for number, line in self.prose()
            for m in LINK.finditer(re.sub(r"`[^`]*`", "", line))
        ]


def read(page: str) -> Page:
    """Return one user page, by its path under `docs/`."""
    return Page(page, (DOCS / page).read_text("utf-8"))


def all_markdown() -> list[str]:
    """Return every markdown file under `docs/`, as paths under it."""
    return sorted(str(p.relative_to(DOCS)) for p in DOCS.rglob("*.md"))


@cache
def strings() -> dict[str, Any]:
    """Return `strings.json`: the en labels a heading must equal."""
    loaded: dict[str, Any] = json.loads((INTEGRATION / "strings.json").read_text("utf-8"))
    return loaded
