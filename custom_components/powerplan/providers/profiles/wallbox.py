"""`wallbox` - a Wallbox charger through the core integration (D4 §5.9).

`homeassistant/components/wallbox`. Everything is on the
charger's device, named with `has_entity_name`:

* *Maximum charging current* (`number.py`), in A, 1 A steps, its minimum from
  the charger's part number;
* *Pause/resume* (`switch.py`), on while charging is allowed - unavailable
  while no car is connected, and pausing leaves the status `Paused`;
* *Status description* (`sensor.py`), the text of `ChargerStatus` (`const.py`),
  not an `enum` sensor, so a dump without a platform cannot be recognised;
* **cloud, polled every 90 s** (`UPDATE_INTERVAL`), so the read-back waits 90 s.

`Waiting for car demand` is a car that is not drawing: full, or on its own
schedule. It reads as connected, never done - only an unambiguous word may
latch a finished session.
"""

from __future__ import annotations

from typing import Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import TRANSIENT_GRACE_S, Transport

from .base import Quirks, SessionState, StatusVocabulary
from .registry import register
from .vocabulary import EnableSpec, Find, VocabularyCharger

#: `ChargerStatus`, lower-cased as `StatusVocabulary` reads it (D4 §5.9).
STATUSES: Final = StatusVocabulary(
    states={
        "disconnected": SessionState.DISCONNECTED,
        "ready": SessionState.DISCONNECTED,
        "charging": SessionState.CHARGING,
        "discharging": SessionState.CONNECTED,
        "paused": SessionState.CONNECTED,
        "scheduled": SessionState.CONNECTED,
        "waiting for car demand": SessionState.CONNECTED,
        "waiting": SessionState.CONNECTED,
        "locked, car connected": SessionState.CONNECTED,
        "waiting in queue by power sharing": SessionState.CONNECTED,
        "waiting in queue by power boost": SessionState.CONNECTED,
        "waiting mid failed": SessionState.CONNECTED,
        "waiting mid safety margin exceeded": SessionState.CONNECTED,
        "waiting in queue by eco-smart": SessionState.CONNECTED,
        "locked": SessionState.LINK_DOWN,
        "updating": SessionState.LINK_DOWN,
        "error": SessionState.LINK_DOWN,
        "unknown": SessionState.LINK_DOWN,
    }
)

#: Cloud, polled every 90 s: `verify_after_s = 90` (D4 §5.9).
QUIRKS: Final = Quirks(
    transport=Transport.CLOUD,
    verify_after_s=90.0,
    min_interval_s=0.0,
    tolerance=1.0,
    transient_grace_s=TRANSIENT_GRACE_S,
    poll_interval_s=90.0,
    statuses=STATUSES,
)

PROFILE: Final = register(
    VocabularyCharger(
        key="wallbox",
        platform="wallbox",
        current=Find("number", ("maximum", "charging", "current")),
        enable=EnableSpec(Find("switch", ("pause", "resume"))),
        status=Find("sensor", ("status", "description")),
        statuses=STATUSES,
        quirks_row=QUIRKS,
        reads=((Role.POWER, Find("sensor", ("charging", "power"), "power")),),
    )
)

__all__ = ["PROFILE", "QUIRKS", "STATUSES"]
