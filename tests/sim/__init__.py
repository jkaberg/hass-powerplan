"""Physics simulators for the test suite (D9 §2, §3).

A static mock would have passed the broken heat-pump driver this design exists
to prevent (D9 §11), so a physical thing in a powerplan test is a simulator
with quirks: a slab that cools, a tank that stratifies, an EV with the 6 A
cliff, a charger whose Bluetooth link drops for ten minutes, a meter whose
register only moves at the hour boundary plus twelve seconds.

The models are deliberately **richer than the product's own derivation tables**
(D4 §6) - two-node RC slab, stratified tank, EV taper, a COP curve with a
defrost signature - so that the planner and D11's shadows are tested against
something they do not already assume (D9 §2).

Nothing here imports `custom_components`: the shapes in `base.py` belong to
`tests/sim` and WP0.9's runner adapts between them and `core.model`
(D-0041).
"""
