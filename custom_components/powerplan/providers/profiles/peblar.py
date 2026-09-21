"""`peblar` - a Peblar charger through the core integration (D4 §5.9).

`homeassistant/components/peblar`:

* *Charge limit* (`number.py`), in A, from **6 A** to the lower of the hardware
  and the installation limit - it cannot hold the car at 0 A;
* *Charge* (`switch.py`, a configuration switch), on while the limit is 6 A or
  more; off sets the limit to 0 and on restores the last one;
* *State* (`sensor.py`, `cp_state`), an `enum` sensor with six options
  (`const.py`, `PEBLAR_CP_STATE_TO_HOME_ASSISTANT`); the charger's own unknown is
  Home Assistant's unknown, which the vocabulary reads as a lost link;
* **local, polled every 10 s** (`coordinator.py`).

*Power* (total) is one of four power sensors whose names are subsets of each
other's, so it is found by excluding the three phases.
"""

from __future__ import annotations

from typing import Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import TRANSIENT_GRACE_S, Transport

from .base import Quirks, SessionState, StatusVocabulary
from .registry import register
from .vocabulary import EnableSpec, Find, VocabularyCharger

#: `cp_state`'s options, onto D4 §5.11's session states (D4 §5.9).
STATUSES: Final = StatusVocabulary(
    states={
        "no_ev_connected": SessionState.DISCONNECTED,
        "suspended": SessionState.CONNECTED,
        "charging": SessionState.CHARGING,
        "error": SessionState.LINK_DOWN,
        "fault": SessionState.LINK_DOWN,
        "invalid": SessionState.LINK_DOWN,
    }
)

#: Local, polled every 10 s (D4 §5.9).
QUIRKS: Final = Quirks(
    transport=Transport.LOCAL,
    verify_after_s=10.0,
    min_interval_s=0.0,
    tolerance=1.0,
    transient_grace_s=TRANSIENT_GRACE_S,
    poll_interval_s=10.0,
    statuses=STATUSES,
)

PROFILE: Final = register(
    VocabularyCharger(
        key="peblar",
        platform="peblar",
        current=Find("number", ("charge", "limit")),
        enable=EnableSpec(Find("switch", ("charge",), exclude=("single", "socket"))),
        status=Find("sensor", ("state",), exclude=("limit", "source")),
        statuses=STATUSES,
        quirks_row=QUIRKS,
        status_has_options=True,
        reads=(
            (Role.POWER, Find("sensor", ("power",), "power", exclude=("phase",))),
            (Role.SESSION_ENERGY, Find("sensor", ("session", "energy"), "energy")),
            (Role.ENERGY, Find("sensor", ("lifetime", "energy"), "energy")),
        ),
    )
)

__all__ = ["PROFILE", "QUIRKS", "STATUSES"]
