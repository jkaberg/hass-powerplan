# Change: the capacity axis measures the house

> The change specification from the field audit of the reference house, 23–26 Sep 2026 (powerplan 0.0.1, `7165a88`). It is filed here so `design/PLAN.md` and the LLDs can cite its sections. §12 records the owner's INV decisions of 26 Sep 2026; the HLD, the LLDs and DECISIONS (D-0685 … D-0693) carry them, and PLAN §3.0k schedules the work (FA.1–FA.9). Nothing in it is implemented yet.

**Scope.** Nine changes (C1–C9) for eight defects and one tariff behaviour. Each one names the problem as the house showed it, the cause in the code and the design text, the change, the HLD/LLD text it amends, the tests that close it, and the alternatives, steelmanned. All numbers come from the house's recorder, `home-assistant.log`, and the diagnostics snapshot of 26 Sep 10:18 UTC, unless a row says otherwise.

---

## 1. What the audit found

| # | Finding | Evidence | Kind | Change |
|---|---|---|---|---|
| F1 | The ladder escalates at every loaded hour's start | 15 escalations in two nights, 10 to stage 3, no window within 2.5 kWh of the ceiling; 7 hours of stage 3 in a row for a car that wasn't plugged in, at 17–37 % of the ceiling measured | LLD drift from INV-38/INV-62 | C1 |
| F9 | Idle on/off thermostats reserve 500 W each | Σ reserved 4 424 W against `P_allow` 4 964 W at 12:18; entrance floor denied "1 094 W < 1 200 W" | LLD (D6 §5.2) vs D-0168 | C2 |
| F2 | The seeded baseline is about an hour out of step | 21:00 bins 2 990 W against 1 376 W measured; two negative bins | code, duplicate reader | C3 |
| F3 | Expired `best_save` plans are kept | three plans built 23 Sep 16:02 UTC, last slot 25 Sep 16:00 UTC, still in force 26 Sep | D5 §5.9 gap | C4 |
| F4 | A device that ignores writes is invisible | HP1: 9 of 11 setpoint writes not applied, 9 read-back deviations, health `ok` | D4 §8 surface not built | C5 |
| - | A manual step below the reached one is still defended | step 1 (4.7 kWh) chosen with September at 5–10 kW; 0 NOK to gain | D2 §5.4 gap | C6 |
| F7 | Floor heating's `min_on_s`/`min_off_s` are unused | bathrooms flip heat → eco after 10 min 5 s against 900 s configured | D4 §5.10 vs §6.1 | C7 |
| F5, F6 | A sensor named "Monetary balance"; a partial month shown as a whole one | `sensor.nygardsvegen_6_monetary_balance`; 25 h of energy + a month's capacity fee = 457.40 NOK | code, surface | C8 |
| F8 | Restarts write and unwrite devices; an unbound role waits for the next start | EV enabled 20:45:25 and parked 20:46:10 on 25 Sep; SoC unbound 18:12–20:45 | INV-48, D-0485 | C9 |

What held up: 0 of 26 windows over the ceiling, both deadlines met, no comfort floor crossed, the price chain exact to four decimals, the meter windows exact, D2's peak history exact. The changes below touch *how* the house gets there, not whether it does.

## 2. What the changes serve

HLD §1: cheap energy, within the capacity the tariff allows, without breaking comfort or equipment; observable, fail-safe. At the reference house under Norgespris the price lever is 0.14 NOK/kWh day to night (0.8779 against 0.7379, D11 §5.9), and the capacity lever is the step: 5–10 kW → 2–5 kW is (317.6 − 186.4) × 1.25 = **164 NOK/month** (Tensio TS 2026-07-01). A capacity axis that holds back more than the ceiling needs gives away the second lever; one that sheds for nothing costs equipment and trust. Six principles fall out of that, and each change cites the ones it applies:

| | Principle | Already in |
|---|---|---|
| P1 | Actuation reads measurements, information reads forecasts | INV-38, INV-62, D-0627 |
| P2 | Hold back what a load is expected to draw, not what it may draw | D-0629 |
| P3 | A plan that doesn't cover the present isn't a plan | D-0253, partly |
| P4 | Defend a target the period can still reach | D2 §11 ("one below is unreachable") |
| P5 | A degradation the household can't see is a defect | HLD §1.1 "observable" |
| P6 | One reader per recorder convention | D3 §5.11, `providers/meters/recorder.py` |

---

## 3. C1 - The ladder projects from measured power (F1; P1) - D-0685

**Seen.**

| Night | Car | Escalations | Minutes at stage 1 / 2 / 3 | At the hour's start |
|---|---|---|---|---|
| 24→25 Sep | not connected (`disconnected`, plan status `no_car`, plan still 10.56–11.52 kWh) | 10, stage 3 every hour 00:00–06:00 | 71 / 31 / 32 | projection 10.4–10.9 kWh; measured 1.6–3.6 kW (hourly means) |
| 25→26 Sep | charging, 18.7 kWh | 5 | 36 / 14 / 13 | 00:00:04: projection 7.13 → 10.26 kWh with 0.03 kWh used; stage 3 at 00:00:19, back to 0 at 00:18:03 |

Fourteen of the fifteen escalations came 15–22 s after the hour, and the fifteenth at 22:15:24, a plan-slot boundary. None of those hours got within 2.5 kWh of the 9.7 kWh ceiling. The side-effects:

- 98 of 162 device writes landed in minutes :00–:19.
- The EV was cut 18 → 6 A and climbed back 2 A a minute.
- The heat pumps were set back 0.5–1.5 K at 21:30, 22:00, 00:00 and 01:00 and restored 30 min later.
- The tank was cut 23:00–23:10.

The live snapshot reproduces the arithmetic exactly. Projection 2.551 kWh = used 0.653 + floor envelopes 2 080 W × 0.689 h (1.434) + baseline 674 W × 0.689 h (0.464), while the three floors measured 0 W.

**Cause.** D6 §2 (D-0319) redefined `Budget.projected_kwh`: with a confident baseline it is `used + Σ plan.kwh_between(envelope) + ∫ baseline`. The HLD and the rest of D6 never moved:

- HLD INV-38: "the ladder projects from the smoothed total".
- HLD INV-62: "the ladder and the trim never read a forecast".
- D6 §5.4's table: "projection from `P_smooth`, τ = 120 s".

Four consumers still read `projected_kwh` as the measured number:

- the ladder, whose docstring at `ladder.py:172` calls it "the smoothed projection";
- `cap_for_projection`;
- the group rotation's scarcity test (`constraints/group.py:198`);
- `_live_warning` (`engine.py:2900`).

Two more pieces meet on top of that. D-0629 moved the planner to reserve a banked slot's planned draw instead of its envelope, but the projection still sums envelopes, so the room the planner handed the EV is counted a second time. And `deadline_fill` keeps a charging plan for an absent car, whose envelope the projection counts though no grant can follow it. D7 §5.4 (D-0627) already made the argument for the warning: "D6 caps every plan-driven grant at the ceiling (INV-1), so a plan never causes a breach." It holds for the ladder word for word.

**Change.**

1. `Budget.projected_kwh = used + P_smooth × t_rem`, always. `projection_source` goes.
2. `budget()` loses `controlled_planned_kwh`. `tick()` stops summing plans, and `Plan.kwh_between` has no caller left, so it goes (D9 hygiene). D-0319's reserve half stays: σ from D10's residual when confident, never under the floor (INV-62's allowance).
3. `sensor.<site>_forventet_forbruk_denne_timen` stays the ladder's number, so the sensor and `sensor.<site>_styringsniva` always agree. It gains an `expected_kwh` attribute with D7 §5.4's `expected`, the number the peak warning uses, so there is one definition of each.
4. D2's `projected_level`, the rotation's scarcity and `_live_warning` read the measured projection with no change of their own.

**Residual, measured before acting (C1b).** The EMA carries the previous hour's final rate into the new hour's full `t_rem`. At 00:00 on 25→26 Sep, `P_smooth` was ≈ 9.45 kW against 9.7, which gives stage 2 for a few minutes even after C1. The cause is the end-of-window catch-up: in the last minutes, `P_allow` reaches the fuse (`tilgjengelig_effekt_na` read 25.097 kW at 23:58). If the C1 scenario still shows stage ≥ 2 at seams with the measured total under the new window's allowance, cap `P_allow` in a window's final minutes at the next window's opening allowance for any grant that will still be running at the seam. Not before.

**Text amended.**
- D6 §2: the baseline paragraph loses the projection line and keeps the reserve.
- D6 §3 and §4: `budget()` signature, `Budget` fields.
- D6 §5.4: add "never a plan's energy, never a forecast (INV-38, INV-62)".
- D6 §9 3 is rewritten.
- D6 §11 gets the steelman below.
- D7 §5.1: tick step 5 drops the plan sum.
- D7 §5.4: the sensor attribute.
- D8 §5.5: the sensor's attributes.
- DECISIONS: D-0685, superseding D-0319's projection half.
- HLD: none. This brings D6 back under INV-38 and INV-62.

**Tests.**
- D6 §9 3: the projection is `used + P_smooth × t_rem` whatever the baseline's confidence.
- New D6 §9: a window packed to the plan fraction, measured draw at the allowance, `t = 0`, gives stage 0.
- New D9 scenarios: `night_ev_tank_banked_floors` (9.7 kWh, EV + tank + three banked floors: 0 escalations while the measured total stays under 0.85 × ceiling) and `ev_plan_no_car` (a planned EV with no car adds nothing).
- Markers INV-38 and INV-62.

**Docs.** `docs/entities.md` for the sensor's attribute.

**Alternatives (steelmanned).**

*Fix the double count only: prorate `_planned_draw_w` in `kwh_between`, skip absent loads.* *For:* the smallest diff. It keeps D-0319 as decided, and the forecast still sees a planned 3 kW start at :30 from :00. *Against:* the ladder still reads a forecast (INV-62), so the baseline's own error (C3: +1.6 kW at 21:00) triggers sheds. The anticipation buys nothing either: the allocator caps every plan-driven grant at the budget, which is D-0627's argument. **Decision:** measured.

*Keep the baseline in the projection, drop only the plans.* *For:* the evening's cooking is visible before it starts. *Against:* that is the reserve's job, and INV-62 already lets D10 shape it. The projection answers "where does this window land if nothing changes", and a forecast is a guess about change. **Decision:** measured.

*Project what the grants will draw: `used + (uncontrolled + Σ granted) × t_rem`.* *For:* it judges this tick's decision rather than the last tick's state, so the seam carry-over disappears. *Against:* it is circular, because the stage feeds the walk. It would also re-inflate on the reservation margins C2 removes. **Decision:** not now; C1b covers the seam if it matters.

*Raise the thresholds.* *For:* configuration only. *Against:* the projection opened at 106–112 % of the ceiling, so no threshold below 1.12 stops it, and a stage-3 trigger above the ceiling gives up the ceiling. **Decision:** fix the input.

---

## 4. C2 - An idle on/off thermostat reserves what its plan draws (F9; P2) - D-0686

**Seen.** Snapshot 26 Sep 12:18, target 4.7 kWh:

| Load | Kind | State | Measured | Reserved |
|---|---|---|---|---|
| Heat pump 1, 2 | modulating thermostatic | idle | 23 W, 81 W | 523 W, 581 W |
| Tank | setpoint (resistive) | 70.1 °C, setpoint 45 | 0 W | 500 W |
| Bathroom 1, entrance, kitchen, living room, TV room | mode | idle or shed | 0.00–0.07 W | 500 W each |
| Bathroom 2 | mode | granted | 0.03 W | 320 W (its nameplate) |

The walk judges a load against `P_allow − uncontrolled − Σ reserved(loads before it)`. For the entrance floor that is 4 964 − 1 446 − 2 424 = **1 094 W**, below its 1 200 W. The EV, walked last, sees every margin: 4 964 − 1 446 − 4 424 < 0. The living-room floor used 0.002 kWh since install and the kitchen 0.50 kWh (`energi_totalt`), yet each held 500 W the whole time.

**Cause.**
- D6 §5.2's table: an on/off kind (SWITCH, MODE, resistive SETPOINT) reserves "nameplate if grant > 0 or currently on, else 0".
- D-0168: "a thermostatic load measured at zero reserves the margin". That was written for heat pumps (D6 §5.2's second row).
- `LoadView.thermostatic` defaults to true for every `setpoint` and `mode` kind (`strategies/context.py:549`), and `reserved_w` tests `thermostatic` before the on/off branch.

So every floor and the tank take the heat pump's rule. The table and D-0168 disagree, and the code follows D-0168 for all of them.

**Change.** D6 §5.2 rows:

| Kind | Reserves |
|---|---|
| modulating thermostatic (heat pump) | measured + grant margin (500 W), ≤ rated - unchanged |
| on/off, running (measured > `ON_W`) or granted | nameplate - unchanged (D-0169 included) |
| on/off thermostatic, idle | its plan's draw for this slot, `_planned_draw_w` (standing loss + planned), 0 without a plan |

A relay that closes on its own is seen on the next tick (≤ 10 s, D7 §2) and reserves its nameplate from then on (D-0169). A surprise from the entrance floor costs 1.2 kW × 10 s ≈ 3 Wh of the window, and the trim takes it back from the lowest priority. D-0168 is amended to name the modulating kind.

**Text amended.** D6 §5.2 table and text; D-0168; D6 §9 new. **Tests.** New D6 §9: five idle mode floors with banked plans reserve their planned draw, not 5 × 500 W; a floor whose relay closes reserves its nameplate the next tick. **Docs.** No user-visible change.

**Alternatives (steelmanned).**

*Keep a margin per thermostat.* *For:* any of them can start any moment, D-0168 was deliberate, and more held back is never less safe. *Against:* they don't start together. Bathroom 1 drew 300–330 W in one hourly mean out of every three to five (24–26 Sep). At 4.7 kWh the margins are 74 % of the allowance, which makes step 2–5 kW, the 164 NOK/month, unworkable for the lowest-priority load. **Decision:** planned draw.

*Reserve 0 when idle, the D6 table as written.* *For:* simplest, and already the text. *Against:* a banked floor's standing loss is real energy the window will carry; 0 hands it to the EV, and the trim takes it back on every relay cycle. **Decision:** planned draw, which is 0 only without a plan.

*One site margin: the largest idle thermostat that could start.* *For:* covers the single surprise, independent of plans. *Against:* on the snapshot the largest one able to start is the TV-room floor at 1 920 W, which frees ~1 kW and still denies the entrance; it also needs a per-kind "could start" test. **Decision:** planned draw.

*A learned duty cycle per thermostat.* *For:* the most accurate. *Against:* D10's fits for these floors don't pass yet (R² −0.56 … 0.14 in the fit log of 26 Sep 03:17). **Decision:** v1.x.

---

## 5. C3 - One register reader, one placement rule (F2; P6) - D-0687

**Seen.** Re-seeding the baseline offline with the core's own `uncontrolled_history` and `HourOfWeekBaseline.seed` over the house's recorder (end 26 Sep 10:00 UTC, the shipped 60-day span):

| | 21:00 bins, mean | 23:00 bins, mean | Negative bins | `lag_h` |
|---|---|---|---|---|
| measured uncontrolled (grid − ten load meters, 13–26 Sep) | 1 376 W | 1 036 W | - | - |
| stored in the house | 2 990 W | 543 W | 2 | not kept |
| seed as shipped (`sum` placed at the row's start) | 2 149 W | 812 W | 6 | 0 |
| seed with `sum` placed at the row's end | 1 374 W | 1 107 W | 0 | 0 |

**Cause.** `providers/forecasts/recorder_baseline.py::_query` places a `sum` row at its `start`. HA files a statistics row under its period's start, but its `sum` is the register after the last report inside the period (`sensor/recorder.py`, `stat["sum"] = _sum` after the loop). So the meter trace runs one period early against the loads' correctly stepped means (D-0483). On 5-minute rows that moves the 22:00 start of the EV and tank into the 21:45 window, and on hourly rows into 21:00. The rule is already written and implemented once: D3 §5.11 and `providers/meters/recorder.py` place a row at `S + period − min(c, period)`, with `c` the register's report cadence. D2's peak seed uses that reader, which is why the month's top three days are exact. `recorder_baseline` is a second reader that skipped the rule. The fixed-price saving (`runtime.py::_fixed_saving` via `async_site_register_kwh`) is the third consumer, on the wrong reader; that fits its 1 131 NOK against ≈ 1 122 NOK recomputed aligned. The split was recorded, not accidental: D3 §5.11 said "D10's own reader still places every row at S", D-0352 had rejected the 5-minute table for want of a reported case, and D10 leaned on its `meter_lag_h` detector instead. With rows placed where their values refer to, that detector finds 0 on this house (r 0.97 at lag 0 against 0.73 at lag 1), so it stays as a logged safety net for a register that really publishes late.

**Change.**
1. `providers/meters/recorder.py` becomes the only reader of the import register's statistics, for both tables: `async_register_rows(hass, entity_id, start, end)`, the 5-minute table within 10 days and the hourly one beyond, with the same placement rule.
2. D10's seed and the fixed-price saving call it. `async_site_register_kwh` and `_statistic_rows`'s `sum` branch go.
3. The seed logs `lag_h` at INFO and keeps it in the forecast store (D10 §5.2), so a wrong detection is visible.
4. Bump the seed version, so every install re-seeds once. The house loses three days of live updates made on shifted bins.

**Text amended.**
- D3 §5.11: the placement rule binds every reader, both tables, one module.
- D10 §5.2: the seed reads through D3's reader; `lag_h` is kept.
- D8 §5.16 (D-0499): the fixed-price saving's source.
- DECISIONS: D-0687, which also bounds D-0483 to the loads' `mean` rows.

**Tests.**
- New D10 §9: a seed from HA-shaped rows (hourly and 5-minute, a 10 s reporter, a step at 22:00 local) fills bin 22, not 21, and a house with no production has no negative bin.
- New D3 §9: the reader on 300 s rows.
- An import-boundary test: only `providers/meters/recorder.py` asks `statistics_during_period` for an energy `sum`.

**Alternatives (steelmanned).**

*Place the row at its end inside `_statistic_rows`.* *For:* a one-line change. *Against:* two readers of one register already diverged once, and the end isn't quite right either: a meter that latches at the boundary reports at `S`. The cadence rule is D3's and belongs in one place. **Decision:** one reader.

*Let the 28-day half-life wash it out.* *For:* no migration. *Against:* every new install seeds the same error, and the 21:00 bin sizes the planner's headroom (D5 §5.1) for weeks. **Decision:** fix and re-seed.

*Seed from states, not statistics.* *For:* every timestamp is exact. *Against:* states are purged after 10 days, and D-0505 chose statistics for 60 days of shape. **Decision:** statistics, with the rule.

---

## 6. C4 - A plan that no longer covers the present is replaced (F3; P3) - D-0688

**Seen.** The kitchen, living-room and TV-room floors (`best_save`) still carried plans built 23 Sep 16:02 UTC, whose last slot ended 25 Sep 16:00 UTC. On 26 Sep 12:18 the TV room, 0.4 K under target, read `capped_by: plan`, `denied: planned idle`, 0 kWh planned. Comfort held only because a `mode` load's idle is "stand still" (21.4–22.8 °C on its own thermostat).

**Cause.**
- D5 §5.9 replaces a spent plan only when the new one has an active slot ahead (D-0253). `best_save` never has one; it answers `None` or `0` per slot (D5 §5.5).
- On a flat Norgespris day both plans cost 0, so the difference never clears the threshold.
- `Plan.cap_w` (`core/model.py:607`) answers `0.0` for an instant past the plan's last slot.

**Change.**
1. D5 §5.9: a kept plan must cover the next hour, `old.slots[-1].end ≥ now + 1 h`, else the new one is adopted, hysteresis or not. D-0253 becomes a case of it.
2. `Plan.cap_w` past the last slot answers `None`, and the load's reason says the plan ran out. That is defence in depth: after 1 it can't happen, and if it does the allocator, with every ceiling and floor, has the load rather than a hold.

**Text amended.**
- HLD INV-32 (decided, §12): add "and a kept plan covers the next hour".
- HLD INV-30: add "an instant outside every slot is `None`".
- D5 §5.9, D5 §4 (`cap_w`), D5 §9 new.

**Tests.** New D5 §9: `best_save` on a flat curve, with a kept plan whose slots end before now + 1 h, is replaced; `cap_w` past the last slot is `None`.

**Alternatives (steelmanned).**

*Only the `cap_w` fix.* *For:* one line, no change to adoption. *Against:* the plan status and the calendar keep publishing a plan from three days ago; the defect is keeping it. **Decision:** both.

*A plan TTL.* *For:* generic. *Against:* a TTL shorter than the horizon churns against INV-32, and coverage is the TTL already, stated in the plan's own time. **Decision:** coverage.

*Outside a plan stays `0`.* *For:* conservative on price, since nothing runs unplanned. *Against:* for a relay or setpoint load, `0` holds it off until a comfort floor breaks, so INV-30's stand-still becomes a shed in effect. `None` keeps every ceiling and floor. **Decision:** `None`.

---

## 7. C5 - Read-back deviations surface as "not following" (F4; P5) - D-0689

**Seen.** Between 25 Sep 21:30 and 26 Sep 02:22 powerplan wrote HP1's setpoint 11 times; the device took 2 (00:00:18, 02:22:59). It held 21 °C for 2 h 30 min while the plan asked 20.5–20, then 20 °C for 1 h 52 min while the plan asked 21. The trace was nine INFO lines, while `sensor.inngang_varmepumpe_1_etasje_helse` read `ok`. HP2, an ESPHome unit of the same make and profile, took every write.

**Cause.**
- D4 §8's table already has the row: "Write accepted, not applied (BLE) → read-back deviation; re-issued next tick → INFO, `deviations`", and D8 §5.5 lists `deviations` as an attribute of `sensor.<load>_health`.
- `GateState.deviations` is counted (`core/loads/gate.py:556`) and read by nothing: health, the sensor, diagnostics and notifications all miss it.
- "Re-issued next tick" isn't true for a heat pump either, whose 900 s interval (row 6) gates the retry.

**Change.**
1. Count consecutive deviations per role; the first read-back that matches resets the count. At 3 in a row the load's health is `not_following`, a state beside `ok`, `transient` and `unhealthy`. The load stays in allocation, and decisions stay against the measured state (INV-22). It is not a failure.
2. `sensor.<load>_helse` gains the state and the `deviations` attribute D8 §5.5 already lists, plus `last_deviation_at` and `role`.
3. The `device_unhealthy` notification category carries `not_following` with its own text. The house already routes that category to notify, and a new category would be one more switch nobody turns on.
4. Diagnostics carry each load's `GateState`.
5. D4 §8's row is corrected: re-issued when the gate allows it (§5.10).

**Text amended.**
- HLD INV-22 (decided, §12): "… a deviation is logged, not counted as a failure; three in a row on one role are published as `not_following`".
- D4 §5.10 (verify), D4 §8.
- D8 §5.5: the health sensor's states; D8's notification text.
- D4 §9 and D8 §9 new.

**Tests.** New D4 §9: three deviations give `not_following`, one match clears it, and the allocation is unchanged throughout. New D8 §9: the sensor's state and attributes.

**Docs.** `docs/entities.md`, `docs/troubleshooting.md` ("a device that doesn't follow").

**Alternatives (steelmanned).**

*Count deviations as failures, unhealthy at 2.* *For:* it reuses a path that notifies already. *Against:* `unhealthy` takes the load out of allocation (D4 §8), so a heat pump that drops one Bluetooth write in ten stops being steered. INV-22 exists because of exactly that. **Decision:** a separate state.

*Keep the INFO lines.* *For:* nothing to build. *Against:* nearly two hours against the plan, visible only in `home-assistant.log`. HLD §1.1 asks for observable. **Decision:** surface.

*Retry a deviation as urgent.* *For:* faster recovery. *Against:* heat pumps have no urgent path (compressor protection, D4 §5.10), and a device that drops writes drops the retry too. **Decision:** no.

---

## 8. C6 - A step the period has passed isn't defended (P4) - D-0690

**Seen.** September's metric was 8.97 kW (17 Sep 9.12, 21 Sep 8.94, 13 Sep 8.86), which is the 5–10 kW step, before powerplan ran. On 26 Sep 08:52 the target became step 1, strictness `flat`, so the ceiling was 4.7 kWh/h for the rest of the month. Consequences:

- `capacity_savings` 0.000.
- Today's worst hour was already 6.32 kWh.
- The next step needs one day at 11.95 kW (`days_that_matter`).

Every kWh held back until 1 Oct buys nothing.

**Cause.** D2 §5.6: `feasible(T) = 0` once `metric_now > T`. D2 §5.4 at `risk < 0.5` takes `base = T − ε` whatever `feasible` says. D2 §11 names the case ("one below is unreachable") and answers it only with the `auto` default.

**Change.**
1. D2 §5.4: while `metric_now > T_chosen`, in a period whose metric can't fall (`mean_top_n` over a month; not `rolling_months`, where old months leave), `T_eff = max(T_chosen, T_auto)`. That is `auto`'s answer (§5.5): the reached step's bound.
2. The ceiling's reason is `unreachable_target`, and the advice `target_unreachable` carries the date the chosen target applies again.
3. The select keeps the household's choice, and from the next period it binds from day one.

**Text amended.**
- HLD INV-10 (decided, §12): "a step below the one the period has already reached isn't defended; the reached step is, until the period closes".
- D2 §5.4, §5.5, the advice table, D2 §9 new.
- D8: the options of `sensor.<site>_anbefaling`.

**Tests.** New D2 §9:
- NO, step 1 chosen, metric 8.97 → the ceiling is the 5–10 kW bound less ε (9.7), with reason `unreachable_target`.
- The 1st of the next month → 4.7 from day one.
- BE rolling-12 → unchanged.

**Docs.** `docs/capacity-tariffs.md`, `docs/entities.md`.

**Alternatives (steelmanned).**

*Flat means flat.* *For:* the household chose a step and strict, a strict household wants predictability, and a lean last week can be practice for October. *Against:* the bill can't change until the mean passes 10 kW, so holding 4.7 costs EV room and comfort for 0 NOK, and October's practice starts on the 1st anyway. **Decision:** relax, with the reason visible.

*Advice only.* *For:* never overrides a choice. *Against:* the house still sheds for nothing; `sensor.<site>_anbefaling` already says `step_headroom`, and an info item doesn't change a ceiling. **Decision:** behaviour, with a reason.

*Relax to the full slack (8.86 kW) rather than the reached step (9.7).* *For:* passing 8.86 raises the metric. *Against:* raising the metric inside the step costs nothing (a `StepTable` prices the step), and `auto` already defends the reached step with tests. **Decision:** `auto`'s answer.

---

## 9. C7 - The floor's dwell applies to its mode (F7) - D-0691

**Seen.** Bathrooms 1 and 2: heat 11:14:37 → eco 11:24:42 → heat 11:34:48 on 26 Sep (10 min 5 s, 10 min 6 s) against 900 s configured. The entrance floor changed mode 20 times in 15.5 h.

**Cause.** D4 §6.1 lists "min on/off (900 s), command interval (600 s)" as floor heating's advanced answers, feeding the kind. `ModeKind.dwell_s()` returns `(0, 0)` ("the mode toggle's clock is the command interval", D4 §5.10), and `floor_heating._kind` passes the dwell to `SetpointCfg` only.

**Change.** `ModeCfg` takes `min_on_s`/`min_off_s`, and floor heating passes its answers, 900/900 by default. Row 7 then gates the comfort ↔ shed reversal; `urgent` (a slab from stage 3) and `blunt` still pass, as today.

**Text amended.** D4 §5.5, the §5.10 table, §6.1, D4 §9 new. **Tests.** New D4 §9: a mode floor switched to eco can't go back to heat within `min_off_s` unless the change is urgent. **Docs.** No user-visible change; the field already exists.

**Alternatives (steelmanned).**

*Hide the fields for the mode kind.* *For:* the thermostat protects its own relay. *Against:* for a Heatit in floor mode the eco flip *is* the relay decision (eco is 1.5–2 K lower), and D4 §6.1 already promises the dwell. **Decision:** honour it.

*Raise the mode's interval to 900 s instead.* *For:* one knob. *Against:* the interval limits every write, both ways; the dwell is the reversal clock, the thing that damps a flip-flop. **Decision:** dwell.

---

## 10. C8 - A sensor's name and a partial month (F5, F6; P5) - D-0692

**Seen.**
- `sensor.nygardsvegen_6_monetary_balance` is the fixed-price saving (unique id `…_fixed_price_savings`), named by its device class.
- `sensor.nygardsvegen_6_kostnad_denne_maneden` reads 457.40 NOK: 60.40 NOK of energy since 25 Sep 09:00 UTC (a schema-1 ledger discarded, D11 §9 32), plus the whole 397.00 NOK September fee.
- Per load, cost with unsettled slots stands next to a settled-only counterfactual (bathroom 1: 1.49 against 0.74 NOK).

**Cause.** `SiteFixedPriceSavingsSensor` (`sensor.py:993`) doesn't re-assign `_attr_translation_key` after `PowerplanEntity.__init__` sets it to the key, which `SiteDeviationsSensor` does. D11 intends the month's share of the fee against a zero baseline for a partial month (§2, §5.11), and live cost before settlement (§5.9). The surface doesn't say either.

**Change.**
1. The one line in `sensor.py`, plus an entity-registry migration renaming an entity id that still carries the device-class fallback.
2. The cost sensors gain `partial` and `energy_since`, and the month card says "since {date}" when partial (D12).
3. Per-load savings rows compare `settled_cost` with `cf_cost`; D11 has both.

**Text amended.** D8 §5.5, D12 month card, D9 new test. D11: none.

**Tests.** New D9 (surface): every entity's *effective* `translation_key` exists in `strings.json`. That catches an instance override the class attribute hides.

**Docs.** `docs/entities.md`, `docs/savings.md`.

**Alternatives (steelmanned).**

*Prorate the fee for a partial month.* *For:* like for like with the energy. *Against:* the fee is billed for the whole month (D11 §2), and a prorated number is one no bill will ever show. **Decision:** label it, don't prorate.

---

## 11. C9 - Restart hygiene (F8), after C1–C5 - D-0693

**Seen.**
- The 18:12 start on 25 Sep left the EV's SoC and HP2's outdoor temperature unbound; their entities had no unit yet. The 20:45 start bound both.
- Every start releases every load and the first tick re-applies. The EV was enabled at 20:45:25 and parked at 20:46:10.
- Across 51 reloads on 23–25 Sep, the EV charged 3.4 kWh at the day price (23 Sep 20:00), and the tank sat at its pre-powerplan 75 °C for 14 h on 24 Sep, reheating three times in the daytime.

**Change.**
1. **Binding (D-0485):** an answered role whose entity has no unit at setup gets a one-shot state listener that binds it when the unit arrives, instead of waiting for the next start. D4 §5.9, D7 §5.5.
2. **Startup release netted against the first tick:** startup computes each load's release but writes it only for loads the first tick leaves uncontrolled (mode off, observe, removed). A controlled load gets the first tick's value in one write. D7 §5.5. **Deferred** (§12, decision 5): netting the startup release alone leaves the unload release (INV-26), so a reload still churns; the change isn't worth a new path where F-1 happened.

**Alternatives (steelmanned).**

*Keep INV-48 as written.* *For:* a known baseline at every start, and a crash can leave a device mid-shed. Audit F-1 (startup writing in observe) came from a startup path doing something clever. *Against:* INV-64 already makes every shed state safe indefinitely, so the release buys no safety at startup, only a write and an unwrite, while the first tick writes anyway. **Decision:** keep INV-48 (owner, 26 Sep); C9.1, the binding listener, goes ahead.

---

## 12. Owner decisions

Decided by the owner on 26 Sep 2026.

| # | INV | Added wording | Decision | Change |
|---|---|---|---|---|
| 1 | INV-32 | "… nor past its own end: a kept plan that doesn't cover the next hour is replaced" | adopted | C4 |
| 2 | INV-30 | "An instant outside every slot of a plan is `None`" | adopted | C4 |
| 3 | INV-22 | "… three in a row on one role are published as `not_following` - still not a failure, and the load stays in allocation" | adopted | C5 |
| 4 | INV-10 | "… a chosen step below the one the period has already reached can't be defended, so the reached step is, until the period closes" | adopted | C6 |
| 5 | INV-48 | netting the startup release against the first tick | **not adopted**, INV-48 unchanged; C9.2 deferred | C9 |

C1, C2, C3 and C7 change no INV. C1 brings D6 §2 back under INV-38 and INV-62 as they stand.

## 13. Work packages (PLAN §3.0k)

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **FA.1 The ladder reads the house** | measured `projected_kwh`; plan sum and `kwh_between` removed; `expected_kwh` attribute | D6 §2, §5.4; D7 §5.1, §5.4; D8 §5.5 | D6 §9 3 + new; scenarios `night_ev_tank_banked_floors`, `ev_plan_no_car` | - |
| **FA.2 A slot's draw, not a margin** | idle on/off thermostats reserve planned draw | D6 §5.2; D-0168 | D6 §9 new | FA.1 |
| **FA.3 One register reader** | `async_register_rows`; seed and fixed-price saving on it; `lag_h`; re-seed | D3 §5.11; D10 §5.2 | D10 §9 new; D3 §9 new | - |
| **FA.4 Plans cover the present** | coverage rule; `cap_w` `None` outside | D5 §4, §5.9; INV-30, INV-32 | D5 §9 new | decisions 1, 2 |
| **FA.5 Not following** | health state, sensor attributes, notification text, diagnostics | D4 §5.10, §8; D8; INV-22 | D4 §9, D8 §9 new | - |
| **FA.6 The reached step** | `T_eff`, reason, advice | D2 §5.4, §5.5; D8; INV-10 | D2 §9 new | - |
| **FA.7 The floor's dwell** | `ModeCfg` dwell | D4 §5.5, §5.10, §6.1 | D4 §9 new | - |
| **FA.8 Names and partial months** | the translation key, migration, `partial`/`energy_since`, settled comparison | D8 §5.5; D12 | D9 surface test; D8 §9 | - |
| **FA.9 A role binds when its entity is ready** | one-shot listener for answered roles with no unit at setup | D4 §5.9; D7 §5.5 | D7 §9 new | - |

**Order.** FA.3 and FA.1 first: they decide what the house does tonight under 4.7 kWh. FA.2 comes before any month run at step 1. FA.4, FA.5, FA.7 and FA.8 are independent. FA.6 before the next month whose level is set early. FA.9 last.

## 14. How we'll know

**Before merge:** the new §9 tests, the two scenarios, `tools/benchmark.py --tier smoke --compare` (the digests change on purpose for FA.1 and FA.2; each changed metric is named in the PR).

**On the house:** after deploy, the same measures as the audit's stable run.

| Measure | 25–26 Sep | Target |
|---|---|---|
| escalations per night with the measured total < 0.85 × ceiling | 10 (24→25 Sep) | 0 |
| minutes at stage 3 per night | 13–32 | 0 unless the measured total passes the ceiling |
| device writes in minutes :00–:19 | 60 % (98 of 162) | ≈ 33 % |
| baseline 21:00 bins against measured | 2 990 against 1 376 W | within 15 % |
| windows over the ceiling | 0 of 26 | 0 |
| deadlines missed | 0 | 0 |

## 15. Alternatives to this plan (steelmanned)

*Tune, don't redesign.* *For:* thresholds, margins and intervals are configuration, and the house met every goal. *Against:* C1's input sits above every sane threshold, C2's margins are structural, C3 and C4 are bugs, and tuning would bury two measures that disagree under a third. **Decision:** fix the measures.

*A window optimiser (MPC) instead of plan + walk + ladder.* *For:* one model, no seam between planner and allocator, no double counting by construction. *Against:* it is a non-goal in HLD §1.2. The walk is the precedence rule made readable, and the defects here are two numbers that disagree, which an optimiser fed the same inputs would inherit. **Decision:** no.

*Wait for winter data.* *For:* September's loads are light, and the defects cost about a krone a night. *Against:* under the 4.7 kWh target the same mechanisms decide whether the EV is full by 06:00, winter adds heat-pump load to the same hours, and C3's seed error is in every new install from day one. **Decision:** now, in the order of §13.
