"""The config flow's steps, rendering and explanations (D8 §3).

`config_flow.py` holds the handlers and the order they run in; this package holds
what they are made of:

| module | owns |
|---|---|
| `steps.py` | every site step's schema, defaults, validation and derivations |
| `questionnaire.py` | a registry `Field` → a selector (D8 §5.4) |
| `device_pick.py` | the device pick and the meter roles it pre-fills |
| `review.py` | the review step's explanation (INV-67) |

D4's `Question` rendering joins `questionnaire.py` with the load subentry flow in
WP2.4, and the profile matching joins `device_pick.py` there.
"""
