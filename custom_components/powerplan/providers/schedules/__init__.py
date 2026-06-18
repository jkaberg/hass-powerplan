"""Reads a bound `schedule.*` helper's weekly windows (D4 §4.4).

v1 ships one source: `ha_schedule`, HA's own `schedule` integration, read
through its `get_schedule` action (D-0300). A future source - Google Calendar's
recurring events, say - is one more module here, not a change to this one.
"""

from .ha_schedule import fetch_windows

__all__ = ["fetch_windows"]
