"""Links into the user pages under `docs/` (D14 §3.2, §5.5).

The only place a page name is spelled in Python. A URL is
`{DOCS_URL}/{page}.md#{anchor}`, and an anchor is the key as it stands in code:
a flow step id, a registry key or a repair id. The pages are English only, so
nothing here reads `hass.config.language`.
"""

from __future__ import annotations

from .const import DOCS_URL, SUBENTRY_CIRCUIT, SUBENTRY_GROUP, SUBENTRY_LOAD, SUBENTRY_ZONE

__all__ = ["REVIEW_STEPS", "abort_placeholders", "doc_url", "step_page", "step_placeholders"]

#: The home flow's page.
SETUP = "setup"
#: Circuits, groups and rooms share one page (D14 §3.1).
CIRCUITS = "circuits-groups-rooms"
#: The appliance flow's first steps, before a type is known, and the type index.
APPLIANCES = "appliances/README"

#: The review steps explain in place (INV-67) and carry no link (D14 decision 7).
REVIEW_STEPS = frozenset({"review", "reconfigure_review"})

#: A step that repeats another links the section of the one it repeats (D14 §5.5).
#: On the shared page, a subentry's own first step is anchored by what it adds.
_ANCHORS: dict[tuple[str, str], str] = {
    (SUBENTRY_CIRCUIT, "user"): "circuit",
    (SUBENTRY_CIRCUIT, "reconfigure"): "circuit",
    (SUBENTRY_GROUP, "user"): "group",
    (SUBENTRY_GROUP, "reconfigure"): "group",
    (SUBENTRY_ZONE, "user"): "room",
    (SUBENTRY_ZONE, "reconfigure"): "room",
    (SUBENTRY_LOAD, "reconfigure_device"): "device",
    (SUBENTRY_LOAD, "entities"): "device",
}


def doc_url(page: str, anchor: str | None = None) -> str:
    """Return the URL of a user page, and of one section on it."""
    url = f"{DOCS_URL}/{page}.md"
    return url if anchor is None else f"{url}#{anchor}"


def step_page(flow: str, type_key: str | None = None) -> str:
    """Return the page a flow's steps link: `config`, or a subentry type.

    The appliance flow's steps link the type's own page once the type is chosen,
    and the type index before.
    """
    if flow == SUBENTRY_LOAD:
        return APPLIANCES if type_key is None else f"appliances/{type_key}"
    if flow in (SUBENTRY_CIRCUIT, SUBENTRY_GROUP, SUBENTRY_ZONE):
        return CIRCUITS
    return SETUP


#: The page an abort whose fix lies outside the flow links (D14 §5.4).
TROUBLESHOOTING = "troubleshooting"


def abort_placeholders(reason: str) -> dict[str, str]:
    """Return an abort's `{docs}` placeholder: its entry on the troubleshooting page."""
    return {"docs": doc_url(TROUBLESHOOTING, reason)}


def step_placeholders(flow: str, step_id: str, type_key: str | None = None) -> dict[str, str]:
    """Return a step's `{docs}` placeholder: the URL of that step's own section.

    A review step has none.
    """
    if step_id in REVIEW_STEPS:
        return {}
    anchor = _ANCHORS.get((flow, step_id), step_id)
    return {"docs": doc_url(step_page(flow, type_key), anchor)}
