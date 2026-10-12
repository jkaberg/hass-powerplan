# Review: the house's week after the October fixes

> The reference house, 4 Oct 20:05 UTC – 12 Oct 08:10 UTC 2026 (powerplan 0.0.7 at `8eb4fef`, every load `auto`, target 5–10 kW, Norgespris 0.40 NOK). Seven findings, their root causes, and the change or the proposal for each, steelmanned. All numbers come from the house's recorder, `home-assistant.log`, the diagnostics dump of 12 Oct 08:01 UTC and a read-only `weather.get_forecasts` call, unless a row says otherwise. Nothing was written to the house. Heat pump 1 (`not_following`, 6 onsets) is the owner's known device and is out of scope.

---

## 1. What held

| Measure | 4–12 Oct | Target |
|---|---|---|
| windows over the ceiling | 0 of 275 (month) | 0 ✓ |
| month metric | 8.10 kW (top 8.16, 8.11, 8.02) | under 10 kW ✓ |
| energy at the night rate (22–06) | 258 of 378 kWh this month (68 %) | as much as fits ✓ |
| comfort below the floor | tank 55 min, 2 episodes, both 6 Oct 19:08–20:03 | 0 unless the draw outruns the element |
| real EV deadline misses | 1 (5–6 Oct: plugged in at 17 %, 89 % at 06:00) | 0 |

The tank's dip was physics: a draw took the centre sensor from 57.1 to 33.3 °C in 19 min (19:02–19:21) against a 3 kW element; its parked setpoint is the floor, so it heated on its own at 44.6 °C. Nothing a plan could have done short of a hotter tank.

---

## 2. Findings

| # | Finding | Evidence | Root cause | Change |
|---|---|---|---|---|
| L1 | Heat pumps never rank in a zone | both zones `excluded: cop_below_floor` at 6 °C; `forecasts.outdoor_c: null`; baseline `beta_w_per_k: 0` in every bin | two: the runtime never sets `Inputs.outdoor_c`, and `weather_entity.py` reads `native_temperature`, which `weather.get_forecasts` never returns | D-0719 |
| L2 | The charger is cut to 6 A at most loaded hours' start | 10–11 Oct, 00:01:31–00:01:42: grant 7 360 → 5 520 → 5 060 → 3 450 → 1 840 → 1 380 W, back to 7 130 W at 00:02:07 and the charger 4 A/min behind it; same at 01:00:25, 02:00:23, 03:00:18, 22:00:20, 23:00:22 | the walk cuts a settling charger again on a residual INV-18 shortened by exactly the last cut | D-0718 |
| L3 | `deadline_at_risk` for a car that is not there | 4 of 5 EV events (7, 8, 10, 11 Oct) with `sensor.garasje_billader_status` = `disconnected` | the event reads the plan alone; D-0709 keeps an absent car's plan | D-0720 |
| L4 | The ladder flips 0 ↔ 1 | 35 `stage_changed` in 8 days, 26 of them 6 Oct 02:00–06:01 and 12 Oct 00:01–01:01, mostly at 85 % / 84 % | de-escalation's clean-tick test reads the allowance, never binding at 85 %; same threshold both ways | D-0721 |
| L5 | `plan_adopted` at every quarter of a charge | 288 events: EV 117, tank 77; a charge announced 29.8 → 20.5 → 15.4 → 10.6 → 6.7 → 4.8 → 1.9 → 1.0 → 0 | D-0714 compares with the last announcement; a running plan's remainder falls 25 % a quarter | D-0722 |
| L6 | The charger is enabled for a minute at every restart | `switch.garasje_billader_charger_enabled` on 07:37:40 → off 07:38:35, on 09:58:27 → off 09:59:22 | INV-26: release on HA stop, re-park at the first tick after start | proposal, §8 |
| L8 | Every heating appliance reads a loss | October per-load savings: inngang −0.66, bad 1 −0.19, VP1 −1.04, VP2 −1.20 NOK; reference price 0.811 against an even Norgespris day's 0.831 | the counterfactual takes phantom surplus at 0 NOK on a house with no panels | D-0723 |
| L9 | Heating control can't pay here | floors are `mode` devices: "bank" is their own thermostat, the lever is ~45 min of eco before 22:00; heat pump 2 dips 0.5 K 21:30–22:15 | Norgespris leaves 0.14 NOK/kWh; a two-level device can't store heat | D-0724, §11 |
| L7 | Floors that barely run, a grant that isn't drawn | stua 0.01 kWh since 25 Sep; kjøkken 0.54; bad 2. etasje granted 320 W at 0 W; TV-stua shed and drawing 612 W | no defect: rooms at or above target, the device's own deadband, eco mode holding its setpoint | none, §9 |

---

## 3. L1 - the outdoor temperature never reached anything (D-0719)

**Cause, first half.** `Inputs.outdoor_c` is declared ("the site's ambient reads", D7 §3 amended) and read by `Engine._zone_constraints`, every `LoadCtx` and `SlotClose`; `runtime._inputs` never sets it. A zone with no outdoor temperature ranks a pump at its curve's lowest anchor (`ZoneSource.cop_at`, D4 §5.14's pessimism): the house's curve gives 1.8 at −15 °C, under `min_cop` 2.0, so both pumps were excluded on every tick of the week. At 6 °C the curve gives ≈ 3.7: 0.24 NOK/kWh of heat against 0.88 for a floor.

**Cause, second half.** `weather.get_forecasts` answers through `WeatherEntity._convert_forecast`, which pops `native_temperature` and writes `temperature` in the unit system's unit. The source read `native_temperature`, dropped every entry, and D10 had no weather on any day since install. The house has `weather.hjem` (met.no, hourly): the live call of 12 Oct 08:0x returned 48 entries keyed `temperature`, none `native_temperature`. The planner's store contexts, `heat_capacitor`'s preheat gate and the baseline's β all ran without it.

**Change.** The source reads `temperature` and converts to °C. The runtime hands the tick the first bound `outdoor_temp` by load id (here heat pump 2's `sensor.ac_2_etasje_outside_temperature`), and the engine falls back to the forecast's hour, D10 §5.4's own order. Tests: `tests/providers/forecasts/test_weather_entity.py` (the response shape, imperial), `tests/runtime/test_outdoor_input.py`, `tests/core/engine/test_zones.py` (blind, measured, forecast).

**What the house will do differently.** In "2 etasje" heat pump 2 carries the zone and kjøkken, stua and TV-stua are shed `zone_substituted` while it is cheaper per kWh of heat; bad 2. etasje is `never_substitute`. In "1 etasje" heat pump 1 replaces inngang - the pump the owner says misbehaves. If it can't hold the room, inngang's comfort floor still wins (precedence: comfort floors > plan), but the owner may want heat pump 1 out of that zone until it follows.

| Decision | For the alternative | Against | Decision |
|---|---|---|---|
| Where the tick's outdoor comes from | *A site-level "outdoor sensor" in the flow:* one obvious place, independent of which appliance binds one | a fourth question for every household, and the heat pump already asks it; D10 auto-detects the weather entity | the bound sensor, else the forecast hour |
| Sensor before forecast | *The forecast first:* met.no isn't heated by the sun on a wall or by a defrost cycle | a pump's COP is the air at its own coil, which is what its sensor reads; D10 §5.4 already orders them this way | sensor first |
| One temperature per site | *Per source:* each pump's own sensor for its own `ZoneSource` | one house, one outdoor; a pump's own decisions already use its own sensor (`heat_pump.outdoor_c`) | one per site |

---

## 4. L2 - one cut became four (D-0718)

**Cause.** At an hour's start the allowance drops from the end-of-window sprint to ≈ 9.2 kW, and the walk honestly cuts the charger once (here 28 → 19 A, about 2 kW) - the seam D-0712 left deferred. The charger takes ≈ 6 s to follow and the meter reports every 10 s. On the next tick the charger is settling, so D3 counts it at its **commanded** 4 370 W (INV-18) while the grid still carries its 6 440 W: `uncontrolled_w` grows by 2 070 W, the residual shrinks by the same, and the walk cuts to 12 A. Then again to the 6 A floor. When the meter catches up the grant is back at 6–7 kW within 20 s, but the charger climbs at `step_up_a` 4 A per 60 s settle: 6–25 minutes at a fraction of its rate, every loaded hour. At 01:00:25 on 12 Oct the grid read 8 239 W against a 9 247 W allowance: there was no deficit at all.

The trim already knew: "a load written within its settle window is skipped (the deficit may be our own write)" (D6 §5.5). The walk didn't.

**Change.** While a modulating load's own write settles and the device still draws more than it was told, the walk adds `measured − commanded` - its own old draw, counted as uncontrolled under INV-18 - back to that load's room. Never on a blunt stage; its plan, the surplus it follows and the hard limits still bind, and a stop stays a stop. A first build held the load at its commanded watts instead; it also overrode legitimate cuts once the device had followed, and `pv_no_battery_ev_waits` charged 0.21 kWh at night. Test D6 §9 32.

| Decision | For the alternative | Against | Decision |
|---|---|---|---|
| Fix the estimator, not the walk | *Count a load at its measured draw until the device can have answered (≈ 10–15 s), at the commanded one after:* that is the physically right `uncontrolled_w`, and it would also stop other loads being denied for those seconds | it rewrites INV-18 (owner's call), and needs a per-kind response time nobody has measured; BLE chargers and Z-Wave relays differ by an order of magnitude | the walk; the estimator is worth a measured follow-up |
| Freeze `uncontrolled_w` while anything settles | *One rule for everyone:* no load is judged on a number our own write distorts | during a ramp the charger settles 60 s out of every 60 s - the house would be blind to an oven most of the night | no |
| Cap the seam | *Remove the first cut too:* no cut, no cascade | D-0712 tried three caps; each moved energy into the next window (the car at 79.93 % at 4.7 kWh, `legionella_expensive_week` over its ceiling) | stays deferred |

---

## 5. L3 - an absent car's deadline at risk (D-0720)

**Cause.** With no car the EV's demand is `wants: False` but keeps `required_kwh` (6.7–7.7 kWh, from the car's own SoC over the internet) and its departure, and `deadline_fill` plans for it - D-0709 kept that on purpose ("the plan for an absent car still needs it, and the dashboard shows it"). The plan can't run, falls behind, and is uncovered by about 05:00 every departure morning; `Engine.plan` fired on `site_plan.uncovered` alone. The deviation counter was already right (D-0709): the month's 3 EV misses are not these nights.

**Change.** `uncovered` is kept to loads whose demand wants. The `uncovered:<load>` edge stays `0` while the car is away, so plugging in a car whose plan is short fires the event then. Test D7 §9 33.

| Decision | For the alternative | Against | Decision |
|---|---|---|---|
| Gate the event, not the plan | *Plan nothing for an absent car:* no plan, no false signal, nothing reserved | D-0709's own rejection - the dashboard and the next plug-in need it | gate the event |
| Mark the plan covered | *One place:* `covered` means "nothing to warn about" | it lies on the dashboard about a car that needs 7.7 kWh | no |

---

## 6. L4 - the ladder flips at 85 % (D-0721)

**Cause.** De-escalation needs two clean ticks, `P_total < P_allow − 300 W`. At 85 % of the ceiling the allowance is ≈ 1 kW above the house, so every tick is clean and the projection alone decides, with the same 0.85 both ways. Stage 1 itself costs little (rotation, the PI's binding flag) but the event and the dashboard's "Styringsnivå" churned.

**Change.** On the way down the thresholds are 2 % lower: in at 85 %, out under 83 %, out of the trim under 98 %. The way up and the two clean ticks are unchanged. Test D6 §9 33.

| Decision | For the alternative | Against | Decision |
|---|---|---|---|
| A projection band | *A longer EMA τ:* smooths the cause, not the symptom | slows the way up, which is the half that defends the ceiling | band |
| 2 % rather than 3 % | *3 %:* a wider margin against noise | the house's flips sat between 84 and 85 %; at 3 % D6 §9 9's 82.5 % would no longer leave | 2 % |

---

## 7. L5 - a charge announced every quarter (D-0722)

**Cause.** D-0714 announces a plan when its energy moves more than max(0.5 kWh, 25 %) from the last announced. A charge or a tank block in progress has less left every quarter: 117 EV and 77 tank events in eight days, most of them a running plan reporting its own progress.

**Change.** A plan that reads `now` before and after, with the same mode and coverage and less energy left, updates the stored edge silently, so a later rise is measured from what was really left. Test D7 §9 30d.

| Decision | For the alternative | Against | Decision |
|---|---|---|---|
| Silence a running plan's decrease | *Raise the share to 50 %:* one number, fewer events everywhere | it also hides a requirement that really changed, the event's purpose | silence progress only |

---

## 8. L6 - the charger enabled at every restart (proposal, owner's decision)

**Cause.** INV-26 releases every load on HA stop, and the charger's release is "enable on" (what it held before powerplan's first write). On a restart HA is down ≈ 55 s and the first tick parks it again. Twice on 12 Oct; a car plugged in at the time could start at full current in a day hour.

**Proposal.** On `EVENT_HOMEASSISTANT_STOP` with `hass.exit_code == RESTART_EXIT_CODE` (a restart, not a shutdown), skip the stop-time release: the startup release and restore that follow undo nothing the stop would not, and the device keeps powerplan's last decision for the minute it is gone. A real stop, an unload and a removal still release. `HomeAssistant.async_stop` sets `exit_code` before it fires the stop event (`core.py`), and `homeassistant.restart` passes 100; a restart from outside (`docker restart`, SIGTERM) reads 0 and still releases, the safe default.

| Decision | For the alternative | Against | Decision |
|---|---|---|---|
| Keep INV-26 as written | *Ownership:* if HA never comes back, the household's car must charge; a restart can hang | `exit_code` 100 is HA telling us it will come back; a hang after a restart is the same risk as a crash, which INV-26 already can't cover | owner decides; nothing built |
| Release only the charger's current, not its enable | *Half a hand-back:* the car may charge, at the watchdog's limit | the house's charger has no separate watchdog, so the limit it held before powerplan is 32 A | no |

---

## 9. L7 - no defect (no change)

| Observation | Why it is right |
|---|---|
| Gulvvarme stua, 0.01 kWh since 25 Sep | the floor never went under its 22 °C target all week (min 22.3 °C); heat pump 2 heats the room |
| kjøkken 0.54 kWh | min 21.4 °C, drew up to 467 W when under target |
| bad 2. etasje granted 320 W, measuring 0 W | powerplan wants at 0.4 K under 24 °C; the thermostat's own deadband hasn't closed the relay. *For a change:* 320 W reserved for nothing. *Against:* the relay may close any second, and an idle granted relay reserving its nameplate is D-0169's rule against the opposite failure. Keep |
| TV-stua shed (`zone_substituted`) and drawing 612 W | eco mode holds its own 20.5 °C eco setpoint and the floor read 20.6 °C; a shed relay thermostat in eco is a lower target, not off (D4 §5.5) |

---

## 10a. L8 - the losses were phantom sun (D-0723)

**Cause.** D11's headline for a heating load is the `day` reference: the day's own measured energy spread evenly over its slots, priced slot by slot, against what the load paid. A thermostat that draws evenly saves nothing by construction. The counterfactual may also eat the house's surplus, `sun_cap = max(0, export − import + Σ loads)`, at the sun price. With no production sensor and nothing exported that is the loads' own sensors minus the grid meter; at 8 kW a 2 % mismatch is 0.16 kWh a slot. The house's buffers of 12 Oct had it in 6 of 40 slots, offered to all nine shadows at once, at 0 NOK (no export curve). The real loads paid full price. Hence a reference of 0.811 NOK/kWh where an even day costs 0.831, and a loss on every heating appliance.

**Evidence.** Recomputed from each power sensor and `sensor.nygardsvegen_6_strompris_na`, 5–12 Oct: inngang 17.0 kWh, paid 14.16 NOK, even spread 14.17 → **+0.01**; heat pump 2 46.8 kWh, 38.57 against 38.93 → **+0.36**. The export register: 0 kWh every month since Jan 2025.

**Change.** No production reading and no export in a slot ⇒ no surplus on either side. Test D11 §9 24 (24d): test 24's night floor with its sensor 2 % over the meter books 0.56 NOK as before; the old code booked 0.47.

**What it does not change.** The heating loads' real effect is small either way (L9); the EV and the tank were understated by the same phantom.

---

## 11. L9 - powerplan leaves heating alone when it can't pay (D-0724)

**What the house shows.** Inngang's 10 Oct: Heating mode at the device's own 23.5 °C almost all day, so the slab heats whenever it is under 23.5 - 10:45–14:15 and 18:15–21:15 at the day price - exactly as without powerplan. "Bank" on a `mode` floor is the device's own thermostat: it can't hold a slab above its own setpoint. The one real act is eco 21:15–22:00 and Heating at 22:00, which moves the evening's heat 45 min into the night rate (≈ 0.5 kWh × 0.14 NOK ≈ 0.07 NOK a day per floor) and starts a recovery that overshoots to 24.6 °C. Heat pump 2: 21.0 °C all week except a 0.5 K dip 21:30–22:15. The house's heating loads used 33–39 % of their energy at night (8 of 24 h is 33 %).

**Change.** The owner: "if powerplan can't achieve savings it shouldn't do anything". A worth-it gate in `plan_all` (D5 §5.9a): a `heat_capacitor` or `best_save` price plan for a slab or room store must earn 4 % of its energy's cost by its timing - D11's own `day` yardstick, ex ante, counting only energy the device can draw (a `mode` device's bank is not) - or the load gets `PlanMode.NONE`: no pacing writes, the device at comfort on its own thermostat. It comes back at 6 %. Capacity, comfort floors and hard limits are untouched: the allocator still sheds a floor for the ceiling (precedence item 2, INV-1), which is where the 188 NOK a month is. On the house's curve a `mode` floor reads 2.1 % and `best_save` 0.3 %: hands-off. A setpoint store banking the night, and every plan on a volatile NO3 day, keep theirs.

| Decision | For the alternative | Against | Decision |
|---|---|---|---|
| Keep pacing every thermal load | *Small savings add up:* 6 floors × 0.07 NOK × 30 days ≈ 13 NOK a month; spot prices or a wider spread make the same plans worth more, and a gate that switches them off forgets the house's thermal behaviour | 13 NOK at best against a recovery overshoot that may eat it, relay and mode writes, a comfort dip at the evening coast, and a dashboard that says "shifted 21 kWh" for nothing a household would notice | gate it - the gate reopens by itself when the spread grows |
| Where the gate lives | *In each strategy:* `heat_capacitor` knows its own bank and coast | five strategies would each grow the same comparison; `plan_all` already has the curve, the plan's cost and `should_adopt` | `plan_all`, next to adoption (D5 §5.9) |
| Threshold | *A NOK figure per load and day:* the household thinks in kroner | it means something else in EUR, and a towel rail and a 3.6 kW floor would be held to the same figure; under Norgespris the most any heating plan can earn is 11 % | 4 % in, 6 % out, `SiteContext.worth_share` |
| Scope | *Every price plan, the EV and the tank too:* one rule | their hands-off is charging at plug-in and reheating at the day price, and both plans pay | thermal stores only |
| Hands-off for a `mode` floor that can't bank | *Make banking real:* write the device setpoint above comfort when the plan banks (the `setpoint` kind does this) | the device's own setpoint is the household's comfort target (INV-27); raising it is a different control kind the subentry would have to choose | out of scope; the gate covers it |

---

## 10. Verification

Fast suite and the touched modules: see the branch. Scenarios recomputed against the change (`POWERPLAN_SIM_CACHE=off`): §10.1, filled by the run.
