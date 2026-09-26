"""D4 §9 48 - a mode floor's comfort ↔ shed reversal waits out its dwell (D-0691).

The reference house's bathrooms went heat → eco → heat in 10 min 5 s and 10 min 6 s
against 900 s configured: the `MODE` kind had no dwell at all. It takes the load's
`min_on_s`/`min_off_s` now, and the gate's row 7 holds a reversal inside them unless
the command is urgent or blunt.
"""

from __future__ import annotations

from custom_components.powerplan.core.loads.gate import config_for
from tests.core.loads.conftest import FLOOR_PARAMS, floor_load


def test_48_a_mode_floor_carries_its_answers_dwell_to_the_gate() -> None:
    """The answers' 900 s reach the kind and the gate's config."""
    floor = floor_load(params={**_mode_params(), "min_on_s": 900.0, "min_off_s": 900.0})

    assert floor.kind.key == "mode"
    assert floor.kind.dwell_s() == (900.0, 900.0)
    assert config_for(floor.kind).min_off_s == 900.0


def test_48_the_default_dwell_is_900_s_each_way() -> None:
    """No answer: D4 §6.1's default, 900 s each way."""
    floor = floor_load(params=_mode_params())

    assert floor.kind.dwell_s() == (900.0, 900.0)


def _mode_params() -> dict[str, object]:
    return {**FLOOR_PARAMS, "kind": "mode"}
