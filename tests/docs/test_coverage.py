"""D14 §9 3: every key code links to has its section, headed by its en label.

Each family names the page it lives on. A page on `pages_pending.txt` is not
checked until its WP writes it and deletes the line - the same ratchet as the
INV traceability list. The country and grid-source keys of D13 join when their
registries exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan import doclinks
from custom_components.powerplan.core.loads.types import base as device_types
from custom_components.powerplan.core.pricing.modifiers import registry as modifiers
from custom_components.powerplan.core.strategies import base as strategies
from custom_components.powerplan.dashboard.layout import CUSTOM_CARDS, VIEWS
from custom_components.powerplan.events import EVENT_TYPES
from custom_components.powerplan.providers.prices.formats import registry as formats
from custom_components.powerplan.providers.profiles import registry as profiles
from custom_components.powerplan.repairs import CATALOGUE
from tests.docs.pages import PAGES, is_pending, pending, read, strings

if TYPE_CHECKING:
    from collections.abc import Mapping

#: D14 §4 names these beside the entities whose states are translated: their
#: state is a number or a step name, and it needs the words around it.
EXPLAINED_TOO = frozenset({"level", "stage", "active"})


def _selector(key: str) -> Mapping[str, str]:
    options: Mapping[str, str] = strings()["selector"].get(key, {}).get("options", {})
    return options


def _explained_entities() -> dict[str, str]:
    """Every entity whose state or attributes need words: translated states, or D14 §4's list."""
    found: dict[str, str] = {}
    for rows in strings()["entity"].values():
        for key, row in rows.items():
            states = row.get("state", {})
            if set(states) - {"on", "off"} or "state_attributes" in row or key in EXPLAINED_TOO:
                found[key] = row["name"]
    return found


def _steps() -> dict[str, dict[str, str | None]]:
    """Every non-review flow step's anchor and title, by the page it links (doclinks)."""
    found: dict[str, dict[str, str | None]] = {}
    data = strings()
    flows = [("config", data["config"]), *data["config_subentries"].items()]
    for flow, body in flows:
        for step_id, step in body["step"].items():
            placeholders = doclinks.step_placeholders(flow, step_id)
            if not placeholders:
                continue
            page, _, anchor = placeholders["docs"].split("/blob/main/docs/")[1].partition("#")
            if flow == "load" and page != "appliances/README.md":
                continue  # the type's own steps: `_type_steps`
            found.setdefault(page, {})[anchor] = step.get("title")
    return found


def _families() -> list[tuple[str, str, dict[str, str | None]]]:
    """`(family, page, {anchor: en label or None})` for every family §9 3 names."""
    events = strings()["entity"]["event"]["events"]["state_attributes"]["event_type"]["state"]
    services = strings()["services"]
    issues = strings()["issues"]
    dashboard = _selector("dashboard")
    families: list[tuple[str, str, dict[str, str | None]]] = [
        ("strategy", "strategies.md", {k: _selector("strategy").get(k) for k in strategies.keys()}),  # noqa: SIM118 - a registry, not a dict
        (
            "device type",
            "appliances/README.md",
            {k: _selector("load_type").get(k) for k in device_types.keys()},  # noqa: SIM118 - a registry, not a dict
        ),
        (
            "price format",
            "prices.md",
            {k: _selector("price_format").get(k) for k in formats.keys()},  # noqa: SIM118 - a registry, not a dict
        ),
        ("modifier", "prices.md", {k: _selector("modifier").get(k) for k in modifiers.keys()}),  # noqa: SIM118 - a registry, not a dict
        ("profile", "devices.md", dict.fromkeys(profiles.keys())),
        ("repair", "troubleshooting.md", {k: issues[k]["title"] for k in CATALOGUE}),
        ("action", "actions.md", {k: v["name"] for k, v in services.items()}),
        ("event type", "events.md", {k: events[k] for k in EVENT_TYPES}),
        (
            "dashboard view",
            "dashboard.md",
            {k: dashboard.get(f"view_{k}") for k in VIEWS},
        ),
        (
            "custom card",
            "dashboard.md",
            dict.fromkeys(card.removeprefix("custom:") for card in CUSTOM_CARDS),
        ),
        ("entity", "entities.md", dict(_explained_entities())),
    ]
    families += [(f"{page} step", page, anchors) for page, anchors in _steps().items()]
    families += [
        (
            f"{type_key} step",
            f"appliances/{type_key}.md",
            {
                anchor: strings()["config_subentries"]["load"]["step"].get(step_id, {}).get("title")
                for step_id in ("device", "match", "questions", "questions_followup")
                for anchor in [
                    doclinks.step_placeholders("load", step_id, type_key)["docs"].split("#")[1]
                ]
            },
        )
        for type_key in device_types.keys()  # noqa: SIM118 - a registry, not a dict
    ]
    return families


FAMILIES = _families()


@pytest.mark.parametrize(("family", "page", "anchors"), FAMILIES, ids=[f[0] for f in FAMILIES])
def test_03_every_key_has_its_anchor_and_its_label(
    family: str, page: str, anchors: dict[str, str | None]
) -> None:
    """`<a name="<key>">` on its page, and the heading under it equals the key's en label."""
    assert page in PAGES, page
    if is_pending(page):
        pytest.skip(f"{page} is pending")
    found = read(page).anchors()
    missing = sorted(set(anchors) - set(found))
    assert not missing, f"{page}: no anchor for {family} {missing}"
    wrong = {
        key: (label, found[key])
        for key, label in anchors.items()
        if label is not None and found[key] != label
    }
    assert not wrong, f"{page}: heading is not the en label {wrong}"


def test_03_the_pending_list_names_only_user_pages() -> None:
    """A pending line is a page of D14 §3.1, so a typo cannot hide a family."""
    assert pending() <= set(PAGES), sorted(pending() - set(PAGES))
