# Benchmark baselines - changelog (D9 §5.11)

One line per baseline change: the WP, the metric, old → new, why. A metric outside
tolerance is fixed or explained here; a tolerance is never loosened.

| WP | build | tier | change |
|---|---|---|---|
| WP0.11 | `801993f` | smoke | First baseline: `nordic_detached@1` × `y2026_27@1`, every load controlled (D-0264). Two weeks: 334 windows, `over_target` 0, `comfort_violation_min` 10.5 (bedroom heaters at the floor under stage-2 sheds), `deadline_misses` 3 (recorded, `not_worse` until WP2.6), `sessions_dropped` 10 (the BLE simulator's own drops while paused), `writes` 2 713, fee 857 NOK, 225 ticks/s (§5.9 asks 500; D-0261). Zero-class tolerances start as `not_worse` per §5.11; `engine_failures`, `zero_amp_writes`, `commitment_breaks`, `plan_gaps` are `zero` from the start. Money columns from D11 wait for WP0.10. |
| WP0.10 | `ad8e77d` | smoke | House spec `nordic_detached@1` → `@2` (D-0268: the floor loops answer `loss_coeff_w_per_k` at 0.7 W/m²K × area, the car carries its 80 % app limit) - a reset that moves **no** control metric: windows, `over_target` 0, comfort 10.5 min, `deadline_misses` 3, writes 2 713, fee 857 NOK all identical to `801993f`. New money columns from the D11 ledger behind D7 (D-0267): `cost_energy` 721.78 NOK, `cost_counterfactual` 2 422.20 NOK, `savings` 843.50 NOK over the two weeks (October 427, January 416 - capacity dominates: the uncontrolled car at plug-in lands the counterfactual two steps above the controlled 5–10 kW); tolerances `cost_energy` `not_worse`, `savings` `not_less`. 198 ticks/s measured alongside other suites (225 alone at `801993f`; the per-load meters and the slot closes are the added tick work). |
