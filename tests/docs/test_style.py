"""D14 §9 6, 7, 8: the pages' words, frame, alerts and images; the root README for HACS.

The design's words are the list D8 §9 18d already uses (one source), matched
outside code spans. GitHub renders the rest: an alert inside a list, a table or
`<details>` shows as a plain quote, and a link cannot open a fold.
"""

from __future__ import annotations

import re

import pytest

from tests.docs.pages import ALERT, DOCS, KINDS, REPO_ROOT, Page, read, written
from tests.flows.test_text import DESIGN_WORDS

ALERTS = frozenset({"NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"})
#: S7: what the design is built of never shows on a user page.
BUILT = re.compile(
    r"\bINV-\d|\bD-\d{4}\b|\bWP\s?\d|\b(?:custom_components|core|providers|tests|tools)/\w"
)
#: S4: HA's style guide.
HOUSE_STYLE = re.compile(r"\be\.g\.|\bi\.e\.|\bclick", re.IGNORECASE)
BRITISH = re.compile(
    r"\b\w*(?:colour|behaviour|favour|honour|programme|litre|metre|centre|catalogue|"
    r"organis|recognis|optimis|minimis|maximis|prioritis|summaris|analyse)\w*\b",
    re.IGNORECASE,
)
CODE = re.compile(r"`[^`]*`")
URL = re.compile(r"\]\([^)]*\)|https?://\S+|<[^>]+>")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*`\[])")
BREADCRUMB = re.compile(
    r"^\[PowerPlan docs\]\((?:\.\./)?README\.md\)( \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} .+)?$"
)
MAX_IMAGE = 250 * 1024


def _plain(line: str) -> str:
    return URL.sub(" ", CODE.sub(" ", line))


@pytest.mark.parametrize("page", written())
def test_06_the_household_words(page: str) -> None:
    """No design word outside code (S2), nothing of how it is built (S7), HA's style (S4)."""
    wrong: list[str] = []
    for number, line in read(page).prose():
        if line.startswith("<!--"):
            continue
        plain = _plain(line)
        for pattern in (DESIGN_WORDS["en"], BUILT, HOUSE_STYLE, BRITISH):
            wrong += [f"{page}:{number}: {m.group(0)!r}" for m in pattern.finditer(plain)]
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("page", written())
def test_06_alerts_and_folds(page: str) -> None:
    """Five alert types, three a page, never consecutive or nested; no anchor in a fold."""
    parsed = read(page)
    wrong: list[str] = []
    alerts: list[int] = []
    folded = False
    prose = parsed.prose()
    for index, (number, line) in enumerate(prose):
        stripped = line.strip()
        if stripped.startswith("<details"):
            folded = True
        if stripped.startswith("</details>"):
            folded = False
        if folded and stripped.startswith("<a name="):
            wrong.append(f"{page}:{number}: an anchor inside <details>")
        match = ALERT.match(stripped)
        if not match and re.match(r"^\s*(?:[-*]|\d+\.|\|).*>\s*\[!\w+\]", line):
            wrong.append(f"{page}:{number}: an alert inside a list or table")
        if not match:
            continue
        if match.group(1) not in ALERTS:
            wrong.append(f"{page}:{number}: alert {match.group(1)}")
        if folded:
            wrong.append(f"{page}:{number}: an alert inside <details>")
        if line != stripped:
            wrong.append(f"{page}:{number}: an alert indented into a list")
        before = next((t for _, t in reversed(prose[:index]) if t.strip()), "")
        if before.startswith(">"):
            wrong.append(f"{page}:{number}: two alerts in a row")
        alerts.append(number)
    if len(alerts) > 3:
        wrong.append(f"{page}: {len(alerts)} alerts, at most 3")
    assert not wrong, "\n".join(wrong)


def _lede(parsed: Page) -> str:
    lines = parsed.lines
    title = next(i for i, line in enumerate(lines) if line.startswith("# "))
    paragraph: list[str] = []
    for line in lines[title + 1 :]:
        if not line.strip():
            if paragraph:
                break
            continue
        paragraph.append(line)
    return " ".join(paragraph)


@pytest.mark.parametrize("page", written())
def test_06_the_frame(page: str) -> None:
    """Line 1 the kind, line 2 the breadcrumb, one title, a lede of two sentences; no front matter."""
    parsed = read(page)
    lines = parsed.lines
    assert lines[0] != "---", f"{page}: YAML front matter"
    kind = re.fullmatch(r"<!-- kind: (\w+) -->", lines[0])
    assert kind, f"{page}: line 1 is {lines[0]!r}"
    assert kind.group(1) in KINDS, f"{page}: line 1 is {lines[0]!r}"
    if page != "README.md":
        assert BREADCRUMB.match(lines[1]), f"{page}: line 2 is {lines[1]!r}"
    titles = [text for _, level, text in parsed.headings() if level == 1]
    assert len(titles) == 1, f"{page}: {titles}"
    lede = _plain(_lede(parsed))
    assert len([s for s in SENTENCE.split(lede) if s.strip()]) <= 2, f"{page}: lede {lede!r}"
    footer = next(line for line in reversed(lines) if line.strip())
    assert footer.startswith(("**Next:**", "**See also:**")), f"{page}: ends {footer!r}"


def test_07_the_root_readme_reads_on_hacs() -> None:
    """Absolute links only; no `<picture>`, alert, mermaid or `<details>` (§5.8)."""
    parsed = Page("README.md", (REPO_ROOT / "README.md").read_text("utf-8"))
    wrong = [
        f"README.md:{number}: {target}"
        for number, _, _, target in parsed.links()
        if not target.startswith("https://")
    ]
    for pattern in (r"<picture", r"^>\s*\[!", r"```mermaid", r"<details"):
        wrong += [f"README.md: {pattern}" for _ in re.finditer(pattern, parsed.text, re.MULTILINE)]
    assert not wrong, "\n".join(wrong)


def _images() -> dict[str, list[str]]:
    """Every image the pages and the root README use, by path under `docs/`."""
    used: dict[str, list[str]] = {}
    root = "https://raw.githubusercontent.com/jkaberg/hass-powerplan/main/docs/"
    sources = [(page, read(page)) for page in written()]
    sources.append(
        ("../README.md", Page("../README.md", (REPO_ROOT / "README.md").read_text("utf-8")))
    )
    for page, parsed in sources:
        for _, is_image, alt, target in parsed.links():
            if not is_image:
                continue
            if target.startswith(root):
                path = target.removeprefix(root)
            elif target.startswith("https://"):
                continue
            else:
                path = str(
                    (DOCS / page).parent.joinpath(target).resolve().relative_to(DOCS.resolve())
                )
            used.setdefault(path, []).append(alt)
    return used


def test_08_images() -> None:
    """Every image used exists, has alt text and is at most 250 KB; every file is used."""
    used = _images()
    wrong = [f"{path}: missing" for path in used if not (DOCS / path).is_file()]
    wrong += [
        f"{path}: no alt text" for path, alts in used.items() if not all(a.strip() for a in alts)
    ]
    files = {str(p.relative_to(DOCS)) for p in (DOCS / "images").rglob("*") if p.is_file()}
    wrong += [f"{path}: not used by any page" for path in sorted(files - set(used))]
    wrong += [
        f"{path}: {(DOCS / path).stat().st_size} bytes"
        for path in files
        if (DOCS / path).stat().st_size > MAX_IMAGE
    ]
    assert not wrong, "\n".join(wrong)
