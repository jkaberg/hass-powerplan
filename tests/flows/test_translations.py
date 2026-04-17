"""`strings.json` ≡ `en.json` ≡ `nb.json`, and every step the flow shows (D8 §5.11).

Two halves. The structural half compares the three files key by key: a step added
to `strings.json` and forgotten in `nb.json` is a user who sees an English label
in a Norwegian UI, which hassfest does not catch.

The coverage half **walks the flow submitting nothing but the defaults** and
checks the strings for every step it is shown. That walk is also the strongest
statement of HLD §7.9 (2) available: a step whose answers do not all have
defaults makes `data_schema({})` raise, and the walk stops there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, section

from custom_components.powerplan.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "powerplan"
STRINGS = INTEGRATION / "strings.json"
TRANSLATIONS = INTEGRATION / "translations"

#: Steps that only route the menu choice on: they never render a form.
ROUTING_STEPS = frozenset({"full", "price_only", "fuse_only"})

#: Titles that are the same in both languages **by construction**: a proper noun,
#: and two titles that are nothing but a placeholder the flow fills with the name
#: of the modifier or carrier being asked about.
SAME_TITLE_BY_DESIGN = frozenset({"prices_nordpool", "modifier_options", "carrier_options"})


def _load(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _shape(node: Any, prefix: str = "") -> set[str]:
    """Return every leaf path in a translation document."""
    if isinstance(node, dict):
        return {path for key, value in node.items() for path in _shape(value, f"{prefix}.{key}")}
    return {prefix}


def test_the_three_documents_are_structurally_identical() -> None:
    """A key in one file is a key in all three (D8 §5.11)."""
    strings = _shape(_load(STRINGS))
    english = _shape(_load(TRANSLATIONS / "en.json"))
    norwegian = _shape(_load(TRANSLATIONS / "nb.json"))

    assert strings - english == set(), f"missing from en.json: {sorted(strings - english)}"
    assert english - strings == set(), f"not in strings.json: {sorted(english - strings)}"
    assert strings - norwegian == set(), f"missing from nb.json: {sorted(strings - norwegian)}"
    assert norwegian - strings == set(), f"not in strings.json: {sorted(norwegian - strings)}"


def test_norwegian_is_translated_not_copied() -> None:
    """`nb` ships with `en` and is real bokmål, not the English text again."""
    english = _load(TRANSLATIONS / "en.json")["config"]["step"]
    norwegian = _load(TRANSLATIONS / "nb.json")["config"]["step"]

    same = [
        step
        for step, body in english.items()
        if step not in SAME_TITLE_BY_DESIGN
        and "title" in body
        and body["title"] == norwegian[step].get("title")
    ]
    assert not same, f"these step titles were never translated: {same}"


def _labelled(body: dict[str, Any], schema: Any, step_id: str) -> None:
    """Assert every field of `schema` has a label and a description in `body`."""
    keys = {str(key) for key in schema.schema}
    sections = {str(key) for key, value in schema.schema.items() if isinstance(value, section)}
    data = set(body.get("data", {}))
    described = set(body.get("data_description", {}))
    assert keys - sections <= data, (
        f"step {step_id} fields without a label: {sorted(keys - sections - data)}"
    )
    assert keys - sections <= described, (
        f"step {step_id} fields without a data_description: {sorted(keys - sections - described)}"
    )
    for key, value in schema.schema.items():
        if not isinstance(value, section):
            continue
        block = body.get("sections", {}).get(str(key))
        assert block is not None, f"step {step_id} has no strings for section {key}"
        assert "name" in block, f"section {key} of {step_id} has no name"
        _labelled(block, value.schema, f"{step_id}.{key}")


@pytest.mark.inv("INV-65")
async def test_the_flow_walks_on_defaults_and_every_step_is_translated(
    hass: HomeAssistant,
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
) -> None:
    """Submit only defaults from `user` to `review`, checking strings as we go."""
    hass.config.time_zone = "Europe/Oslo"
    hass.config.currency = "NOK"
    hass.config.country = "NO"
    steps = _load(STRINGS)["config"]["step"]

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == set(steps["user"]["menu_options"])

    user_input: dict[str, Any] | None = {"next_step_id": "full"}
    seen: list[str] = ["user"]
    for _ in range(40):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
        if result["type"] is FlowResultType.CREATE_ENTRY:
            break
        step_id = result["step_id"]
        seen.append(step_id)
        body = steps.get(step_id)
        assert body is not None, f"step {step_id} has no strings"
        assert "title" in body, f"step {step_id} has no title"

        schema = result["data_schema"]
        assert schema is not None, f"step {step_id} rendered no schema"
        _labelled(body, schema, step_id)
        # HLD §7.9 (2): every answer has a default, so this never raises - and the
        # advanced section's own defaults come back with it (INV-65).
        user_input = schema({})
    else:  # pragma: no cover - the flow does not terminate
        pytest.fail(f"the flow never finished; steps seen: {seen}")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not ROUTING_STEPS & set(seen)
    assert "review" in seen
