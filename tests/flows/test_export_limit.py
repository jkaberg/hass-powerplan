"""D3 §6, §9 21 - the export limit on the electrical step.

Asked, under Advanced, only of a home whose meter has a production sensor; in
kW on the form, in W in the entry and on the profile; left empty, it is `None`,
which is the fuse. A reconfigure shows the stored watts as the kW they were.
"""

from __future__ import annotations

from typing import Any

from custom_components.powerplan.const import SECTION_ADVANCED
from custom_components.powerplan.flow import steps


def _advanced_keys(schema: Any) -> dict[str, Any]:
    """Return the advanced section's fields by key, with each marker."""
    for key, value in schema.schema.items():
        if str(key) == SECTION_ADVANCED:
            return {str(inner): inner for inner in value.schema.schema}
    raise AssertionError("no advanced section")


def test_21_only_a_home_that_produces_is_asked_the_export_limit() -> None:
    """No production sensor, no question; with one, an empty field under Advanced."""
    without = _advanced_keys(steps.electrical_schema(country="DE"))
    with_pv = _advanced_keys(steps.electrical_schema(country="DE", produces=True))

    assert "export_limit_kw" not in without
    assert "export_limit_kw" in with_pv
    assert with_pv["export_limit_kw"].description == {"suggested_value": None}


def test_21_the_limit_is_kw_on_the_form_and_w_in_the_entry() -> None:
    """6 kW typed: 6 000 W on the profile and in the entry; empty is `None`."""
    answers = {"country": "DE", "main_fuse_a": "35", "export_limit_kw": 6.0}
    profile = steps.electrical_profile(answers)
    data = steps.electrical_data(answers, profile)

    assert profile.export_limit_w == 6000.0
    assert data["export_limit_w"] == 6000.0
    empty = steps.electrical_profile({"country": "DE", "main_fuse_a": "35"})
    assert empty.export_limit_w is None
    assert steps.electrical_data({}, empty)["export_limit_w"] is None


def test_21_a_reconfigure_shows_the_stored_limit_in_kw() -> None:
    """The entry's 6 000 W comes back as 6 kW suggested."""
    stored = {"country": "DE", "main_fuse_a": 35.0, "export_limit_w": 6000.0}
    fields = _advanced_keys(steps.electrical_schema(country="DE", values=stored, produces=True))

    assert fields["export_limit_kw"].description == {"suggested_value": 6.0}
