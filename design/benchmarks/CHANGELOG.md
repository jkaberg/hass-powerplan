# Benchmark baselines - changelog (D9 §5.11)

One line per baseline change: the WP, the metric, old → new, why. A metric outside
tolerance is fixed or explained here; a tolerance is never loosened.

| WP | build | tier | change |
|---|---|---|---|
| WP0.11 | `801993f` | smoke | First baseline: `nordic_detached@1` × `y2026_27@1`, every load controlled (D-0264). Two weeks: 334 windows, `over_target` 0, `comfort_violation_min` 10.5 (bedroom heaters at the floor under stage-2 sheds), `deadline_misses` 3 (recorded, `not_worse` until WP2.6), `sessions_dropped` 10 (the BLE simulator's own drops while paused), `writes` 2 713, fee 857 NOK, 225 ticks/s (§5.9 asks 500; D-0261). Zero-class tolerances start as `not_worse` per §5.11; `engine_failures`, `zero_amp_writes`, `commitment_breaks`, `plan_gaps` are `zero` from the start. Money columns from D11 wait for WP0.10. |
