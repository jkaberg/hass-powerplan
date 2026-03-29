"""The pure decision core.

Nothing under this package imports `homeassistant` (INV-2, HLD §5). Time,
entity states and knob values arrive in `Inputs`; writes leave as `Effects`.
`tests/core/invariants/test_purity.py` asserts it on every module.
"""
