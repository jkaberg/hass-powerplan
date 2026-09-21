"""`ocpp` - any charger speaking OCPP 1.6 through the HACS `ocpp` integration (D4 §5.9).

lbbrhzn/ocpp, read at v0.12.0. One profile for every brand the long tail
speaks OCPP through - ABB, Alfen, CTEK, Wallbox, EVBox, Vestel, Etrel, Autel.
What the source says, for a charger with one connector (all on the charge
point's device, `<cpid>`):

* **The limit is station-wide.** `number.<cpid>_maximum_current` (0 A to the
  charger's maximum, 1 A steps) sends a `ChargePointMaxProfile`, which holds with
  or without a transaction (`number.py`, `set_max_charge_rate_amps`). So a limit
  written before plug-in is not lost, and nothing is re-sent on the plug-in edge -
  the design's worry was the per-transaction `TxProfile`, the integration's
  separate *Session Current Limit*. A refused limit raises, so the gate sees it.
* **The limit is the switch.** *Charge Control* sends `RemoteStopTransaction`:
  the session ends and the connector goes `Finishing`, which this vocabulary
  reads as done. It is not bound; 0 A holds the car, as for Zaptec (D-0372).
* **The status** is `sensor.<cpid>_status_connector`, OCPP 1.6's
  `ChargePointStatus` as the charger sends it (`enums.py`, `sensor.py`).
* **Local and pushed**: the charger connects to Home Assistant's websocket.
"""

from __future__ import annotations

from typing import Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import TRANSIENT_GRACE_S, Transport

from .base import Quirks, SessionState, StatusVocabulary
from .registry import register
from .vocabulary import Find, VocabularyCharger

#: OCPP 1.6 `ChargePointStatus`, onto D4 §5.11's session states (D4 §5.9).
STATUSES: Final = StatusVocabulary(
    states={
        "available": SessionState.DISCONNECTED,
        "reserved": SessionState.DISCONNECTED,
        "preparing": SessionState.CONNECTED,
        "suspendedevse": SessionState.CONNECTED,
        "suspendedev": SessionState.CONNECTED,
        "charging": SessionState.CHARGING,
        "finishing": SessionState.DONE,
        "unavailable": SessionState.LINK_DOWN,
        "faulted": SessionState.LINK_DOWN,
    }
)

#: Local and pushed: the number reports the accepted limit as soon as the charger
#: answers, so the kind's own settle window binds.
QUIRKS: Final = Quirks(
    transport=Transport.LOCAL,
    verify_after_s=5.0,
    min_interval_s=0.0,
    tolerance=1.0,
    transient_grace_s=TRANSIENT_GRACE_S,
    statuses=STATUSES,
)

PROFILE: Final = register(
    VocabularyCharger(
        key="ocpp",
        platform="ocpp",
        current=Find("number", ("maximum", "current")),
        status=Find("sensor", ("status", "connector")),
        statuses=STATUSES,
        quirks_row=QUIRKS,
        reads=(
            (Role.POWER, Find("sensor", ("power", "active", "import"), "power")),
            (Role.ENERGY, Find("sensor", ("energy", "active", "import", "register"), "energy")),
            (Role.SESSION_ENERGY, Find("sensor", ("energy", "session"), "energy")),
        ),
        notes=(
            (
                "a charger with several connectors has one device per connector; "
                "powerplan steers the charge point's own maximum current (v1)"
            ),
        ),
    )
)

__all__ = ["PROFILE", "QUIRKS", "STATUSES"]
