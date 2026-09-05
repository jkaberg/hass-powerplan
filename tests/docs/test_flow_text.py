"""D14 §9 4: the flow text says little and links its own section (D8 §5.13; D14 decision 7).

Per flow, while the page its steps link is pending, only the rules that hold
already are checked: no string holds a URL, and a `{docs}` placeholder sits
only where `doclinks` supplies one. Once the page is written (DOC.2, DOC.3),
every non-review step description ends with exactly one `{docs}` link, a field
carries at most one link, and the word budgets hold.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from custom_components.powerplan import doclinks
from tests.docs.pages import INTEGRATION, is_pending

LANGUAGES = ("en", "nb")
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
LINK = re.compile(r"\[[^\]]*\]\(\{(docs\w*)\}\)")
ANY_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
DOCS_PLACEHOLDER = re.compile(r"\{(docs\w*)\}")
WORD = re.compile(r"\w[\w'’-]*")

#: A step that needs no explanation, and why (D14 decision 7).
NO_LINK: dict[tuple[str, str], str] = {
    ("config", "name"): "the home's name: nothing to explain",
}

#: D8 §5.13's budgets, placeholders and link text excluded.
STEP_WORDS, STEP_SENTENCES = 30, 2
FIELD_WORDS, FIELD_SENTENCES = 15, 1
OPTION_WORDS = 5


def _document(language: str) -> dict[str, Any]:
    name = "strings.json" if language == "en" else f"translations/{language}.json"
    loaded: dict[str, Any] = json.loads((INTEGRATION / name).read_text("utf-8"))
    return loaded


def _flows(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("config", document["config"]), *document["config_subentries"].items()]


def _leaves(node: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(node, dict):
        return [leaf for key, value in node.items() for leaf in _leaves(value, f"{path}.{key}")]
    return [(path.lstrip("."), node)] if isinstance(node, str) else []


def _plain(text: str) -> str:
    return DOCS_PLACEHOLDER.sub(" ", re.sub(r"\{\w+\}", " ", ANY_LINK.sub(" ", text)))


def _words(text: str) -> int:
    return len(WORD.findall(_plain(text)))


def _sentences(text: str) -> int:
    plain = _plain(text).strip()
    return len([s for s in re.split(r"(?<=[.!?])\s+", plain) if s.strip()]) if plain else 0


def _page(flow: str) -> str:
    return f"{doclinks.step_page(flow)}.md"


@pytest.mark.parametrize("language", LANGUAGES)
def test_04_no_string_holds_a_url(language: str) -> None:
    """Hassfest refuses a URL in a translation string: it arrives as a placeholder."""
    found = [path for path, text in _leaves(_document(language)) if URL.search(text)]
    assert not found, found


@pytest.mark.parametrize("language", LANGUAGES)
def test_04_a_docs_placeholder_is_one_doclinks_supplies(language: str) -> None:
    """`{docs}` only in a non-review step's description; `{docs_<concept>}` only in a field's."""
    wrong: list[str] = []
    for flow, body in _flows(_document(language)):
        for step_id, step in body["step"].items():
            for path, text in _leaves(step):
                for name in DOCS_PLACEHOLDER.findall(text):
                    where = f"{flow}.{step_id}.{path}"
                    if name == "docs":
                        if path != "description" or step_id in doclinks.REVIEW_STEPS:
                            wrong.append(where)
                    elif ".data_description." not in f".{path}.":
                        wrong.append(where)
    for path, text in _leaves(_document(language)):
        outside = not path.startswith(("config.", "config_subentries.", "services."))
        if outside and DOCS_PLACEHOLDER.search(text):
            wrong.append(path)
    assert not wrong, wrong


FLOWS = [flow for flow, _ in _flows(_document("en"))]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("flow", FLOWS)
def test_04_every_step_links_its_section_and_keeps_its_budget(flow: str, language: str) -> None:
    """Exactly one `{docs}` link, last; a field one link at most; D8 §5.13's budgets."""
    if is_pending(_page(flow)):
        pytest.skip(f"{_page(flow)} is pending")
    body = dict(_flows(_document(language)))[flow]
    wrong: list[str] = []
    for step_id, step in body["step"].items():
        where = f"{flow}.step.{step_id}"
        description = step.get("description", "")
        if step_id not in doclinks.REVIEW_STEPS:
            links = LINK.findall(description)
            if (flow, step_id) not in NO_LINK and (
                links != ["docs"] or not description.rstrip(" .").endswith("({docs})")
            ):
                wrong.append(f"{where}: ends with one [...]({{docs}}) link, has {links}")
            if _words(description) > STEP_WORDS or _sentences(description) > STEP_SENTENCES:
                wrong.append(f"{where}: over {STEP_WORDS} words or {STEP_SENTENCES} sentences")
        fields = dict(step.get("data_description", {}))
        for section in step.get("sections", {}).values():
            fields.update(section.get("data_description", {}))
        for field, text in fields.items():
            if len(ANY_LINK.findall(text)) > 1:
                wrong.append(f"{where}.{field}: more than one link")
            if step_id in doclinks.REVIEW_STEPS:
                continue
            if _words(text) > FIELD_WORDS or _sentences(text) > FIELD_SENTENCES:
                wrong.append(f"{where}.{field}: over {FIELD_WORDS} words or one sentence")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("language", LANGUAGES)
def test_04_option_labels_keep_their_budget(language: str) -> None:
    """An option label has at most five words, once every flow's page is written."""
    if any(is_pending(_page(flow)) for flow in FLOWS):
        pytest.skip("a flow's page is pending")
    long = [
        f"{key}.{option}"
        for key, selector in _document(language)["selector"].items()
        for option, label in selector.get("options", {}).items()
        if _words(label) > OPTION_WORDS
    ]
    assert not long, long
