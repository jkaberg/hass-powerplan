"""`goecharger_api2` - a go-e charger through the HACS integration (D4 §5.9).

marq24/ha-goecharger-api2, read at 2026.9.5 (`const.py`, `translations/en.json`,
`pygoecharger_ha/const.py`). The API v2 keys are the entities:

* `amp`, *Requested current* (`number`), 6–32 A - it cannot hold the car at 0 A;
* `frc`, *Force state* (`select`, options `0`, `1`, `2`): `2` charges, `1` does
  not, `0` is neutral - the charger's own logic, which charges a connected car;
* `car`, *Car state [CODE]* (`sensor`): the car state as its API code. The
  integration's *Car state* beside it is the same code in Home Assistant's
  language (`Idle`, `Inaktiv/Frei`, …), so the code is the vocabulary. The code
  sensor ships **disabled**; the match names it until it reports;
* `nrg` index 11, *Power total now* (`sensor`), in W;
* local (HTTP API v2 or websocket).
"""

from __future__ import annotations

from typing import Final

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.gate import TRANSIENT_GRACE_S, Transport

from .base import Quirks, SessionState, StatusVocabulary
from .registry import register
from .vocabulary import EnableSpec, Find, VocabularyCharger

#: The API's `car` codes: Unknown/Error, Idle, Charging, WaitCar, Complete, Error.
STATUSES: Final = StatusVocabulary(
    states={
        "0": SessionState.LINK_DOWN,
        "1": SessionState.DISCONNECTED,
        "2": SessionState.CHARGING,
        "3": SessionState.CONNECTED,
        "4": SessionState.DONE,
        "5": SessionState.LINK_DOWN,
    }
)

#: Local (D4 §5.9).
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
        key="goecharger_api2",
        platform="goecharger_api2",
        current=Find("number", ("requested", "current")),
        enable=EnableSpec(Find("select", ("force", "state")), on=("2", "0"), off=("1",)),
        status=Find("sensor", ("car", "state", "code")),
        statuses=STATUSES,
        quirks_row=QUIRKS,
        reads=((Role.POWER, Find("sensor", ("power", "total", "now"), "power")),),
        notes=(
            (
                "the Car state [CODE] sensor ships disabled — enable it, it is how "
                "powerplan knows whether a car is connected"
            ),
        ),
    )
)

__all__ = ["PROFILE", "QUIRKS", "STATUSES"]
