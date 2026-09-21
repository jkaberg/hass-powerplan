"""`v2c` - a V2C Trydan through the core integration (D4 §5.9).

`homeassistant/components/v2c`:

* *Intensity* (`number.py`), the charge current in A; *Max intensity* and *Min
  intensity* beside it are the charger's own bounds and never written;
* *Pause session* (`switch.py`, key `paused`) is **on while paused**, so this
  profile's ENABLE is inverted: charging allowed is the switch off;
* **no charge-point state sensor**: the integration publishes the plug as
  `binary_sensor` *Connected* (device class `plug`) and charging as a second
  binary sensor. The status is the plug - connected or not - and whether the
  car draws is the power, as for every charger;
* **local, polled every 5 s** (`coordinator.py`, `SCAN_INTERVAL`).
"""

from __future__ import annotations

from typing import Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import TRANSIENT_GRACE_S, Transport

from .base import Quirks, SessionState, StatusVocabulary
from .registry import register
from .vocabulary import EnableSpec, Find, VocabularyCharger

#: The plug sensor's two states (D4 §5.9).
STATUSES: Final = StatusVocabulary(
    states={"off": SessionState.DISCONNECTED, "on": SessionState.CONNECTED}
)

#: Local, polled every 5 s (D4 §5.9).
QUIRKS: Final = Quirks(
    transport=Transport.LOCAL,
    verify_after_s=5.0,
    min_interval_s=0.0,
    tolerance=1.0,
    transient_grace_s=TRANSIENT_GRACE_S,
    poll_interval_s=5.0,
    statuses=STATUSES,
)

PROFILE: Final = register(
    VocabularyCharger(
        key="v2c",
        platform="v2c",
        current=Find("number", ("intensity",), exclude=("max", "min")),
        enable=EnableSpec(Find("switch", ("pause", "session")), on=("off",), off=("on",)),
        status=Find("binary_sensor", ("connected",), "plug"),
        statuses=STATUSES,
        quirks_row=QUIRKS,
        reads=(
            (Role.POWER, Find("sensor", ("charge", "power"), "power")),
            (Role.SESSION_ENERGY, Find("sensor", ("charge", "energy"), "energy")),
        ),
    )
)

__all__ = ["PROFILE", "QUIRKS", "STATUSES"]
