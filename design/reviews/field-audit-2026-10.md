# Change: the house's first fortnight

> The change specification from the second field audit of the reference house, 24 Sep – 4 Oct 2026 (powerplan 0.0.3–0.0.7, every load `auto`, target 5–10 kW). It is filed so `design/PLAN.md` and the LLDs can cite its sections. §9 records the owner's decisions of 4 Oct 2026; the LLDs and DECISIONS (D-0709 … D-0717) carry them, and PLAN §3.0o schedules the work (FB.1–FB.7).

**Scope.** Seven changes (C1–C7) for six defects and one missing figure. Each names what the house showed, the cause, the change, the text it amends and the tests that close it. All numbers come from the house's recorder (states from 24 Sep 02:12 UTC, long-term statistics from Dec 2021), `home-assistant.log` and the site store of 4 Oct 17:34 UTC, unless a row says otherwise. Nothing was written to the house.

---

## 1. What the audit found

The field audit's own measures (`field-audit-2026-09.md` §14), after FA.1–FA.9 shipped in 0.0.3 on 26 Sep:

| Measure | 25–26 Sep | 26 Sep – 4 Oct | Target |
|---|---|---|---|
| windows over the ceiling | 0 of 26 | 0 of 225 | 0 ✓ |
| escalations with the measured total < 0.85 × ceiling | 10 a night | 0 | 0 ✓ |
| stage 3 per night | 13–32 min | 40 s, one night, measured 10.2 kW | 0 unless the house passes the ceiling ✓, but see F3 |
| observed device writes in minutes :00–:19 | 60 % | 57 % (349 of 609) | ≈ 33 %; quarter-hour slots put ≈ 50 % there by construction |
| baseline against measured | 2 990 against 1 376 W at 21:00 | bias +2.6 % (1 119 against 1 091 W over 187 h), MAE 293 W (a flat mean: 321 W) | within 15 % ✓ |
| deadlines missed | 0 | 10 counted, 0 real | 0 (F1) |

| # | Finding | Evidence | Kind | Change |
|---|---|---|---|---|
| F1 | A deadline nobody could meet is judged | EV: 6 counted misses; on 5 the charger read `disconnected` at 06:00 (28, 29, 30 Sep; 3, 4 Oct), on 1 the car sat at 98 % with the charger `awaiting_start` (27 Sep) | code vs D7 §5.10 ("unplugged … isn't judged") | C1 |
| F2 | The tank heats at the start of the night and misses its own ready band | 4 misses at 73.8–73.9 °C at 06:00 against the 74 °C band (ready 75 °C − `READY_BAND_K`); hits at 74.3–75.4 °C. Heating ends 00:15–01:30 local, the tank loses ≈ 1.2 K by 06:00; the top-up raised at 05:45 can't place a 30 min block in 15 min | code: `_fill_blocks` ignores `prefer_late` (D5 §5.3) | C2 |
| F3 | The allowance sprints into the seam | three nights: the house at 9.4–10.6 kW in a window's last 12 min (charger raised 24 → 32 A at 00:51), then stage 2 (twice) or 3 (once) at hh:00:15–24, the charger cut to 6 A and ramped back 4 A a step; 300 charger writes in 8 days; the tank shed 10 min at 30 Sep 21:00 UTC; at 1 Oct 23:00:24 UTC a live warning named 8.44 kWh "uncontrolled" while the EV (5.4 kW) and the tank (2.9 kW) ran | D6 §10's deferred item; its trigger is met, but the fix it names moves energy (C4) | C4 |
| F4 | A flapping device pushes a notification per flap | 30 `device_unhealthy` onsets in 11 days (EV 17, each back within 20–60 s; heat pump 1 seven `not_following`; six floors at once on 1 Oct 09:31 UTC), 9 inside quiet hours. A recovery clears the 6 h key (`engine.py` `_unhealthy_notifications`), and the category is urgent | D8 §2 vs D8 §5.8 code; HLD §4 lists a "device unhealthy" repair that was never built | C3 |
| F5 | `plan_adopted` fires on every re-cut | 3 277 events in 11 days (entrance floor 769, first-floor bathroom 715); on 27 Sep – 4 Oct, 235 of 2 213 changed mode, coverage, start or planned energy | D7 §5.2 | C5 |
| F6 | Two false signals | a WARNING at every start: "easee_ble: sensor.ee24504_battery_level declares unit None … leaving the role unbound" for a role the subentry binds; `level_changed` 5–10 → 0–2 kW (metric 0) at every month's first tick | code | C6 |
| F7 | The step below is invisible | September–October the house's daily peak was always 22:00–02:00, EV and tank stacked at the night rate's start, the rest of the house 0.7–1.8 kW; under 4.7 kWh an hour 9 of 11 nights would have held both, serialised (the two that didn't: EV 19.6 and 37.6 kWh). The step is worth (317.6 − 186.4) × 1.25 = 164 NOK a month; `auto` never aims below the reached step (D2 §5.5) | missing figure | C7 |

What held up: 0 windows over the ceiling, the October metric at 6.77 kW against 8.3–9.3 kW in every month since Sep 2024, peaks on charging nights down from 7.3–9.1 to 6.3–7.9 kW, comfort held (floors and heat pumps never under their minimum; the tank 151 min under 45 °C on 26–27 Sep evenings, none since), the EV and the tank at 100 % and 95 % night share, the baseline unbiased. The money is small at this house: ≈ 1 NOK a day against no control (4.68 NOK in September, 0.97 NOK in October so far; 0.7648 against 0.7752 NOK/kWh), the fee unchanged at 5–10 kW.

---

## 2. C1 - A deadline is judged only if the load still asks for it (F1) - D-0709

**Cause.** `_count_deviations` arms every demand that carries a future deadline. The EV's demand keeps its departure and its `required_kwh` while no car is connected (`wants = False`, reason "no car"), so at 06:00 the unmet requirement is judged a miss. D7 §5.10 already says a deadline withdrawn before it passes isn't judged, and lists "unplugged"; nothing defined withdrawn.

**Change.** A deadline is armed on a tick when the demand wants energy, or when its requirement is met (`required_kwh ≤ 0.1`). A demand that stops wanting while its requirement is unmet - a car not connected, a session the car ended - withdraws it. The tank's `wants` is `level < target − 1 K`, so a tank inside its band arms as met and one below it arms as wanting: its four misses stay misses, and C2 fixes them.

**Text amended.** D7 §5.10. **Tests.** D7 §9 29. **Docs.** `docs/entities.md` (the deviations sensor).

---

## 3. C2 - The tank finishes at its deadline (F2) - D-0710

**Cause.** The water heater plans with `min_block_min` 30, which takes `_fill_blocks`. Its block choice ties to the earliest (`(mean, start, stop)`), its extension to the lower index, and `prefer_late` reaches only `_fill_priced`. On a flat night every block scores the same, so the tank heats at 22:00 local and coasts six hours.

**Change.**
1. `_fill_blocks` honours `prefer_late`: equal blocks tie to the latest stop, an equal neighbour to the later slot.
2. The water heater derives `prefer_late: true`. A tank that ends near its deadline loses less heat and is at its target when the household needs it, and its block no longer starts with the EV's earliest-first fill. An existing tank keeps its stored parameters (INV-66) until the household saves **Change setup** on it.

**Text amended.** D5 §5.3, §6; D4 §5.12 (the tank's derivation). **Tests.** D5 §9 31; D4 §9 50. **Docs.** `docs/appliances/water_heater.md`, `docs/strategies.md`.

---

## 4. C3 - A device that stops answering is a repair, retried until it answers (F4) - D-0711

**Seen.** The charger's BLE link drops for 20–60 s every few hours and comes back; heat pump 1 ignores a setpoint write now and then (read-back 21.0 for 20.5). The owner knows both devices are flaky: keep retrying until they answer, and show it as a repair in Home Assistant (§9).

**Cause.** The gate already retries: a failed write goes out again once its command interval is up, and a deviation is re-issued when the gate allows (D4 §5.10), with no cap. What is wrong is the surface: `_unhealthy_notifications` sends a notification on every edge, a recovery clears its key, so the 6 h interval never applies, and `device_unhealthy` is urgent, so quiet hours never hold it. The repair HLD §4 lists was never built.

**Change.**
1. A repair per load, `device_unhealthy_<load>` or `device_not_following_<load>`: raised when the load's health has read that state for 30 min unbroken, cleared on the first tick it reads anything else. Thirty minutes is two heat-pump intervals (900 s), so one dropped write or a BLE reconnect never raises it. Not persistent: a restart re-evaluates.
2. The `device_unhealthy` notification goes out with the repair, once per raise, and is cleared with it. It is no longer urgent: quiet hours hold it, and the repair is there in the morning.
3. The bus event `powerplan_device_unhealthy` stays on the raw edge: an automation may want the flap.
4. D4 §8 says the retry has no end: the gate keeps sending, however long the device stays away.

On the house's eleven days: 30 notifications become 3 repairs (heat pump 1 on 29 Sep for 60 min, 1 Oct for 18 h, 3 Oct for 46 min).

**Text amended.** D8 §2, §5.8, §5.9; D4 §8. HLD §4 already lists the repair. **Tests.** D8 §9 49; D4 §9 50 (the retry). **Docs.** `docs/troubleshooting.md`, `docs/setup.md` (notifications).

---

## 5. C4 - Our own cut is ours; the seam stays deferred (F3) - D-0712, D-0713

**Cause.** `P_allow = E_budget / t_rem` grows as the window empties, so in its last minutes a window with budget left lets the charger run to its plan's cap. The power carries into the next window, the 120 s EMA projects it over the full hour, and the ladder escalates for under a minute. In that tick a settling load counts at its commanded power (INV-18), so the meter's uncontrolled term holds our own draw, and the live warning names it "uncontrolled".

**Change.**
1. The live "it's not us" warning (D7 §5.4) waits while a load of ours is settling down from a cut, and neither fires nor clears in that time.
2. No seam cap. Three were built and run through the catalogue (D-0712): the whole allowance capped at the next window's stage-0 level, which left the car at 79.93 % against 80 % at 4.7 kWh (`night_ev_tank_banked_floors_5kw`); at the stage-2 line, which shed the tank at hh:52, moved 0.3 kWh of its dwell into the next window and let a household burst take `legionella_expensive_week` to 6.12 kWh against 6.0 (5.86 without); and the modulating residual alone, 6.07 kWh. Every cap at a window's end moves energy into the next one. The seam costs current churn, not money, so D6 §10 keeps it deferred with the evidence.

**Text amended.** D7 §5.4; D6 §10. **Tests.** D7 §9 31. **Docs.** none: no screen changes.

---

## 6. C5 - `plan_adopted` on a change the household can see (F5) - D-0714

**Cause.** A plan is replaced whenever its inputs move (D5 §5.9): a floor's temperature in its digest, a charging car's requirement. That is right for the plan and wrong for the event, which is meant for the household.

**Change.** The plan is adopted and stored as before; `plan_adopted` fires when the adopted plan differs from the last one announced for the load in mode, coverage, start (a running plan's start is "now") or planned energy by more than max(0.5 kWh, 25 %). The last announcement is an edge in the `events` section.

**Text amended.** D7 §5.2; D8 §5.6. **Tests.** D7 §9 30. **Docs.** `docs/events.md`.

---

## 7. C6 - Two false signals (F6) - D-0715, D-0716

**Change.**
1. `extra_bindings` takes the roles the subentry binds and skips them: the unit check, and its warning, run only for a role still to bind (D4 §5.9, FA.9).
2. `level_changed` doesn't fire on a period's first tick: the new period's level seeds the edge silently, and `period_closed` already announces the close (D8 §5.6).

**Text amended.** D4 §5.9; D8 §5.6. **Tests.** D4 §9 51; D7 §9 32. **Docs.** `docs/events.md`.

---

## 8. C7 - The step the steered appliances could have reached (F7) - D-0717

**Change.** D11 bills a third book beside the actual and the counterfactual: the **levelled** one. Per day, each window's energy less what the steered loads drew in it is fixed, and the steered energy is poured over the day's eligible windows, lowest first (water-filling): the day's peak is the lowest level that holds it. The evaluator bills those days through the same code path as the other two books (INV-69), through the last settled day. The figure is an upper bound on what steering can do - a car that isn't home can't charge at noon - and it is labelled so.

1. `sensor.<site>_savings` gains `metric_kw_levelled` and `capacity_step_levelled` beside `metric_kw_without` / `capacity_step_without`.
2. At a period's close the ledger keeps the levelled step. When it was below the reached one, the advice sensor carries `step_below` for the next period: the step, its fee difference and the period it refers to. It is advice, never a target: below the reached step is a gamble the household picks (D2 §5.5, §11).

**Text amended.** D11 §5.13 (new); D2 §5.11; D8 §5.5. **Tests.** D11 §9 39; D8 §9 50. **Docs.** `docs/entities.md`, `docs/savings.md`, `docs/capacity-tariffs.md`.

---

## 9. Owner decisions

Decided by the owner on 4 Oct 2026.

| # | Question | Decision |
|---|---|---|
| 1 | Try the 2–5 kW step this winter? | No. The house keeps 5–10 kW in the winter months. The target is the household's own select per site, so nothing is built for it; C7 tells a household when the step below was within reach |
| 2 | Flaky devices (the charger's BLE, heat pump 1) | Keep retrying until they answer, and show a repair in Home Assistant (C3) |

No INV changes. C3 builds HLD §4's repair as written.

## 10. Work packages (PLAN §3.0o)

| WP | Produces | Implements | Exit criteria | Depends on |
|---|---|---|---|---|
| **FB.1 A deadline the load still asks for** | the arming rule in `_count_deviations` | D7 §5.10; D-0709 | D7 §9 29 | - |
| **FB.2 The tank finishes at its deadline** | `prefer_late` in `_fill_blocks`; the tank derives it | D5 §5.3, §6; D4 §5.12; D-0710 | D5 §9 31; D4 §9 50 | - |
| **FB.3 Retried until it answers, a repair meanwhile** | `device_unhealthy` repair after 30 min; the notification with it, not urgent | D8 §2, §5.8, §5.9; D4 §8; D-0711 | D8 §9 49; D4 §9 50 | - |
| **FB.4 Our own cut is ours** | the live warning waits for our own settle; the seam cap tried and left deferred | D7 §5.4; D6 §10; D-0712, D-0713 | D7 §9 31 | - |
| **FB.5 `plan_adopted` on a visible change** | the announced-plan edge | D7 §5.2; D8 §5.6; D-0714 | D7 §9 30 | - |
| **FB.6 Two false signals** | bound roles skipped; no `level_changed` at rollover; PLAN's FA rows | D4 §5.9; D8 §5.6; D-0715, D-0716 | D4 §9 51; D7 §9 32 | - |
| **FB.7 The levelled step** | the levelled book; two savings attributes; advice `step_below` | D11 §5.13; D2 §5.11; D8 §5.5; D-0717 | D11 §9 39; D8 §9 50 | - |

**Order.** FB.1–FB.3 first: they make the deviations sensor and the notifications true. FB.4–FB.7 in any order.

## 11. How we'll know

| Measure | 26 Sep – 4 Oct | Target |
|---|---|---|
| deadlines counted missed while the load wasn't asking | 6 | 0 |
| tank below its ready band at its deadline | 4 of 9 | 0 |
| `device_unhealthy` notifications for a device back within 30 min | 27 | 0 |
| live warnings while our own cut settles | 1 | 0 |
| `plan_adopted` per day | ≈ 300 | ≈ 30 |
| WARNINGs at a clean start | 1 | 0 |

## 12. Alternatives (steelmanned)

*Switch `device_unhealthy` notifications off at the house.* *For:* no code. *Against:* a charger that really stops would go unseen, and HLD §1.1 asks for observable. **Decision:** the repair, and the notification with it.

*Ship the seam cap.* *For:* the charger stops being cut 32 → 6 A at hh:00, and the house sees no stage-2 blip at the seam. *Against:* every version moved energy into the next window, and one scenario then went over its ceiling - 6.12 and 6.07 kWh against 6.0 - where it hadn't before; the seam costs churn, not money. **Decision:** deferred (D6 §10, D-0712).

*Let `auto` aim one step lower when the levelled book says it fitted.* *For:* households rarely pick a step by hand; it would save money silently. *Against:* the levelled book is an upper bound (it can't know when a car is home), and a failed gamble costs EV deadlines and comfort. D2 §11 keeps anything below the reached step a household's choice. **Decision:** advice now; `auto` only after the advice has proved right over a season.

*Count the tank's 0.1 K misses as met with a looser band.* *For:* one constant. *Against:* the misses are real - the plan coasted six hours - and a looser band hides the next real one. **Decision:** finish at the deadline (C2).
