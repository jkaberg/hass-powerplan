"""D14 §9 1, 2, 9: every link into the pages lands on a section, and every link out of them resolves.

§9 1 collects the URLs the integration builds - the flows' `{docs}` over every
step `strings.json` declares, every repair's **Learn more**, the manifest's help
icon and the frontend's constants - and resolves each offline to a file under
`docs/` and an id on it. A URL into a page that is still pending is skipped
until its WP writes it (`pages_pending.txt`).
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import pytest

from custom_components.powerplan import doclinks
from custom_components.powerplan.const import DOCS_URL, SUBENTRY_LOAD
from custom_components.powerplan.core.loads.types import base as device_types
from custom_components.powerplan.repairs import CATALOGUE, learn_more_url
from tests.docs.pages import (
    DOCS,
    INTEGRATION,
    PAGES,
    REPO_ROOT,
    all_markdown,
    is_pending,
    read,
    strings,
    written,
)

FRONTEND = REPO_ROOT / "frontend" / "src"
#: D14 §5.3: the My Home Assistant redirects a page may use.
MY_REDIRECTS = frozenset(
    {
        "integration",
        "config_flow_start",
        "hacs_repository",
        "repairs",
        "config_energy",
        "lovelace_dashboards",
        "logs",
        "system_health",
    }
)
#: The appliance flow's steps once a type is chosen: they link the type's own page.
TYPE_STEPS = ("device", "match", "questions", "questions_followup", "reconfigure_device")


def _step_urls() -> list[tuple[str, str]]:
    """Return `(where, url)` for every step of every flow `strings.json` declares."""
    found: list[tuple[str, str]] = []
    data = strings()
    for step_id in data["config"]["step"]:
        found += [
            (f"config.step.{step_id}", url)
            for url in doclinks.step_placeholders("config", step_id).values()
        ]
    for flow, body in data["config_subentries"].items():
        for step_id in body["step"]:
            if flow == SUBENTRY_LOAD and step_id in TYPE_STEPS:
                for type_key in device_types.keys():  # noqa: SIM118 - a registry, not a dict
                    found += [
                        (f"{flow}.{type_key}.{step_id}", url)
                        for url in doclinks.step_placeholders(flow, step_id, type_key).values()
                    ]
                continue
            found += [
                (f"config_subentries.{flow}.step.{step_id}", url)
                for url in doclinks.step_placeholders(flow, step_id).values()
            ]
    return found


def _built_urls() -> list[tuple[str, str]]:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text("utf-8"))
    found = [("manifest.documentation", manifest["documentation"])]
    found += [(f"repair {key}", learn_more_url(key)) for key in CATALOGUE]
    found += _step_urls()
    for path in sorted(FRONTEND.rglob("*.ts")):
        for url in re.findall(
            r"https://github\.com/jkaberg/hass-powerplan/blob/main/docs/[^\"'`\s)]+",
            path.read_text("utf-8"),
        ):
            found.append((str(path.relative_to(REPO_ROOT)), url))
    return found


def _resolve(url: str) -> tuple[str, str | None]:
    """Return `(page, fragment)` for a URL under `DOCS_URL`."""
    assert url.startswith(f"{DOCS_URL}/"), url
    parts = urlsplit(url)
    page = unquote(parts.path.split("/blob/main/docs/", 1)[1])
    return page, parts.fragment or None


def test_01_every_url_the_integration_builds_resolves() -> None:
    """Flows, repairs, the manifest and the frontend: a page, and an id on it."""
    unresolved: list[str] = []
    for where, url in _built_urls():
        page, fragment = _resolve(url)
        if page not in PAGES:
            unresolved.append(f"{where}: {page} is not a user page")
            continue
        if is_pending(page):
            continue
        target = DOCS / page
        if not target.is_file():
            unresolved.append(f"{where}: {page} does not exist")
        elif fragment is not None and fragment not in read(page).ids():
            unresolved.append(f"{where}: {page} has no #{fragment}")
    assert not unresolved, "\n".join(unresolved)


def test_01_doc_url_is_built_over_the_one_constant() -> None:
    """A URL is `{DOCS_URL}/{page}.md#{anchor}`, and a review step has no link (§5.5)."""
    assert doclinks.doc_url("actions", "boost") == f"{DOCS_URL}/actions.md#boost"
    assert doclinks.step_placeholders("zone", "reconfigure") == {
        "docs": f"{DOCS_URL}/circuits-groups-rooms.md#room"
    }
    assert doclinks.step_placeholders("load", "questions", "ev") == {
        "docs": f"{DOCS_URL}/appliances/ev.md#questions"
    }
    assert doclinks.step_placeholders("config", "review") == {}


def _relative(page: str, target: str) -> tuple[str, str | None]:
    path, _, fragment = target.partition("#")
    joined = PurePosixPath(page).parent / path if path else PurePosixPath(page)
    parts: list[str] = []
    for part in joined.parts:
        if part == "..":
            if parts:
                parts.pop()
            else:
                parts.append("..")
        elif part != ".":
            parts.append(part)
    return "/".join(parts), fragment or None


@pytest.mark.parametrize("page", written())
def test_02_every_relative_link_resolves(page: str) -> None:
    """A link between pages lands on a file and an id; none reaches into `design/`."""
    broken: list[str] = []
    for line, _, _, target in read(page).links():
        if re.match(r"^[a-z]+:", target):
            continue
        resolved, fragment = _relative(page, target)
        if resolved.startswith("../design") and page != "README.md":
            broken.append(f"{page}:{line}: links into design/ ({target})")
            continue
        if resolved.startswith(".."):
            if not (DOCS / page).parent.joinpath(target.partition("#")[0]).resolve().exists():
                broken.append(f"{page}:{line}: {target} does not exist")
            continue
        if not (DOCS / resolved).exists():
            broken.append(f"{page}:{line}: {target} does not exist")
        elif (
            fragment is not None
            and resolved.endswith(".md")
            and fragment not in read(resolved).ids()
        ):
            broken.append(f"{page}:{line}: {resolved} has no #{fragment}")
    assert not broken, "\n".join(broken)


def test_02_every_written_page_is_reachable_from_the_index() -> None:
    """Every page a WP has written is linked from `docs/README.md`, or from a page it links."""
    seen = {"README.md"}
    queue = ["README.md"]
    while queue:
        page = queue.pop()
        for _, _, _, target in read(page).links():
            if re.match(r"^[a-z]+:", target):
                continue
            resolved, _ = _relative(page, target)
            if resolved in PAGES and resolved not in seen and (DOCS / resolved).is_file():
                seen.add(resolved)
                queue.append(resolved)
    assert not set(written()) - seen, sorted(set(written()) - seen)


def test_02_every_markdown_file_under_docs_is_a_user_page() -> None:
    """`docs/` is the household's folder: nothing but §3.1's pages (D14 decision 1)."""
    assert not set(all_markdown()) - set(PAGES), sorted(set(all_markdown()) - set(PAGES))


@pytest.mark.parametrize("page", written())
def test_09_every_my_link_names_a_listed_redirect(page: str) -> None:
    """A My Home Assistant link uses one of §5.3's redirects."""
    for line, _, _, target in read(page).links():
        if target.startswith("https://my.home-assistant.io/redirect/"):
            redirect = target.removeprefix("https://my.home-assistant.io/redirect/").split("/")[0]
            assert redirect in MY_REDIRECTS, f"{page}:{line}: {target}"
