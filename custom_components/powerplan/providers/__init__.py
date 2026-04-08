"""HA-facing adapters. Thin (HLD §5).

A provider is the only place a raw entity state, or a response action's payload,
becomes a number the pure core trusts. It reads `hass.states` and performs no
device write: invoking an action belongs to `writegate.py`, with the one
documented exception of the read-only response action in
`prices/nordpool_action.py` (INV-3, `design/DECISIONS.md` D-0080).

Validation lives here, at the boundary: units are scaled from the unit the
entity declares, an entity that cannot answer degrades to `Quality.UNAVAILABLE`
rather than raising (INV-53), and a payload that no longer parses fails its
source instead of reaching the core (D1 §8). Inside `core/` the types are
trusted.
"""
