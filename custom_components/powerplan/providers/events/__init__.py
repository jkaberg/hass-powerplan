"""`EventSource` implementations (D1 §3).

v1 ships `entity`: any Home Assistant entity that announces a day type or an
event. Tempo colour, Flex D, CPP/PDP, DFS Saving Sessions, Czech HDO and §14a
dimming are all that shape and are configured, not coded (D1 §2's extension
table); a source that needs its own API client is v1.x and lands as one more
module here.
"""

from .base import EventSource
from .entity import EntityEventSource

__all__ = ["EntityEventSource", "EventSource"]
