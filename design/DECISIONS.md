# powerplan - decision log

Choices the code forced that the HLD and the LLDs didn't make (PLAN §7 dec. 6). One entry per decision: what, why, which sections it affects, and the rejected alternative in a line. Newest last.

Numbering is stable; an entry is superseded, never edited away.

---

### D-0001 · HA floor is 2026.3.0

`hacs.json` declares `homeassistant: "2026.3.0"`, the first release that requires Python 3.14 (its `pyproject.toml` says `>=3.14.2`, 2026.2.0 still allowed 3.13.2). CI runs the floor and the latest stable. Affects HLD §4, D8 §5.12.
**Rejected:** 2026.2.0 to catch a few more laggards - it doesn't require 3.14, so we'd be back to supporting two Pythons.

### D-0002 · `requires-python = ">=3.14.2"`

`uv sync` can't resolve `>=3.14` since `homeassistant` pins `>=3.14.2`, so the number is HA's and not ours.
**Rejected:** keeping `>=3.14` and constraining the resolver with `tool.uv.environments` - it hides a real floor behind a resolver setting.

### D-0003 · `holidays>=0.84` is a range, not a pin

D8 §5.12 wanted `holidays==` whatever core pins for `workday`, however that version moves between the floor and latest. hassfest only enforces `==` for core integrations, and whatever the running core installed satisfies `>=0.84`.
**Rejected:** `==0.84` - pip would downgrade `holidays` under a newer core and break `workday` for the user.

### D-0004 · INV traceability is a shrinking allowlist

`test_inv_traceability` runs over the safety set from HLD §7.5 and fails both ways: an INV without a `@pytest.mark.inv` test that isn't listed in `tests/core/invariants/traceability_pending.txt`, and an INV that has a test but is still listed. The list starts with all 45 safety invariants. The only way to keep CI green is to delete the line in the same PR as the test, so the list can only shrink (D9 §5.6, §9 1).
**Rejected:** a plain failing test until phase 0 is done - honest, but a suite that's red by design teaches everyone to ignore red. `xfail` hides the day an INV gets a test.

### D-0005 · `hacs.json` lives at the repo root

HACS reads it from the root, next to `manifest.json` it's simply not found (D8 §5.12).
**Rejected:** keeping every packaging file under the integration dir - tidier, and wrong.

### D-0006 · `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)`

An integration with `async_setup` needs a `CONFIG_SCHEMA` or hassfest warns. powerplan is config entries only, and `async_setup` exists only to register actions once (PLAN §7 dec. 8).
**Rejected:** registering actions in `async_setup_entry` - re-registers per entry, which HA's `action-setup` rule forbids.

### D-0007 · `core/model.py` leaves out fields whose types don't exist yet

The ten types HLD §5 names are there. `Demand.urgency`, `Demand.comfort`, `PlanSlot.desired_state` and the per-domain `Snapshot` sections come with the WP that writes their types. `Direction` sits in `model.py` since `PriceCurve` does. Collections are `tuple`, a frozen dataclass crossing a layer has to be immutable all the way down.
**Rejected:** the whole `Snapshot` with `Any` sections now - `Any` in a strict core is a lie that survives until someone removes it.

### D-0008 · mypy needs an explicit package base

`mypy_path = "."`, `namespace_packages` and `explicit_package_bases`, and the strict override is keyed on `custom_components.powerplan.core.*`. `custom_components/` has no `__init__.py`, so without it mypy names the modules `powerplan.core.*`, the override never matches, and `core/` runs at standard strictness while reporting success.
**Rejected:** strict everywhere with opt-outs for the HA modules - inverts the point, `core/` is what's held to the higher bar.

### D-0009 · ruff doesn't format Markdown, confusables allowed

`[tool.ruff.format] exclude = ["*.md"]`, and `allowed-confusables` lists the typographic characters the docs use (`–  −  ·  ×  ≥  ≤  ε  σ  Σ  Δ  °`). ruff reformats fenced Python in Markdown and the LLD sketches aren't valid Python on purpose.
**Rejected:** rewriting the sketches as formatted Python - checkable, but harder to read than the code they specify.

### D-0010 · `capture_fixture.py` matches by name over `GET /api/states`

`--url`/`--token` default to `$POWERPLAN_HA_URL`/`$POWERPLAN_HA_TOKEN`, `--device` is a case-insensitive substring of `entity_id` or `friendly_name`, and location and tokens are redacted. The REST API has states but no device registry, and a fixture is about an entity's attribute shape anyway (D9 §3, §6).
**Rejected:** the WebSocket API with real device ids - exact, however an async client and auth handshake for a throwaway tool.

### D-0011 · Coverage floors configured, not gated yet

`[tool.coverage.*]` is in `pyproject.toml` with the floors in a comment, CI runs `--cov` for the report only. The scaffold would fail the 85 % floor on its placeholders alone; the gate comes once the core has code to cover (D9 §5.8).
**Rejected:** gate from day one - means writing tests for code that's meant to be thrown away.

### D-0012 · The CI floor leg installs on top of the lock

Both legs run `uv sync --locked`, the floor leg then installs its `pytest-homeassistant-custom-component` version over it. `uv.lock` holds one `homeassistant` and the fixture package pins its whole HA stack, so this is deterministic (D9 §5.8).
**Rejected:** two conflicting dependency groups in `tool.uv.conflicts` - splits `dev` into three groups for one CI axis.

### D-0013 · `codeowners` and URLs point at `jkaberg/hass-powerplan`

`manifest.json`, `pyproject.toml` (`project.urls`) and D8 §5.12 name `@jkaberg` and `github.com/jkaberg/hass-powerplan`.

### D-0020 · Where D3 §3's unplaced names live

`MeterSample` in `readings.py`, `ControlledView` in `decompose.py`, `MeterSnapshot`, `PendingClose` and `reconstruct_windows` in `window.py`, `AnchorKind` in `health.py` (re-exported by `window.py`). `stats.py` has `trapezoid_kwh`, the `Trapezoid` accumulator lives in `loads.py`. The import graph has to stay acyclic, and `MeterHealth` carries an `AnchorKind`, so the enum can't live in `window.py`. Every §4 type crosses a layer, so the package exports them (D3 §3).
**Rejected:** one `types.py` for every shared dataclass - no cycles, but it splits each algorithm from the type it owns to solve a cycle with one edge.

### D-0021 · `WindowState` is frozen, and the store section is one object

`WindowState` is `frozen=True, slots=True`, `schema` moves last, `pending_closed` is a tuple, `cadence_samples` and `closed_unacked` become fields, and the wait in §5.5 is a frozen `PendingClose` in `closing`. One frozen object round-trips atomically, so a save can never write a new anchor next to a stale pending list (D3 §4, §7).
**Rejected:** mutable, with `sample()` assigning in place - fewer allocations, however the persisted state is exactly what must not be half-updated when a tick raises.

### D-0022 · `used` is the integral snapped onto register evidence; a latched register only anchors

`used = e_integral` since the boundary, snapped to the register whenever a reading's effective time falls inside the window. Effective time is receipt time for `interpolated` and `meter_window`, and the window start for `latched` - so a latched meter never snaps, the report anchors the window and closes the previous one. Taken literally, D3 §5.4 step 6 reads `used = 0` for the whole window on a latched AMS meter, which is the staircase that pinned the old controller to stage 4 all night (D3 §5.4, §5.5, §5.7).
**Rejected:** the register drives `used` in every mode - only holds for a register reporting many times per window.

### D-0023 · σ is time-weighted with a Bessel correction

`RollingStd` weights each sample by the interval it held and multiplies by `n/(n−1)`, so σ doesn't depend on how often the meter reports, and it reduces exactly to effektstyring's `_stdev` on uniform intervals (D3 §5.9).
**Rejected:** unweighted σ - a meter reporting on change bursts when the house is busy, which is exactly when σ matters.

### D-0024 · `latched` until a cadence is known, and "overdue" is cadence + grace

`detect_register_mode(None, …)` returns `latched`, and a register has stopped when its age exceeds `cadence + register_grace_s`. Waiting for the report is INV-13's behaviour, and guessing wrong costs one cadence that cancels across windows (D3 §5.3, §5.4).
**Rejected:** "unknown" and close nothing for five intervals - leaves a five-hour hole in D2's peak table after every install.

### D-0025 · A latched report is this window's boundary value within half a window

In `latched` mode a reading arriving while the window has no observed anchor is its boundary value, unless one already landed in this window or it's more than half a window late. The property test found the narrower ±25 % rule this replaced over-counting a day by 10.6 % (D3 §5.5).
**Rejected:** deciding from the integral (compare the register delta with the last window's integral) - uses evidence, however it needs one more sample of state and the timing rule is exact for every meter in HLD §8.

### D-0026 · `WindowMeter.ack_closed(upto_utc)`

Drops the closed windows before `upto_utc` from `pending_closed`. D3 §7 only clears them when D2 has recorded them - a day lost across a restart was invisible in the old setup - and §3 had no way to say so. `LoadMeter.ack()` already does the same (D3 §3).
**Rejected:** D7 rebuilds `WindowState` with the list trimmed - puts metering arithmetic in the engine.

### D-0027 · Folded into D-0031

### D-0028 · Register cadence is the median over an *even* number of intervals

The oldest interval is dropped when the count is odd. A once-per-window register with receipt jitter `j` gives intervals alternating `window ± j`, and the middle of an odd sample is one of the extremes - at five and seven intervals a 233,5 s jitter read as a 1 133,5 s cadence, the mode flipped to `interpolated` and the window billed its first 233 s twice. With an even count the jitter cancels (D3 §5.3).
**Rejected:** mean over the observed span - unbiased for any jitter, however two dropped reports in eight push it past the ±25 % test where a median absorbs both. Hysteresis on the mode would have hidden the bug, not fixed it.

### D-0030 · Curve statistics are methods on `PriceCurve`

`price_at`, `slots_between`, `spread`, `mean`, `is_flat`, `coverage_h` and `resample` live on `PriceCurve` in `core/model.py`. `spread`, `mean` and `is_flat` take the local `tzinfo` (a day is a local day) and return `Decimal` (D1 §3).
**Rejected:** functions in `core/pricing/curve.py` like effektstyring had - splits one concept over two files, and every consumer imports D1 to ask a curve about itself.

### D-0031 · Parent-relative imports allowed inside `core/`

ruff's `TID252` is off for `custom_components/powerplan/core/**`, so `core/pricing/model.py` says `from ..model import Slot`. `core/` is meant to be liftable as `powerplan-core` (HLD §5, PLAN §7 dec. 3), a relative tree moves as a directory. Everywhere else the HA convention holds.
**Rejected:** absolute imports - no per-file ignore, but it turns the eventual split from a move into a rewrite.

### D-0032 · `never_on_the_hour` snaps to the boundary + 90 s

D1 §5.1 said "shift +90 s", however `HH:59:00` + 90 s is `HH+1:00:30`, still inside the ±60 s band. Snapping is idempotent and outside the band from either edge (D1 §5.1).
**Rejected:** loop "+90 s" until outside - the result depends on where in the band it started.

### D-0033 · The fetch schedule is pure, every fire time filtered

`next_fetch_at`, `backoff`, `next_retry_at` and `next_hole_check_at` take a `random.Random` the caller owns and all return times through `never_on_the_hour`. INV-6 covers every computed fetch time, so it belongs in `core/` where a test can hit it 10 000 times (D1 §3, §5.1).
**Rejected:** module-level `random` with jitter in the runtime - the invariant would live where it can't be tested.

### D-0034 · A hole in the horizon raises `CoverageError`

`build_curve` raises when the forecaster chain leaves any of `[now, now + horizon]` uncovered. A chain without a terminal forecaster is a config error and should be loud (D1 §5.3, INV-5).
**Rejected:** return the short curve - an misconfigured chain stays invisible as long as real prices happen to reach the horizon.

### D-0035 · Staleness is marked in `compose`, `carry_known` is the identity

`build_curve` decides `KNOWN`/`STALE` from each `RawSlot.fetched_at`, since `Slot` has no `fetched_at` and only `compose` still has the raw rows. `forecasters.base.chain(*parts)` runs D1 §5.5's chain.
**Rejected:** `fetched_at` on every `Slot` - provenance on every slot of every curve for one boolean known once.

### D-0036 · `tou_schedule` borrows D2's `TimeFilter` until D2 exists

D1 §5.4's `tou_schedule` is built on D2's grammar, which lands later. The copy is structurally identical and is replaced by an import (D-0050).
**Rejected:** writing `core/tariffs/grammar.py` ahead of its WP - lands nine more types that would be missing or invented.

### D-0037 · D1 defines its own registry `Schema` for now

`Field`, `FieldKind` and `Schema = tuple[Field, ...]` in `core/pricing/model.py`. Every LLD writes `schema: ClassVar[Schema]` and none defines it. The shared one belongs to the WP that first needs two of them (D1 §4).
**Rejected:** shared in `core/model.py` right away - a schema designed against one domain is a guess at the other five.

### D-0038 · The synthesised floor's energy constant is total minus the grid charge

`Synthesised` uses the duration-weighted mean of `total − grid_energy` over recent known slots (`energy_default` when nothing is known) and adds the grid charge itself. Raw spot alone prices the tail ex levy and VAT, 10–15 øre/kWh under the known head, and a cheap forecast pulls every flexible load into it (D1 §5.5).
**Rejected:** running the modifier chain over the synthesised slots - applies `tou_schedule` and VAT twice.

### D-0040 · Simulators before the engine that uses them

The device simulators in `tests/sim/` come before the scenario runner. Every load, strategy and allocator change is tested against device behaviour, and without the simulators each would write a mock only to delete it later (PLAN §9, D9 §3, §4).
**Rejected:** engine first so the simulators copy D4's command shapes - the stronger argument. Contained by giving `tests/sim` its own four frozen shapes (D-0041), so one adapter absorbs D4 when it lands.

### D-0041 · `tests/sim` owns `Env`, `Command` and `Reads` and never imports `custom_components`

`tests/sim/base.py` defines `Env`, `Command`, `Reads`, `SimLoad` and `SimSource[T]`, the runner adapts to `core.model` and D4. A simulator also needs things no core type has (ground and mains water temperature) and shouldn't need a config subentry to exist (D9 §3, §4).
**Rejected:** use the core's types directly - makes the simulators depend on the layer they test.

### D-0042 · A generator is a pure function of `t`

`weather.py`, `prices.py`, `uncontrolled.py` and `household.py` expose `at(t)` (and `slots(day)`, `events(day)`, `day(d)`), randomness from `derive_rng(seed, *key)` keyed on the day or slot. The planner looks ahead, and a stepped generator with a running RNG answers differently depending on how many look-aheads came first, which breaks D9 §9 8's byte-identical runs.
**Rejected:** one `step()` protocol for everything - a price curve isn't a reading.

### D-0043 · RNG keys are hashed with blake2b, not `hash()`

`hash()` is salted per process for `str`, and every generator key has one. `tests/sim/test_base.py` pins one derived value so a change is visible.
**Rejected:** `PYTHONHASHSEED=0` in CI - determinism would be a property of the environment, not the code.

### D-0044 · The heat pump's COP is a mechanism anchored on D4's table

`cop_at(outdoor_c) = η(T_out) × Carnot(T_out, 35 °C)`, η linear and fitted through D4 §6.4's +7 °C (3.8) and −15 °C (1.8), capped at 5.5 (Toshiba's published SCOP). Interpolating D4's table would make the simulator and the planner agree by construction. The anchors are effektstyring's measured curve, a weaker source than D9 §2 asks for, marked in the file.
**Rejected:** interpolate D4's table - exactly what the product assumes, which is the reason not to.

### D-0045 · Defrost has a dip and a spike

~20 s of valve swing at standby power, then the compressor at rated power while outlet air runs 5 K under the room, then 2 min recovery. D4 §5.14 detects "power up while outlet falls" and a naive watcher sees the dip first. All timings `assumed`.
**Rejected:** one flat "rated power, no heat" phase - gives neither signature.

### D-0046 · The tank is two fixed layers that merge on buoyancy

Hot upper half, cold lower half, 15 W/K between them, element and thermostat sensor in the bottom, full mixing whenever the bottom would exceed the top. Fixed volumes keep the energy balance exact. `TOP_FRACTION` and `MIX_UA_W_PER_K` are `assumed` until a two-sensor capture from the reference house.
**Rejected:** a moving thermocline - closer to reality, however conservation becomes an approximation.

### D-0047 · A dropped EV session reports `awaiting_start`, never `completed`

D4 §5.11 latches "session done" on `completed`, so a charger that said `completed` after a drop the controller caused would make it park the car for the night on its own mistake.
**Rejected:** `completed` since the session did end - it mixes up "the car is full" with "we broke the pilot".

### D-0048 · `Reads.power_w` stays truthful while the link is down

`BleChargerSim` returns `available=False` and `status="offline"` during a link drop but keeps the real `power_w` and `amps`. The provider has to see it unavailable (INV-15), however the car still draws 16 A and the meter must see that.
**Rejected:** `power_w = 0.0` while offline - hides a 3.7 kW load from the meter, blindness injected in the wrong place.

### D-0049 · Every numeric constant in `tests/sim` has a `SOURCES` entry

`SOURCES` maps each UPPER_CASE number to a URL, a document section or `assumed: <what would replace it>`, and `tests/sim/test_sources.py` fails on a missing, stale or incomplete entry. 123 of 223 are sourced (D9 §2, PLAN §6 R2).
**Rejected:** a `Param(value, unit, source)` wrapper - every formula would read `RHO.value * CP.value`.

### D-0050 · `TimeFilter` lives in D2's grammar, D2 has its own `HolidayCalendar`

`TimeFilter`, `HolidayMode` and `_within` move to `core/tariffs/grammar.py`, re-exported from `tou_schedule` (closes D-0036). `grammar.py` declares an one-method `HolidayCalendar` protocol so D2 doesn't import D1. `months`/`weekdays` stay tuples (D2 §4, D1 §3).
**Rejected:** `HolidayCalendar` in `core/model.py` - HLD §5 lists what goes there.

### D-0051 · A step boundary is inclusive upward

`StepTable.index_for` takes the first step whose `upper_kw` the metric is strictly below, so 10,00 kW is in 10–15. Verified in effektstyring's `month.py` against the DSO's figures and a year of bills. No rounding first, `round(9.996, 2)` promotes a whole step (D2 §5.3).
**Rejected:** `≤` as D2 §5.3 first said - mis-bills every boundary in the household's favour, which is how you quietly lose money.

### D-0052 · The mean-top-n closed form needs a divisor and an infeasible case

`d × T − Σ(top d−1 others)` with `d = min(n, other_days + 1)`, 0 when the metric already exceeds the target, and `slack = max(feasible(T), feasible(metric_now))`. The original sketch disagreed with the bisection with fewer than `n` days, a month already over, and a day outside the top `n` (D2 §5.4, §5.6).
**Rejected:** always bisect - 20 metric evaluations on the hot path, and the closed form is what a household can check by hand.

### D-0053 · `schema.json` is enforced by a small stdlib validator

`presets/loader.py` interprets the keywords the schema uses (about a hundred lines) and adds the rules a schema can't express. `core/` has no third-party deps and `jsonschema` isn't in HA's set (D2 §2).
**Rejected:** `jsonschema` in CI only - a community preset from HACS would be unvalidated at load, which is the moment that matters.

### D-0054 · `verified: null` requires `assumed`

A preset version without a `verified` date is refused unless it says what's assumed. `no/tensio`'s next-year version (the current steps × 1,06, nothing published yet), `no/elvia`'s open step above 20 kW and `no/generic-top3`'s fees ship marked (D2 §6).
**Rejected:** ship next year only once real numbers exist - the benchmark year crosses new year and INV-52 would go untested until it matters.

### D-0055 · `auto` holds last period's step while the metric is `partial`

With fewer than `n` days `auto` defends the step containing `max(metric, last period's metric)`. Otherwise one 0,4 kW hour on the 1st makes `auto` defend the 0–2 kW step until the month's first real peak (D2 §5.5).
**Rejected:** `max(current, previous)` always, like `Linear` - the ratchet INV-11 exists to avoid.

### D-0056 · `marginal_cost` is measured from the metric-neutral point

It prices the fee difference with this window at `neutral + kw_over × weight`, `neutral` being the largest value that can't move the metric, so `marginal_cost(0)` is 0. A `ContractedPower(trip)` site prices at 0 here, the trip limit is precedence item 1 and D6 sees it via `limit_now_w` (D2 §5.7).
**Rejected:** add the projection to the signature - D5, D7 and D11 use the protocol too, and a curve that moves with an projection is harder to publish.

### D-0057 · Types D2 §4 named but didn't define

`Period(start, end, key)` and `Evaluator.period(now)`, `TariffVersion` and `TariffSpec` in `grammar.py`, `Ceiling.slack_kwh` in kWh as its name says, the free ride aims ε under `today_max` (effektstyring's `budget.py`), and `MonthRec` stores metric and version id but not the fee (D2 §3, §4).
**Rejected:** `bill` takes two bounds - D11 passes the same period to two bills and the key files them.

### D-0058 · A window that weighs nothing makes no day entry

A window weighing 0 is stored as `WindowRec` but creates no `DayRec`, and a day is rebuilt from its windows on every recording. Under `mean_top_n` a 0 kW entry would count in the divisor, and the rebuild makes late windows, duplicates and re-seeds idempotent (D2 §5.1).
**Rejected:** record zeros and ignore them in the metric - a real 0 kW day would look like an ineligible one.

### D-0059 · A period spanning two versions is billed pro rata

`bill` splits the period at each `valid_from`, prices the metric under each version's own table and weights the fees by each segment's share of the period. The level is classified on the version in force at the period's end, so a December bill computed in January isn't priced on January's table. Norwegian versions start on 1 January, so this only bites when a DSO changes mid-month. Affects D2 §5.10.
**Rejected:** pricing the whole period on the version in force at its end - simpler, but a mid-month change would bill a full month at the new price, which neither version says.

### D-0060 · `Mode`, `Urgency` and `ComfortState` live in `core/model.py`

D4 §3 puts them in `core/loads/base.py`, but `base.py` imports the gate and the gate needs the mode for rows 1-2 of D4 §5.10, which is a cycle. D6, D7 and D11 read them too. They sit next to `Demand` and `core/loads/base.py` re-exports them. `Urgency` is an `IntEnum` because the order is the point.
**Rejected:** keeping them in `base.py` and passing the gate the mode as a `str` - literal to D4 §3, but a `str` mode is the row-1 bug `StrEnum` exists to prevent.

### D-0061 · The per-load read/write vocabulary sits in `kinds/base.py`

`Role`, `Reads`, `Value`, `Write`, `Command`, `Hold`, `KindCtx` and friends are declared in `core/loads/kinds/base.py`, the gate's types in `core/loads/gate.py`, and `core/loads/base.py` re-exports both. The kinds and the gate both need the vocabulary and both sit below the `Load`, so it's declared at the bottom. `providers/profiles/` imports `Role` from here too. Affects D4 §3, §4.1, §4.5.
**Rejected:** a new `core/loads/vocabulary.py` - an obvious home, but a module D4 §3 doesn't draw.

### D-0062 · A store model holds no reads

`StoreModel` drops D4 §4.3's `level(reads)`: the device type reads the level and passes it to `required_kwh(level_now, …)` and `coast_hours(level_now, …)`. Which sensor a level comes from is the type's answer (floor, air or both), so a store that read it would make pure physics depend on the binding layer.
**Rejected:** keeping `level(reads)` and giving every store the sensor mode - copies one answer into four models, and D11's shadows would implement a method they can't answer.

### D-0063 · The tick contract threads the state, and `TypeLogic` is its half of `DeviceType`

`observe`, `apply`, `release` and `restore` take a `LoadState` and return a new one with their result. `Load` is frozen and the core is pure (INV-2), so the latches D4 §5.11 sets during a read have nowhere to go but a returned state. `TypeLogic` (`demand`, `latch`, `kind_ctx`) is the tick half of `DeviceType`, which breaks the import cycle between the `Load` and its type. Affects D4 §4.6, §5.1.
**Rejected:** a `Load` with mutable state, as the pyscript drivers had - the engine would no longer be a function of `(state, inputs)` and the backtest couldn't replay.

### D-0064 · `ApplyResult.action` gains `held_suppressed`

An eleventh action for a write the kind's own deadband swallowed (D4 §5.3: `|Δ| ≥ 2 A` or 60 s stale). It isn't `same`: the device holds 16 A and we decided 17, and the log line that explains why a grant didn't reach the charger has to say so. Affects D4 §4.1.
**Rejected:** reusing `same` with a different reason string - D8 and D9 switch on the action, and "already there" would be a false claim about the device.

### D-0065 · One settle window per kind, the longer of the two numbers

`GateState.verify_due` is both the read-back deadline (D4 §5.10 row 9) and the settle window row 5 holds an upward write inside. For `MODULATE` it's `max(settle_s, 30 s)`, 60 s in the reference house. A deficit measured inside our own write is our own write, so the longer window is the safe one. It costs one poll of latency on a deviation.
**Rejected:** a separate `settling_until` - two clocks to persist and keep in step, for a read-back 30 s earlier.

### D-0066 · The three gaps in D4 §6.1's tables

Room `other` takes the hall's pair (21 / 19 °C, priority 30), the covering defaults to `wood` (cap 27 °C) and the area to 10 m². INV-65 needs a default for every question. Wood is the conservative cap: a tiled floor held to 27 °C loses a degree, a wooden floor allowed 30 °C gets damaged.
**Rejected:** defaulting to `tile`, the first option - the wrong-on-wood default damages a floor, the wrong-on-tile one costs a degree.

### D-0067 · `ev` priority 10

D4 §6.2 gives the EV no priority. It goes below the floor loops' 30: a deferred kWh costs patience, not a cold room, so it's the first load the ladder trims and the only one a plan may stop outright.
**Rejected:** above the thermal loads because a departure is real - deadlines are D5's job (`deadline_fill`), and priority is only the tie-break among satisfiable loads (INV-1).

### D-0068 · A blunt reason buys past rows 6 and 7 as well

In `gate.decide()`, `command.blunt` passes the command interval and the dwell clocks like `urgent` does; row 3 still binds. Read literally, D4 §5.10 would let a 600 s politeness clock hold a main-fuse shed. A blunt reason is physical or contractual by definition (INV-36).
**Rejected:** relying on every kind to set `urgent` on a blunt command - makes the guarantee a property of four kinds instead of one gate.

### D-0070 · The holiday calendar lives in `core/pricing/holidays.py`

`CountryCalendar` wraps the `holidays` package inside `core/`, with the site's country and subdivision, the household's extra and removed days, a per-year cache and `NO_HOLIDAYS` as D1 §8's degradation. INV-2 forbids `homeassistant` under `core/`, not a pure third-party library, and its consumers (`TimeFilter.matches`, `day_type`) are deep in the composition. `holidays>=0.84` goes into `pyproject.toml` as well. Affects D1 §2, §3.
**Rejected:** a `providers/holidays.py` so `core/` imports only the standard library - an HA-facing adapter with no HA in it, injected through two layers.

### D-0071 · `same_weekday_profile` shifts the level, it doesn't scale it

The profile takes the median slot of each weekday and time-of-day bucket and adds one amount to every slot's energy component so the day's mean matches the last known day. A shift hits the mean exactly in `Decimal`, can't flip a negative hour's sign and keeps the day's absolute spread, which INV-8's hysteresis is measured in. Affects D1 §5.5.
**Rejected:** multiplying by `target / profile_mean` - keeps price ratios, but inverts a day with a negative mean and stretches spikes with the baseline.

### D-0072 · `cumulative_tier` adds a `tier` component, and may read the year

The selected step's price is an additive `tier` component, and `basis` picks month-to-date (default) or year-to-date. A modifier writes one component (INV-4), and Denmark's reduced electricity tax has a yearly threshold, which is why `PriceContext` carries `ytd_kwh_at` at all. The boundary belongs to the step above. Affects D1 §5.4.
**Rejected:** month-to-date only - leaves `ytd_kwh_at` unused and the LLD's own Danish example unexpressible.

### D-0073 · `day_type`'s fallback names a configured type

`DayType.fallback` is the key of one of the site's own day types (Tempo's `tempo_blue`), not a price. An unannounced day, or one with no configured rate, takes that type's rate. One rate per colour means the blue rate can't disagree with its own fallback. Affects D1 §5.4.
**Rejected:** a separate `fallback` rate - a second sub-form for a number already on screen.

### D-0080 · INV-3 admits one read-only response action

Nord Pool's core integration publishes no forecast attributes, only `get_prices_for_date`, registered `SupportsResponse.ONLY`. So `providers/prices/nordpool_action.py` may call it, and `test_single_writer.py` checks with an AST walk that every call there passes `return_response=True`. INV-3 exists for the write gate's rate limits and idempotency, which a price read can't bypass. Affects HLD §5, D9 §5.7.
**Rejected:** an own `pynordpool` client in a coordinator - keeps INV-3 literal, but holds a second client for prices the household's integration already has in memory, doubles the load on an endpoint congested on the hour, and adds a version pin that breaks against core's.

### D-0081 · Hand-written format fixtures live in `tests/fixtures/formats/` and name their source

Payloads written from upstream documentation (the HACS Nord Pool attributes, DST days, the response action's shape) go in `tests/fixtures/formats/`, each with a `source` key a test requires. `captured/` stays real dumps only. A DST day can't be captured on demand, and the reference house runs the core Nord Pool integration, so hand-written fixtures are unavoidable; they just mustn't pass as captured.
**Rejected:** installing the HACS integration in the reference house to capture it - a second price integration in a live install for a test file, and still no DST day.

### D-0082 · `MeterSource` is one `async sample(now) → MeterSample`

The protocol is `sample(now)` plus `entity_ids()`, not HLD §6.3's seven per-role getters. Seven getters could hand out readings from seven instants, which the window integral can't survive. `async` stays for the push sources to come, and `entity_ids()` is there because the runtime, not the source, subscribes (INV-3). Affects D3 §3, §4.
**Rejected:** seven getters assembled by the runtime - moves "one instant" out of the type system into every caller.

### D-0083 · The meter's own window start comes from `last_reset`, rounded to the second

`meter_window_start` is the meter-window entity's `last_reset`, rounded to the nearest second. The window meter compares it for equality, and HA's scheduling jitter (a few ms) would otherwise demote the anchor from `METER_WINDOW` forever. A window start sits on a 15- or 60-minute boundary, so rounding can't move it to another window. Affects D3 §4, §6.
**Rejected:** returning the runtime's own window start - always equal, which is the bug when the meter runs an hourly clock under a 15-minute window.

### D-0084 · `fetch_missing` and `RawStore` live in `providers/prices/base.py`

`fetch_missing` takes `PriceSource`s so it can't live in `core/` (INV-2), and it takes the site's `tz` explicitly since a provider mustn't read site settings behind the runtime's back. `RawStore` is a two-method protocol the D7 store satisfies. Scheduling (jitter, backoff) stays in `core/pricing/schedule.py`. Affects D1 §3.
**Rejected:** implementing the raw store here too - its schema, migrations and save policy are D7's.

### D-0085 · A provider scales from the unit the entity declares, on every read

`EntityReader` reads `unit_of_measurement` on every sample and scales by it (W/kW, kWh/Wh). An unknown unit is `UNAVAILABLE` with one WARNING. An integration update that moves W to kW is then simply correct on the next sample, instead of 1000× wrong until plausibility trips. Affects D3 §8.
**Rejected:** storing the unit at setup - breaks a working install until someone notices, and INV-17 says blindness freezes while a wrong number doesn't.

### D-0086 · `native_unit()` returns StrEnums; `carrier` and `direction` are instance attributes

`native_unit()` returns `EnergyUnit` and `Magnitude`, the StrEnums `to_major_per_kwh` already takes, not D1 §4's `Literal`s. `carrier` and `direction` vary per instance, since one `EntitySource` class serves both an electricity and a gas curve. Affects D1 §4.
**Rejected:** keeping the `Literal`s and converting at the boundary - two spellings of one closed vocabulary.

### D-0087 · An entity id is `FieldKind.TEXT`; no `MeterSource` registry yet

Schema fields naming an entity or a config entry are `FieldKind.TEXT` until the flow adds `FieldKind.ENTITY`. Meters get no registry: D3 §6's meter step is a fixed list of seven roles, not an open set, so there's nothing for a registry to render.
**Rejected:** adding `FieldKind.ENTITY` in `core/` now - one line, but only the flow consumes it.

### D-0088 · An `EntityFormat` is one `parse(state) → ParsedPrices`

An adapter returns intervals plus the currency, unit and magnitude it read, and `EntitySource` calls `normalise` once for every row. Several formats carry the unit in an attribute the user can change (HACS Nord Pool's `price_type`), so it's a property of the reading. `end` is optional: some rows publish only starts, and a slot's length is then the next start (INV-7). A `None` value is a hole, not a zero price. Affects D1 §2, §3.
**Rejected:** each adapter returning `RawSlot`s - thirteen copies of the currency check, unit factor, UTC conversion and dedup.

### D-0089 · `EventSource` gains `entity_ids()`; a silent entity announces nothing

The runtime can't subscribe to what it can't name, same as `MeterSource` (D-0082). `EntityEventSource` reads the state as the announcement and the attributes as its window (default: the local day, shifted by `day_offset`). An `off`/`unknown` state yields `[]`, not a revocation. Affects D1 §4, §5.6.
**Rejected:** treating `off` as revoking the last event - makes a stateless poll stateful, and `valid_until` already expires it.

### D-0090 · `storage.py` ships before the runtime

The store's sections, migrations and save throttle land on their own, with the runtime, triggers and lifecycle after. The store needs no engine, so D7 §9 10 and 11 test it against `hass` alone, and INV-14 is easy to get quietly wrong.
**Rejected:** building it with the runtime - one big diff where the throttle is a detail nobody reviews.

### D-0091 · `SiteStore` reads its own file; `Store` only writes it

`load()` reads `.storage/powerplan.<entry_id>` itself and validates the envelope. Unreadable JSON, or JSON with no envelope, is renamed to `.corrupt-<ts>`, the site starts empty with a WARNING, and the repair D7 §8 promises can be raised. `Store.async_load` would log at ERROR, raise a critical issue under `homeassistant` that names no integration, and return `None`, which looks like a first start. Affects D7 §2, §8.
**Rejected:** accepting HA's handling - powerplan would never learn its window was reset.

### D-0092 · Section migrators are registered in `storage.py`, and the store stamps `schema`

`@section_migrator(Section.X)` registers one function per section, and the store stamps each section's current schema integer as it writes, so no section reaches disk without it. The domains owning the sections are pure `core/` and can't import a store (INV-2). Affects D7 §2.
**Rejected:** one document-level migration per version - every domain's shape change would edit one shared function.

### D-0093 · The throttle is one period, armed by the first mark

A `mark_dirty` on a clean store arms one `async_call_later(save_period_s)`; it writes everything dirty and lapses, and the next mark arms the next period. So writes are never closer than the period and nothing waits longer than it (INV-14). `at_once` writes immediately; `flush()` cancels and writes. Affects D7 §2, §7.
**Rejected:** a cadence that keeps running while dirty - same writes in every test, plus a timer armed after the store goes clean.

### D-0094 · HA-side tests that aren't flows live in `tests/runtime/`

The store, and later the runtime's triggers and lifecycle, are neither flows nor pure core. Putting them in `tests/flows/` would make the directory's name a lie. Affects D9 §3.
**Rejected:** `tests/flows/` - no new directory, but it'd come to mean "anything that starts `hass`".

### D-0100 · No timezone literal in the integration

The site's zone is `hass.config.time_zone`. A market's clock (Nord Pool clears around 13:00 CET wherever the house is) is data: `providers/prices/markets.py` maps each bidding area to its clock and is the only module allowed an IANA zone name, which `test_no_timezone_literals.py` greps for. Every source with a publication time shows it under Advanced, defaulting to the area's. Affects D1 §2, §3, §4, §6.
**Rejected:** `MARKET_TZ = "Europe/Oslo"` in `nordpool_action.py` - one line and right for every Nord Pool area, but it doesn't say whose zone it is, and the next reader who needs the site's zone finds it one import away.

### D-0101 · Two format kinds in one registry; `action.py` owns the one action call

The Tibber and EnergyZero integrations publish only a response action, no entity to parse. `formats/base.py` gets `FormatKind` (`ATTRIBUTES`, `ACTION`) and an `ActionFormat` protocol, so the prices step still detects and pre-selects them from the registry. `providers/prices/action.py` holds the single `async_call`, keeping INV-3's allowlist short and `normalise` called in one place. Affects D1 §2, §3.
**Rejected:** plain `PriceSource`s outside the registry like `nordpool_action.py` - the flow would get a second, hand-written list of sources to switch on.

### D-0102 · A state-priced row prices the slot of `last_reported`, snapped to a configured grid

`nordpool_core` and `comed` publish only the current price as the state. `state_interval` makes one interval starting at `last_reported`, floored to `slot_minutes` (Advanced: 5/15/30/60, default 15 for Nord Pool, 60 for ComEd). A single reading says nothing about its own length, so configuration is the only honest source. `last_reported`, not `last_changed`, because two slots in a row can have the same price. Affects D1 §2.
**Rejected:** leaving the end unset for `normalise` to imply - a single interval has no next start, so it falls back to a hidden one-hour guess, which INV-7 forbids.

### D-0103 · A row that publishes only starts can't report a hole, and none is invented

For start-only rows (`energidataservice`, `entsoe`, `tge`, `pvpc`, `hourly_attributes`, `easyenergy_action`), a missing price widens the slot before it, since D1 §5.2 takes a slot's length from consecutive starts. Rows that publish bounds report real holes. Either way no price is invented and nothing becomes a zero. Affects D1 §2.
**Rejected:** inferring the slot length from the series' modal spacing - D1 §5.8 allows mixed 60- and 15-minute slots, and any single inferred resolution mislabels one of them.

### D-0104 · Hour-key rows take the local date from `dt_util.now()`

`pvpc` and `hourly_attributes` publish `<prefix>{HH}h` attributes with no date. The date is HA's local today (or tomorrow for the tomorrow prefix), and the intervals are naive local times that `normalise` localises (D1 §5.2). The 25-hour day's `_d` suffix gets `fold=1`. Affects D1 §2.
**Rejected:** a `timezone` field in each row's schema - a zone name in saved config goes stale when HA's zone changes.

### D-0105 · Amber's NEM second is snapped off the interval start

The NEM labels intervals by their end and opens them a second late (`04:00:01`-`04:30:00`). Taken literally, every slot is 29:59 long and a full day has 47 one-second holes. `formats/amber.py` zeroes the seconds. Affects D1 §2.
**Rejected:** computing the start from Amber's `duration` field - it's not documented to always be there, and snapping needs only the two timestamps every row has.

### D-0106 · `manual` publishes one slot per local day

`ManualSource` returns one slot spanning the whole local day (23, 24 or 25 h) at a flat price, unless `daily` overrides that date. Magnitude is configurable so a price can be typed in øre. D1 §2 says gas slots are daily, and `price_at`, `is_flat` and `spread` read a one-slot day correctly. Affects D1 §3.
**Rejected:** 96 identical quarter-hour slots - asserts a resolution the source doesn't have, for 96× the storage.

### D-0107 · The registry test reads D1 §2's table out of the LLD

`test_registry_table.py` parses the format table from `design/lld/D1-pricing.md` and asserts, per row, a registered adapter with that key and platform and a fixture, and that nothing is registered that §2 doesn't list. The failure that actually happens is a row added to the design and never built. D1 §2's key and platform columns must stay backticked.
**Rejected:** a literal roster in the test - a third copy of the table, and the one updated with the code, so the LLD is the one that drifts.

### D-0110 · The presets ship before the benchmark houses

"Presets + benchmark houses" is split: the ten remaining preset files with a golden each first, the `ContractedPower` wiring and the six D9 §5.9 houses after. The presets depend on D2 alone. The houses need `nl_pv`, `fi_linear` and `es_contracted` to have a tariff to run on.
**Rejected:** shipping them together - proving each preset against a simulated year is a stronger claim than a golden, but it sequences the data after the thing that consumes it.

### D-0111 · A preset declares its market's zone as `tz`

`schema.json` gets an optional preset-level IANA `tz`, validated on load, and every shipped preset with a country carries it. Ellevio's 22:00-06:00 or 2.0TD's P1 are the market's clock, not the site's; a Spanish flat managed from Oslo has a Norwegian HA and a Spanish tariff. Affects D2 §6.
**Rejected:** deriving the zone from `country` - the US and Australia span several zones. Putting `tz` on each version - a DSO doesn't change continent between price lists.

### D-0112 · The ES preset's two potencias differ

`es/2_0td` ships 4.6 kW in P1 and 5.75 kW in P2, and D2 §6's default row says so (and gains NL's 17.25 kW). D2 §9 12 asserts `limit_now_w` flips at 08:00 on the shipped file; with the same number on both sides there's nothing to flip. A higher valle power is the usual 2.0TD setup.
**Rejected:** keeping 4.6/4.6 and testing the flip on an inline spec - the test exists to catch transcription errors in the shipped file.

### D-0113 · A shipped preset's numbers may be a default the flow offers

`nl/connection`'s 17.25 kW, `es/2_0td`'s potencias and `fi/energiavirasto-2026`'s 2.50 EUR/kW are marked `assumed` as defaults the flow offers, separate from the published structure around them, which is verified. The household reads its own periods and a default power it's expected to change.
**Rejected:** shipping them with no numbers - the schema requires them, and a preset that can't load can't show up in the select.

### D-0114 · Three tariff-model gaps the presets found, and how each ships anyway

| Gap | Where | Shipped as |
|---|---|---|
| Seasonal pricing and eligible hours (US summer/winter demand rates) | `us/aps-saver-choice-max`, `us/srp-e27` | `eligible.months` covers May-October; winter steers on price alone (HLD §3's degradation) |
| No per-day `price_period_unit` (Ausgrid's c/kVA/day) | `au/ausgrid-ea116` | converted to a year; month length averages out, a few % off per month |
| No kVA demand charge | `au/ausgrid-ea116` | read as kW, exact at pf 1.0 |

Each file says in its `assumed` what's missing. Each gap would be a field that changes what every existing preset means, so the model isn't widened here. Which half-hour matters is right in all three, and that's what steering uses; only the reported fee drifts (INV-68).
**Rejected:** applying the summer version year-round - SRP's winter peak hours differ, so it would shed at the wrong times.

### D-0120 · The site timezone is the environment's

The site zone is `hass.config.time_zone`, stored with `timezone_source` at creation. A `timezone` step appears only when HA's setting is missing or unknown to tzdata. No flow module contains an IANA key, which `test_no_timezone_literals.py` greps for. A market's publication zone stays data on the price source. Affects D8 §4, §5.1.
**Rejected:** asking on the name step with HA's zone as default - two sources of truth for one clock is how a window boundary and a `TimeFilter` end up an hour apart.

### D-0121 · `FieldKind.ENTITY` is the only new member

D-0087 owed it and D8 renders it as an `EntitySelector`. No `DEVICE`, `AREA`, `TIMEZONE` or `CONFIG_ENTRY`: the device pick, the timezone step and the meter's roles are hand-written steps with filters `Field` can't express. Affects D1 §4.
**Rejected:** adding all four so the enum looks complete - members nothing produces can't be checked.

### D-0122 · The entry's unique id is the import register's platform unique id

`unique_id` is `meter:<platform>:<registry unique id>` of the import register (or the grid-power sensor without one), and `site:<flow id>` on the price-only path. INV-50 wants something the household can't rename; an entity id can be renamed, the platform's unique id (the meter serial) can't. Affects D8 §2.
**Rejected:** the entity id - stable only as long as nobody renames it.

### D-0123 · Money crosses `entry.data` as a decimal string, at any depth

`MONEY` fields and any `Decimal` default are stored as `str(Decimal)`, and `jsonable` converts nested ones too. orjson refuses a `Decimal`, and a float turns Tensio's 0.3604 into something that isn't. Affects D8 §4.
**Rejected:** floats - invisible until a month is summed into a bill and compared with an invoice, which is exactly what D11 does.

### D-0124 · The fuse list runs to 400 A

D3 §6's list stopped at 125 A while its own US default is 200 A. The select now offers 16-400 A, matching D3 §5.1's validation band.
**Rejected:** a free-text box above 125 A - every value it could take is a standard rating anyway.

### D-0125 · Norway defaults to the IT system, and the review states the kW

`default_system("NO")` is `IT_230`. Every answer needs a default (HLD §7.9), and IT and TN differ by ~1.74× in watts per amp, too much to hide, so the review states the connection in kW and the field help says how to tell them apart. A wrong answer shows on the review, not as a ceiling 74 % off for a month.
**Rejected:** `TN_400`, the newer stock - but older IT houses are where capacity tariffs hurt most, and the review catches either.

### D-0126 · A modifier is pre-ticked only if every required option has a default

The prices step pre-ticks a country's recommended modifiers except those with a required field and no default. For Norway that's VAT; the grid energy charge comes from the chosen tariff preset as a `tou_schedule` with `source: <preset id>`. Pre-ticking a levy or time-of-use table with no numbers would ship an unsourced price. Affects D8 §5.1.
**Rejected:** tariff step before prices so the preset's components show pre-ticked - D8 §5.1 fixes the order and the review shows the merge either way.

### D-0127 · The price-only path skips the tariff step and gets `NoPeak`

With no meter there's no metric to bill, so on `price_only` the flow skips meter and tariff and stores the `no_peak` preset (HLD §4). Affects D8 §5.1.
**Rejected:** asking for the tariff anyway - stores a ceiling nothing can defend; adding a meter later is a reconfigure.

### D-0128 · The entry holds the preset's identity, not a copy of its model

`entry.data["tariff"]` holds the preset id, file, name, version ids, the chosen version, the rendered description, target and risk. The copy INV-66 relies on is the site store's (D2 §8); the identity lets it be checked and `preset_outdated` raised. Affects D8 §4.
**Rejected:** keeping a `grammar_copy` in the entry - two copies in two shapes, and only the store's is read.

### D-0129 · Advanced is a collapsed `section`; back is `last_step=False` plus sticky answers

Advanced fields sit in a collapsed `section("advanced")` marked `vol.Optional(default={})`, so defaults arrive unopened. `show_advanced_options` is deprecated in 2026.9 and logs a warning naming the integration. The eight notification categories that default to off go in the section too. HA has no generic back, so every step but the review passes `last_step=False` and re-renders with what was answered. Affects D8 §5.1, §5.4.
**Rejected:** `show_advanced_options` until it's removed - deprecated and warns. All eleven notification selects abreast - the three that matter get lost.

### D-0130 · The `Plan`'s four questions are methods on the type

`Plan` and friends live in `core/model.py` with `cap_w`, `desired_state_at`, `idle_seconds_from` and `next_active` as methods; `core/strategies/plan.py` re-exports them and adds `build_plan` and `inputs_digest`. D6 and D8 read a plan without importing D5, as with the curve statistics (D-0030). Affects D5 §3.
**Rejected:** free functions in `plan.py` - every consumer would import D5 for arithmetic over fields it can already see.

### D-0131 · `Desired` moves to `core/model.py`; `DesiredState = Desired | SetpointDelta`

`PlanSlot.desired_state` is `Desired` (`comfort | shed`, for MODE) or a `SetpointDelta` in kelvin (for SETPOINT), so the enum moves below `core/loads/`, as D-0060 did for `Demand`'s types. `from .kinds.base import Desired` still works. Affects D5 §4.
**Rejected:** a dataclass with an optional option and an optional delta - a shape neither LLD draws, and two fields to check where a union dispatches on `isinstance`.

### D-0132 · `LoadView`, `Curves`, `Forecasts` and `CeilingSource` are declared in `context.py`

D5 is the first consumer of each. `LoadView.of(load, demand, …)` builds the projection. `CeilingSource` is a two-method view of the tariff, so D5 reads the ceiling and eligible windows and nothing else, and the real evaluator satisfies it in tests. Affects D5 §4, D6 §4.
**Rejected:** `core/model.py` - `LoadView` projects a `Load` and `Forecasts` is D10's surface, so the model would import domains it sits below.

### D-0133 · `plan_all` lives in `base.py`

The site walk sits next to the registry it dispatches through; `context.py` can't import `base.py` without a cycle. Re-exported from `core/strategies`. Affects D5 §3.
**Rejected:** a new `site.py` - a module D5 §3 doesn't draw.

### D-0134 · `Plan` keeps §4's fields where §3 spells them as methods

`cost_estimate`, `confidence`, `coverage`, `covered`, `planned_kwh` and `required_kwh` are fields. D5 §3 and §4 disagreed; §4 is what §7 persists, and `build_plan` computes them once so no two strategies disagree about a plan's cost. Affects D5 §3.
**Rejected:** methods derived from the slots on every call - a restored plan has slot prices as old as the plan.

### D-0135 · `should_adopt` takes the curve and the zone; stale doubles once

`should_adopt(old, new, policy, *, curve, tz, now, inputs_changed, stale)`. The threshold is a fraction of the local day's spread (INV-8), which needs the curve and the zone, and "doubled when stale" is applied once, not by both the policy and the caller. Affects D5 §3, §5.9.
**Rejected:** a pre-computed threshold passed in - every caller would own INV-8's arithmetic.

### D-0136 · The inputs hash covers demand and knobs, never prices

`inputs_digest` covers strategy, mode, deadline, `max_w`, `min_w`, parameters and presence; the requirement is compared with §5.9's ±10 % rule. Prices in the hash would change it on every fetch, and a changed input adopts unconditionally, so float noise on a flat Norgespris night would re-plan every quarter hour (INV-32). A materially changed curve is what the hysteresis measures, in money. Affects D5 §5.9.
**Rejected:** hashing prices rounded to the minor unit - one øre on one slot would still force an adoption, and the rounding is a hidden second threshold.

### D-0137 · A block is filled by spreading, not front-loading

With `min_block_min`, a block's share of the requirement is spread over its slots by capacity; only extension slots fill to capacity. Front-loading would cover a small requirement in the first slot and break the minimum run the rule exists for. In `spread` the share is not raised to `min_w`, which is why `fill` is the default for loads with a power floor. Affects D5 §5.3, §5.9.
**Rejected:** filling from the block's start - cheapest inside a block, but it breaks the block length D5 §9 2 asserts.

### D-0138 · Which demand gets `urgent`, and what an empty plan means

`PlanMode.NONE` for an unknown requirement and for `always`; `URGENT` for `price_sensitive = False` outside `force` (min SoC, legionella, a comfort violation); `FORCE` under force. `NONE`, `URGENT` and an empty plan all mean `cap_w = None`: the allocator runs the load against the ceiling, where precedence lives (INV-1). Affects D5 §4, §8.
**Rejected:** planning urgent demand at `max_w` from now - a plan asserting a grant is the allocator's job.

### D-0139 · `ReplanTrigger`, and which triggers skip the rate limit

The seven triggers are a `StrEnum`; `replan_due()` allows one replan per load per 60 s except for `force`, `service` and `startup`. A switch the household flicked shouldn't wait a minute. Affects D5 §5.9.
**Rejected:** rate-limiting everything - a "charge now" that does nothing for a minute reads as broken.

### D-0140 · The executor ships before the runtime

`writegate.py` depends only on the pure gate and `hass`, and exposes what the runtime will call: `async_apply`, `async_release`, `async_release_all`, `cancel`, `track`/`untrack`, `budget`. Building it against the pure gate proves the split (PLAN §7 dec. 5) and settles `blocking=True`, the read-back timer and the `hass.states` rule in one file. D4 §5.10's four report-backs fix its shape.
**Rejected:** waiting for the runtime - the API would be designed with the runtime in view, but D4 §5.10 already names it.

### D-0141 · The read-back reads through an injected `StateReader`

`WriteGate(hass, read_state=…)`: only `writegate.py` may write and only `runtime.py` and `providers/` may read `hass.states` (INV-3). The read-back is a read and the compare is the gate's, so the reader is injected, and provider scaling (D-0085) isn't duplicated. Affects D4 §5.10.
**Rejected:** allowlisting `writegate.py` for `hass.states.get` - one line, but the second exemption is what makes a rule negotiable.

### D-0142 · `DeviceCall`, and `WriteTarget.call_for()`

D4 §4.5's `ServiceCall` ships as `DeviceCall(domain, service, entity_id, data)`, since `homeassistant.core.ServiceCall` owns the name. The executor needs one method of a profile, `call_for(write) → DeviceCall | None`; an unbound role answers `None`. Affects D4 §4.5, §5.10.
**Rejected:** the LLD's name with an import alias - an alias in every profile, one careless import from a confusing type error.

### D-0143 · A timeout escalates on the executor's own transient clock

On `TimeoutError` the grace runs from the transient clock the executor last reported for that load, not only the one on the decision. `decide()` clears `transient_since` as soon as the entity reads available, so a device with healthy entities and hanging writes would never reach `unhealthy`. Only the executor sees a call that never returned. Affects D4 §5.10, §8.
**Rejected:** escalating only on the decision's clock - a hung transport would never count a failure, raise a repair or notify.

### D-0144 · A write that wasn't confirmed leaves no settle window

Both failure paths clear `verify_due` and arm no read-back. D4 §8 retries a refused write as `urgent`, and row 5 would otherwise hold that retry inside the settle window of a write that never landed. The window exists to stop us reacting to our own write; a write that didn't happen has nothing to settle. Affects D4 §8.
**Rejected:** arming the read-back after a timeout anyway - the next tick reads the entity regardless, and the settle window would hold the retry.

### D-0145 · The executor takes a `Decision` and hands back a `GateState`

`async_apply` takes `Actuation(load_id, name, target, cfg, decision)` and returns an `Outcome` whose `gate` the runtime folds into `LoadState.gate` (or receives through `on_state` for the async read-back). `Decision` is the one pure object with both the command and the gate state, and the executor's outcome is a state change.
**Rejected:** taking the `ApplyResult` - it has no `GateState`, so the executor would need the whole `LoadState` to report one write.

### D-0146 · The site's transport buckets live in the executor

`WriteGate.budget` is the site's `TransportBudget`, read into each tick's `LoadCtx`. It's the one piece of gate state that's per site, not per load, and it has to count writes the engine doesn't drive, like releases at unload (INV-58). Affects D4 §5.10.
**Rejected:** the engine's own state - it would need telling about every out-of-band write.

### D-0147 · A release is a registered plan, so unload needs no engine

`track(load_id, plan)` registers a callable that returns the release `Actuation` for that load from the pure `load.release(state, ctx)`. `async_release(load_id)` performs one, `async_release_all()` all of them. The lifecycle releases at setup, at the site switch and at unload, none of which the engine drives, and the decision still has to be the pure one. Affects D4 §5.10.
**Rejected:** the runtime computing every release and calling `async_apply` - "release everything" becomes a loop written in two places, which is how a shed survives an unload (INV-26).

### D-0148 · A command is atomic: an unaddressable role sends nothing

If any write in a `Command` has no bound entity, nothing is sent and the load reports `failed`, naming the role. A transport that dies mid-command reports what went out in `Outcome.calls`. Stopping a charger is "switch off, then 0 A", and half of that is a state nobody designed. Affects D4 §8.
**Rejected:** sending what can be addressed - a charger left enabled at 0 A, or disabled with 32 A armed, is a state the next tick reads as fact (INV-22).

### D-0150 · `DeviceView` has three builders, and a capture is one of them

`from_hass(hass, device_id)` reads the registries, `from_states(hass, entity_ids)` reads named entities for the tick, and `from_dump(document)` reads what `tools/capture_fixture.py` wrote. All are production code, so D9 §9 7's round trip proves the house's entities match, not that a test loader agrees with a profile. A capture carries no platform, so profiles also match on entity shapes (D4 §5.9).
**Rejected:** a test-only dump loader - the flow needs the same view before anything is bound, and two loaders drift.

### D-0151 · A `RoleBinding` carries the range it read, and whether it may be written

`RoleBinding` gets `step`, `min_value`, `max_value` and `writable`. A write is unscaled, floored to `step` and clamped; an unwritable binding answers `call_for` with `None`. The range is read at match time and used on every tick, and the subentry has to store it (INV-66). Affects D4 §4.5.
**Rejected:** leaving quantising to the control kind - the kind's step is physics (whole amps on the pilot signal), the entity's is the interface, and a refused write is measured against the entity's.

### D-0152 · `MatchResult` says which profile made it and which required role is missing

`MatchResult` gets `profile` and `missing: tuple[Role, ...]`, plus `claimed` (`confidence > 0`). `registry.match(view)` returns claimed matches ranked by confidence, then key. The flow renders the missing roles itself rather than parsing prose. Affects D4 §4.5, §5.9.
**Rejected:** a mapping from profile key to match - a match travels alone into goldens and the review.

### D-0153 · A `Provision` names an entity, because some have no role

`Provision(entity_id, value, reason, role=None, scaled=False)`, and `provisions(view)`. `easee_ble` insists on `select.*_bluetooth_mode = always_on`, since on `button_press` the Bluetooth link only works briefly after someone presses the charger and the control path disappears silently. powerplan never steers it, so it gets no role. Affects D4 §2, §4.5.
**Rejected:** a `TRANSPORT_MODE` role - a role is something powerplan reads or commands on purpose.

### D-0154 · `Quirks` is a whole row of the §5.10 table, and its numbers are floors

`Quirks` gets `min_interval_s`, `tolerance`, `statuses` and `forgets_limit_on_link_loss`. `gate_config(kind)` raises the kind's config to the profile's floors with `max`. `easee_ble`'s 30 s read-back is a fact about the radio; as a floor, `Modulate`'s 60 s settle still wins (D-0065). Affects D4 §4.5, §5.10.
**Rejected:** the profile returning a whole `GateConfig` - it'd have to know which kind it serves.

### D-0155 · Link loss is an unreadable role, not a forgotten memory

While `easee_ble`'s status is `LINK_DOWN`, `CURRENT_SET` and `POWER` read `available = False` with no reading, even if the entities hold their last value. powerplan decides against the entity (INV-22), and during a link loss the charger may have fallen back to its own 32 A; the value is unvouched-for. The reconnect re-arms from scratch. Affects D4 §5.11.
**Rejected:** clearing `GateState.last_value` from the profile - reaches into the pure gate and suppresses the deviation log a lost write shows up in.

### D-0156 · A profile returns the whole `Reads` bundle

`read(role, states) -> Reading` ships as `BoundDevice.reads(view, now) -> Reads`: one frozen bundle at one instant, including statuses, options and switch states, which aren't `Reading`s. It also lets the link-loss rule be a statement about the bundle. Affects D4 §4.5.
**Rejected:** per-role reads assembled by the runtime - the link-loss rule would become a conditional on the profile key in `runtime.py`.

### D-0157 · A profile matches; a bound device writes

`DeviceProfile.match(view)` and `bind(bindings) -> BoundDevice`. The `BoundDevice` is the executor's `WriteTarget`, holding `call_for` and `reads`. A registered profile can't carry one device's entity ids, and bindings live in the subentry (INV-66). `easee_ble` adds `EaseeBleDevice` for the link-loss rule. Affects D4 §4.5.
**Rejected:** a profile instance per load - `match()` would become a classmethod and the split would happen anyway, less visibly.

### D-0158 · A whole-number device value is sent as an integer

`number.set_value` and `climate.set_temperature` carry `int(value)` when the quantised result is integral: `{"value": 16}`, not `16.0`. Same call to HA, clearer logs and goldens; a 0.5 A step or a 0.1 °C setpoint still sends a float.
**Rejected:** always a float - `15.0 A` in a log invites the question whether something rounded.

### D-0160 · `LoadView` gains the fields D6 needs and D5 ignores

`LoadView` gets `thermostatic`, `sheddable`, `min_on_s`, `phase_names`, `phases`, `quantiser` and `quantise()`, filled from the materialised params (INV-66) and the kind's dwell. D6 needs each by name: a thermostatic inverter drawing 23 W doesn't reserve 3 kW, a heat pump isn't sheddable below stage 4, stickiness needs `min_on_s`, phases turn amps into watts. A vetoed stop is charged the floor's watts (D6 §9 23). Affects D5 §4, D6 §4.
**Rejected:** a D6-local wrapper around `LoadView` - two projections per load per tick where the LLDs draw one.

### D-0161 · `budget()` takes a `Baseline` before it uses one

`Baseline` is a protocol in `core/allocation/budget.py` and `budget()` accepts it where D6 §3 puts it, reporting `projection_source = "smooth"` until D10's baseline arrives. The σ floor is implemented now: it stops a forecast shrinking the reserve to its minimum (INV-62). The engine calls this signature, so it's fixed early. Affects D6 §2, §5.1.
**Rejected:** adding the parameter later - a signature change rippling through the engine and the benchmark runner.

### D-0162 · `allocate(ctx, constraints, cfg, state)`: one frozen tick bundle

D6 §3's eleven positional parameters become `AllocCtx` (`now`, `meter`, `budget`, `electrical`, `loads`, `plans`, `views`, `previous`, `stage`, `blunt`, `frozen`, `hard`, `marginal_cost`). §3's list missed the meter snapshot, the per-load controlled views and last tick's grants, and `Constraint.prepare` already needs an `AllocCtx`. `Demand` rides on its `LoadView`. Affects D6 §3.
**Rejected:** fourteen arguments on every tick, with constraints reading a separately built context that could disagree.

### D-0163 · `proportional_trim` returns the grants; the report is the caller's

The trim returns `(grants, freed_w)` and the allocator derives `trimmed`; `Ladder.update` takes the `Budget` instead of five loose numbers. `AllocReport` is frozen, so nothing accumulates into it in place. Affects D6 §3.
**Rejected:** a mutable report draft through the trim and the walk - two shapes of the report that can disagree (INV-40).

### D-0164 · A load is judged against the allowance less what the loads decided before it hold

The walk's availability is `P_allow − uncontrolled_w − Σ reserved(loads decided so far)`. Read literally, D6 §5.2 judged a high-priority load against the reservation of a lower-priority load the walk is about to trim: a 3 kW tank denied because a 4.6 kW charger still runs. Single-load cases come out the same. The uncontrolled term is explicit because the reserve covers its deviation, not its level. Affects D6 §5.2, §5.3.
**Rejected:** §5.2 as written - priority would depend on what lower loads happen to hold.

### D-0165 · A `Constraint` declares its own `shed_reason`

The protocol carries `shed_reason: ClassVar[ShedReason]`; a load a constraint took to zero is shed for that reason. A mapping table in the walk would be a conditional per constraint kind, and new constraints add a line of their own instead. Affects D6 §2.
**Rejected:** deriving it from `scope` - `site` covers both the fuse and a DSO event.

### D-0166 · A breached circuit or phase re-decides its members; a breached site limit doesn't

`post()` returns `Violation`s and the walk applies stage 4 to a blunt violation's members only, with stage 4's exemptions. The site limits return none: a site breach is already a blunt ladder reason reaching every load, and a contracted-power trip needs the ladder's tolerance judgement (D2 §5.8). Affects D6 §5.8.
**Rejected:** every constraint reporting and the walk shedding the union - stage 4 twice, the second time without exemptions.

### D-0167 · A sub-metered circuit and a phase give the asking load its own draw back

`CircuitLimit.cap_w = fuse − (sub_meter − own draw) − unmetered`; `PhaseLimit.cap_w = headroom_a × w_per_amp + own draw`. Both readings include the asking load, so a charger at 16 A on a 32 A circuit would otherwise be offered only the remaining 16 A every tick and walk itself down. Affects D6 §5.8.
**Rejected:** subtracting the sub-meter reading whole - a ratchet to nothing for a load already inside its fuse.

### D-0168 · `measured_w(view)`: unmeasured is not zero

`measured_w(view)` is D3's `controlled_power` precedence (commanded value while settling, INV-18) without its `0.0` fallback: `None` when nothing knows. An unmetered thermostatic load reserves its rated power; one measured at zero reserves the margin. The fallback is right for σ and wrong here.
**Rejected:** checking `view.measured_w is None` at each call site - D3's rule restated three times, and the settling case is the one a restatement gets wrong.

### D-0169 · A relay still closed keeps its reservation after it's shed

A shed on/off load still drawing above `ON_W` reserves its nameplate, so `p_free_w` doesn't count its watts as free yet. The trim does count them as freed: the relay will open. Otherwise a lower load could be granted the same 3 kW in the same tick. Affects D6 §5.2.
**Rejected:** a shed load reserving nothing at once - the same watts go to two loads.

### D-0170 · Accounting's modules and unit tests land before its scenarios

D3's `loads.py`, all of D11 §3 and their unit tests ship first. The `savings_vs_twin` and `observe_calibration` scenarios need D9's runner and follow with it. D11 is the number people will quote (PLAN §6 R10), so its arithmetic gets tested against the simulators as early as possible, and four other parts read its API.
**Rejected:** waiting to ship it whole - `savings_vs_twin` is the evidence for the shadow model, but it's a runner-wiring job once `close_slot` exists.

### D-0171 · `LoadMeterState` is frozen, with a tuple of closed slots

Same reason as `WindowState` (D-0021): it crosses into D7's store, and one frozen object can't be saved with a new anchor beside an old pending list. Both share the `meter` section. Affects D3 §4, §5.12.
**Rejected:** a mutable state as D3 §4 drew it - less `replace` noise, but a half-saved state becomes representable.

### D-0172 · `sample()` returns nothing; `closed()` and `ack()` are the seam

Nothing reads a load meter per tick: D11 asks once per closed price slot. So `sample()` returns `None`, and `closed()`/`ack(upto_utc)` hand over slots. A slot is kept until D11 has recorded it, as a window is kept until D2 has. Affects D3 §5.12.
**Rejected:** returning closed slots from `sample()` - invites consuming without acknowledging.

### D-0173 · A slot-length change is adopted at a boundary both lengths share

The next boundary when the slot shortens, the next boundary of the longer length when it lengthens. Applied literally, 15 → 60 at 10:15 opens a slot at 10:00 and bills its first quarter hour twice. Affects D3 §5.12.
**Rejected:** opening the longer slot at once - it'd be misaligned with every price slot after it.

### D-0174 · The plug-in shadow takes `required_kwh` as it arrives

D11 §5.3 divided `required_kwh` by `charge_eff`, but `EnergyStore.required_kwh` already returns kWh from the wall. Dividing again overstates the counterfactual by 11 % at 0.90 on the house's largest load; D11 §9 5's own numbers (30 kWh at 11 kW, 17:00-19:44) agree. Affects D11 §5.3.
**Rejected:** reading `required_kwh` as energy into the battery - INV-69 means the same convention as the real load.

### D-0175 · A slot the curve doesn't cover is priced at zero and marked

`slot_price` returns zero with `ESTIMATED` for a slot the curve lacks, and it's queued for its one re-price. Zero is the one number that's certainly not a guess, and `estimated_share` shows the household an hour is missing. Affects D11 §5.2, §8.
**Rejected:** carrying the nearest known price forward - invents a number and presents it as priced (INV-5).

### D-0176 · Calibration lives outside the month; the site's confidence counts material loads

`AccountingState.calibration[load_id]` holds the trailing seven days of observe slots and a lifetime `observe_days`, outside the month records so a rollover doesn't reset them. The site's confidence is the worst among loads with at least 10 % of the site's `|savings|`, `none` if no load reaches that. Affects D11 §4, §5.5.
**Rejected:** a savings-weighted average - the average of `ok` and `low` isn't a state.

### D-0177 · A bang-bang shadow is integrated in one-minute steps

`ThermostatShadow` steps its store per minute inside the price slot. One step per slot can't produce an on-fraction: a 1.6 kW cable in a 0.55 kWh/K slab overshoots a 1 K band by 2 K in an hour. A 20-load month is still microseconds. Affects D11 §5.3.
**Rejected:** an analytic duty cycle - drops the store, and a slab below its band draws far more than steady state.

### D-0178 · The observe anchor lands once per local day, not per slot

A bang-bang shadow sits centred on its target while a real thermostat sits about half a band below it. Re-anchored every slot, the shadow re-heats that offset each slot: calibration error 1.16 against `tests/sim/slab.py`, versus 0.004 anchored daily. The error gates `savings_confidence`, so per-slot anchoring would read `low` on every thermal load. Affects D11 §5.3, §5.5.
**Rejected:** crediting the stored-energy change each anchor implies - a priced signed correction per slot, for the same error.

### D-0179 · The counterfactual bill is priced before the actual

`Evaluator.bill` always sets `last_bill`, which D7 persists as the site's bill, so `_close_window` bills the counterfactual first and the real history second. Affects D11 §5.1, §5.6, D2 §5.9.
**Rejected:** restoring `last_bill` afterwards or a side-effect-free `bill` - reaches into D2, or changes its signature for one caller.

### D-0180 · A role may be bound to an attribute

`RoleBinding.attribute`: a `climate` entity binds `SETPOINT` to `temperature` and `TEMP` to `current_temperature`. A climate entity's state is `heat` or `off`, so without this every thermostat's setpoint could be written and never read back (INV-22). Affects D4 §4.5, §5.9.
**Rejected:** a climate-specific `BoundDevice` - the domain leaks back into the layers above.

### D-0181 · `call_for_provision`: the cold path's own addressing

`BoundDevice.call_for_provision(provision)` sends a provision on a role through that role's binding (scaled, quantised, clamped) and one on an entity nothing steers as it stands. Without it, nothing could set the Easee's Bluetooth mode. Affects D4 §4.5.
**Rejected:** the runtime building the call - the ×10 scaling would live in two places.

### D-0182 · The `StateReader` answers in powerplan's units

The injected read-back reader (D-0141) returns a role's value through the load's bindings, from the attribute where one is named. `verify()` compares with `GateState.last_value`, which is in degrees; a `0.1 °C` entity counts tenths, and a raw reader would log a deviation on every landed write because 210 isn't 21.0.
**Rejected:** comparing in device units - the pure gate would hold a number whose meaning depends on an entity it can't see, and D4 §5.10's tolerances are in powerplan's units.

### D-0183 · A `MatchResult` carries the suggested kind and the capabilities

`suggested_kind` (`mode` where the device has an operation-mode select, else `setpoint`) and `capabilities` (bound role names plus climate extras like `cool` and `sensor_mode`), passed to `QCtx.capabilities`. Which kind steers a thermal load is a property of the device (D4 §5.5), and the flow renders from the registry. Affects D4 §4.5, §5.9.
**Rejected:** the flow deriving the kind from the bindings - puts §5.5's rule in the flow where a profile can't override it.

### D-0184 · A generic profile adds no gate floors, and its transport is configuration

The generic profiles' quirks are all zero, so `gate_config(kind)` is the kind's own row of D4 §5.10. The radio is the one thing they can't read off entities (the same Z-TRM is `zwave_js` in one house and MQTT in another), and the site's token bucket needs it, so `quirks_for(cfg.transport)` uses the transport the flow recorded. Affects D4 §5.9.
**Rejected:** guessing from the entity platform - a capture has no platform, bridges are spelled differently, and a wrong guess silently changes a site-wide budget.

### D-0185 · The generic confidence band, capped at 0.75

`generic_climate` starts at 0.6 and adds 0.05 per control capability (mode select, eco setpoint, floor minimum), up to 0.75. `generic_number` is 0.6 with an enable switch, 0.5 without; `generic_switch` 0.4. The cap keeps generic evidence below any specific signature (`easee_ble` is 0.80). Affects D4 §5.9.
**Rejected:** a flat 0.6 - a Heatit and a bare `generic_thermostat` would tie.

### D-0186 · `provisions(view, cfg)`: the device resolves entities, the subentry supplies numbers

`generic_climate` reads `floor_min_limit_c` (falling back to `floor_c`), `eco_setpoint_c` and `swing_k` from `LoadConfig.params`; no config, no provisions. A thermostat's hardware floor is the questionnaire's comfort floor (INV-64), which nothing on the device knows (INV-27). Affects D4 §4.5, §5.9.
**Rejected:** keyword arguments for the three numbers - one keyword per device type.

### D-0187 · A generic profile declines what a more specific one owns

`generic_switch` returns 0 for a device that also has a `climate` entity or a `number` in A or W. The Z-TRM and the ESPHome heat pump both have a switch and a power sensor; steered as plain switches they'd lose their own regulation (INV-64), and a heat pump's mains switch is never actuated (INV-29). Affects D4 §5.9.
**Rejected:** claiming at 0.4 and letting ranking sort it out - most people accept the first suggestion.

### D-0188 · `TEMP` is the climate entity's; `TEMP_FLOOR` falls back to it in floor mode

`TEMP` binds to `current_temperature`. `TEMP_FLOOR` binds to a `*floor*` temperature sensor if one exists, else, when `sensor_mode` says `floor` or `both`, to the same `current_temperature`. The Z-TRM in F-mode publishes the slab as its current temperature and has no floor-sensor entity. `air` is not floor evidence. Affects D4 §5.9.
**Rejected:** leaving `TEMP_FLOOR` unbound for the type to fall back - the same fallback in air mode would call a room temperature a slab.

### D-0189 · A quantised value is rounded to six decimals before it's sent

A 0.1 step turns 21.0 into 21.000000000000004 through `quantise_down`. Six decimals is far below any device step, so it can't move a value to another step, and D-0158's integer path then works for 0.1 °C thermostats too.
**Rejected:** quantising in integer step counts - the step is itself a float, so multiplying back reintroduces the dust.

### D-0190 · `NO_HOLIDAYS` is D5's own empty calendar

`core/strategies/context.py` defines a `HolidayCalendar` that names no day, used when the site has none. `schedule` and `heat_capacitor` ask about holidays, and a `None` check at every call site is the branch that gets forgotten. Affects D5 §4.
**Rejected:** importing D1's - a dependency on D1's holiday module for a one-line null object.

### D-0191 · A forced load gets a plan that says nothing

`cheapest_hours`, like `deadline_fill` (D-0138), returns a free plan with `PlanMode.FORCE` under force. Force ignores price, and asserting a grant is the allocator's job (INV-1, INV-30). Affects D5 §5.4.
**Rejected:** `max_w` in every slot - a cap equal to the maximum says nothing a free plan doesn't, and reads like a grant.

### D-0192 · `best_save` compares against the cheapest slot in the postponement horizon

D5 §5.5's "next slot that is on" is circular. The comparison is the cheapest slot in `(s, s + max_off_min]`: where the load would run if `s` were skipped. A negative slot is never postponed. Affects D5 §5.5.
**Rejected:** iterating on/off to a fixed point - two passes per load per replan for a definition still arbitrary at the edge.

### D-0193 · `run_once` reads the programme from its parameters

`duration_min` and the ten-segment `profile` are strategy parameters (D4 §6.8's answers, later the learned profile), defaulting to a three-hour eco programme. A strategy owns no device state, and parameters are what `inputs_digest` covers. Affects D5 §5.6, §6.
**Rejected:** reading the profile off the `LoadView` - a learned profile would change the plan without changing its inputs hash.

### D-0194 · `schedule` takes configured windows first, the load's own profile second

Windows are local weekly `(weekday, start_min, end_min)` triples (weekday −1 = every day, wrapping allowed); with none configured, the target profile's comfort hours are the schedule. Inside, the envelope is `max_w`; outside, 0, with `comfort`/`shed` for MODE and SETPOINT loads. Affects D5 §6.
**Rejected:** windows only - a household with a weekly thermostat table would type it twice.

### D-0195 · `heat_capacitor` scales the cold term against the slot's own target

The outdoor-cold scaling of the banked delta (0.5-1.5 over 10 K) is measured from the slot's target, not a fixed reference. A 24 °C bathroom and an 18 °C bedroom lose heat differently at the same outdoor temperature. Affects D5 §5.7.
**Rejected:** one site-wide reference - wrong for every room but one.

### D-0196 · A tariff window is a peak only if some of the horizon isn't

Tariff-window banking treats the heaviest-weighted eligible windows as the peak. Under the Norwegian model every hour is eligible at weight 1, so nothing is a peak and the strategy doesn't coast for two days; under Ellevio the half-weight night isn't what a store banks against. Affects D5 §5.7.
**Rejected:** every eligible window as peak - on the reference tariff that's the whole day.

### D-0197 · Combinators are extras on any plan, applied merge → threshold → opportunistic

`threshold`, `merge` and `opportunistic` aren't registry rows; their knobs sit in `COMMON_SCHEMA` and `plan_all` applies them after the strategy, in that order, so the most specific statement ("this hour is paid for") has the last word. `merge` gets its partner through an injected closure. Affects D5 §3, §5.11, §6.
**Rejected:** registering each as a wrapping strategy - the strategy select would ask the household about the planner's structure.

### D-0198 · A reward event is one more price component, keyed by the slot's start

A demand-response reward raises the effective price by `per_kwh` for participating loads, as a composed component (INV-31). Every strategy already avoids dear slots. Affects D5 §5.11, §9 15.
**Rejected:** masking the window - a mask has no value, so the planner couldn't weigh it against price.

### D-0199 · The block variant enumerates runs, scores by capacity-weighted price, extends and prunes

Every contiguous candidate run of at least `min_block_min` is a block (prefix sums, O(n²)), scored by the capacity-weighted mean price of what it can carry. Take the cheapest; extend while an adjacent slot beats the best free block; once covered, extend while a neighbour is cheaper than the set's mean, then drop slots dearer than the mean when cover and block length still hold. The requirement is spread over the set (D-0137). Scoring by duration and stopping at cover missed brute force by up to 34 %. Measured: D5 §9 2's 300 seeded instances within 5 %; worst over 1 200 instances 9.6 % of the price span. Affects D5 §5.3, §9 2.
**Rejected:** exact search (a Dinkelbach iteration over a run-selection DP) - optimal, but pages more algorithm for a worst miss of a tenth of the price span.

### D-0200 · One latch per type on `LoadState`, and `Learned` is the types' numeric memory

`LoadState` gets the legionella timestamps (tank), `cycle: CycleState` (appliance) and `defrost_since` (heat pump). A type's numbers between ticks (the sensorless tank estimate, the legionella hold, the defrost detector's samples) live in `LoadState.learned` as `Learned(value, at, samples)` under documented keys. A cycle surviving a restart is INV-59's point. Affects D4 §4.1, §7.
**Rejected:** a free-form dict per type - untyped state is what migrations can't route.

### D-0201 · `QuestionKind.CURVE` is the seventh question kind

The heat pump's COP curve is shown and editable (D4 §6.4), so the questionnaire gets a `CURVE` kind: `(x, y)` points with bounds. Affects D4 §4.6, §6.4.
**Rejected:** JSON in a `TEXT` field - unreadable and unvalidated.

### D-0202 · `ModulateCfg.enable_role` may be `None`

A battery inverter takes a signed setpoint and nothing else, so its stop is 0 W with no enable write (a command naming an unbound role would send nothing, D-0148). Affects D4 §4.2.
**Rejected:** a separate kind for setpoint-only devices - duplicates the ramp, tolerance and suppression for one missing write.

### D-0203 · The tank's ready temperature is its anchor, and the legionella cycle overshoots 65 °C

The sensorless estimate re-anchors on `ready_temp_c`, the one number in D4 §6.3 that says how hot the tank gets. The legionella cycle drives a margin above 65 °C because a mechanical tank thermostat has a 5-8 K band and the hold is measured at the sensor. Affects D4 §5.7, §5.12, §6.3.
**Rejected:** asking for the dial position - the household describes a tank, not a control loop (HLD §7.9).

### D-0204 · A tank charge is planned in blocks of at most `TANK_BLOCK_MIN`

The water heater gives `deadline_fill` a `min_block_min` so the element runs in blocks, not quarter-hour bursts that wear the contactor. Affects D4 §6.3.
**Rejected:** placing the tank with `run_once` as one block - a tank is a store and may take its energy in two blocks around a draw-off.

### D-0205 · The defrost latch expires, and an inverter may step up a fifth per tick

`defrost_since` lives at most `DEFROST_MAX_S`; a stuck latch would suppress every shed forever. The heat pump reserves a fifth of its measured draw as margin (`RESERVE_MARGIN_FRACTION`), what an inverter ramps before the next tick sees it. Affects D4 §5.14, D6 §5.2.
**Rejected:** reserving rated power - 1.5 kW held for a unit drawing 23 W.

### D-0206 · The radiator table's areas and the bank-above-comfort limit

D4 §6.5's heater sizes get the floor areas they're sold for (the `RoomStore` mass hint), and a radiator banks at most 2 K above comfort (INV-56 needs a number). Area is also an advanced question; the table is the default. Affects D4 §6.5.
**Rejected:** asking the area up front - it's there under Advanced for anyone who knows it.

### D-0207 · The cycle's programme table carries a peak draw, and running is read from power first

D4 §6.8's rows get `peak_w` (the heater's connected load, the default nameplate). Running is `POWER ≥ 20 W` first and `PROGRAM_STATE` second; finished is the text, or a long idle after half the programme; a learned profile must fall within 0.5-2× the default. Residual-heat drying draws 5 W for an hour and looks finished. Affects D4 §5.13, §6.8.
**Rejected:** learning from every run - one aborted-and-restarted run would double the reservation.

### D-0208 · A cycle request is state; `force` is the same request with the price ignored

`ApplianceCycle.request(state, now)` records `requested_at`; `button.run_now` and a loaded machine both call it. It survives a restart (INV-59). Affects D4 §5.13, D8 §5.5.
**Rejected:** a `run_now` mode - it would expire with `force_max_h` and drop the wash.

### D-0209 · The battery type: signed `MODULATE` in watts, the reserve as floor, 0 W as release

A signed `MODULATE` over `BATTERY_POWER_SET` (step 100 W, no enable role, release 0 W, INV-64); an `EnergyStore` whose usable window comes from the chemistry (LFP 95 %, NMC 90 %) with the floor at `reserve_pct + 1`. With grid charging off, `max_w` is 0 until `surplus`. The flow doesn't offer the type until `peak_shave`/`arbitrage` exist. Affects D4 §6.6.
**Rejected:** waiting for the battery strategies - the questionnaire and the store are what they need first.

### D-0210 · D10's core is built before its providers

`core/forecasts/` (model, baseline, reconstruction, the five fits, the registry) lands before the providers, the planning-loop refresh and the baseline-aware reserve. It's pure and can't open a gate, and it settles the baseline's shape before D6's `budget.py` is written against it.
**Rejected:** one D10 WP in phase 5 - one PR per LLD reads better, but a working baseline is easier to design against than a sketch.

### D-0211 · `predict` is pure; decay lands on update, and confidence is one number

`update()` decays the weights to the window's end; `predict()`, `confidence()` and `n_eff()` never mutate and apply the decay for `t` to the confidence only. `predict`'s confidence is `min(bin, day-mean)`. Read literally, "decay lazily on read" would let a 48 h planning query age the state it plans from. Affects D10 §3, §5.1, §5.3.
**Rejected:** decaying all 168 bins on every read - side effects on persisted state from a read.

### D-0212 · `n_eff` is a property on `weight`, and the state is frozen

`Bin` keeps `mean_w`, `m2`, `weight`, `beta_w_per_k`, adds `samples`, and `n_eff` returns `weight` (D10 §5.1 defines them as equal). `samples` matters: a variance from one sample is 0.0 and mustn't be published as certainty (INV-62). Frozen state, mutable wrapper, as with D3's `WindowMeter`. Affects D10 §4, §7.
**Rejected:** storing both - a stale `n_eff` beside a fresh `weight` is one migration away.

### D-0213 · `Fit.effective` is `float | None`

When a fit fails and there's no configured value, `effective` is `None`. For a slab's loss coefficient there's usually no default, and D4 §5.7 skips an unknown loss term rather than guessing. Affects D10 §4.
**Rejected:** returning the fitted value as fallback - applies the fit INV-63 just refused, silently.

### D-0214 · The site's reconstruction is the worst of its loads

`UncontrolledHistory.reconstruction` is the worst per-load mark (`none < partial < full`), and a load with neither power nor on/off history is `none`. A site with one metered EV and one unmetered tank shouldn't report `full`. `loads` carries the per-load marks. Affects D10 §2, §8.
**Rejected:** `partial` for anything missing - hides which load a household could fix by binding one sensor.

### D-0215 · The constants and key D10 §5.6 leaves unnamed

Coast or idle episodes ≥ 2 h, heat-up ≥ 30 min; "on" above 10 % of rating without state history; nearest-rank p95; idle falling faster than 3 K/h is a draw; no area → per-unit bounds; `fit_all` keyed `"<load_id>.<fit_key>"`; the nameplate fit excludes `heat_pump` and `battery`. Fits publish their reasons, so a wrong constant shows up as a refused fit. Affects D10 §5.6.
**Rejected:** configuration options - nothing a household could answer (HLD §7.9).

### D-0216 · A well-insulated slab's coast fit lands under the floor, and the floor stays

On a two-node floor only the screed's share of the loss (~27 % per m²) comes out of the screed, so a 0.7 W/m²K envelope fits ~0.19 W/K·m² and is refused by the [0.5, 50] bound; the store then skips the loss term and charges again next slot. The bound caught a model mismatch. If real houses land under it too, the fix is a two-node store model, not a wider bound. Affects D10 §5.6.
**Rejected:** lowering the floor to 0.1 - it would apply the screed's share as the house's loss and under-charge every night fourfold.

### D-0217 · `for_planner()` bridges D10's answers to D5's protocol

`Forecasts.for_planner()` returns a `PlannerForecasts` satisfying D5's `Forecasts`: `outdoor_c(t)`, `surplus_w(t) = 0.0` until PV, `baseline_w(t)` with 0.0 when the baseline isn't offered. D5 takes bare floats; D10 answers with a confidence. 0.0 is right because the planner subtracts it (INV-62). Affects D10 §3, §9.
**Rejected:** widening D5's protocol - D6, not D5, is where confidence changes a decision.

### D-0218 · A series carries a float confidence, and `STALE` is never produced

`SeriesPoint.confidence` is 0..1, mapped for publication (≥ 0.85 `KNOWN`, ≥ 0.6 `ESTIMATED`, else `SYNTHESISED`). D10 §5.4 ages confidence numerically and D10 §2's gate is 0.6, so the float is primary. Affects D10 §4.
**Rejected:** `STALE` for an aged series - in D1 it means a source stopped answering; a forecast decays even when every fetch works.

### D-0219 · The `ForecastSource` protocol lives in `core/`

Declared in `core/forecasts/model.py`, with the entity readers in `providers/forecasts/base.py`. The registry is in `core/`, and a core registry typed by a providers protocol would invert INV-2's direction. It mentions no HA type. Affects D10 §3.
**Rejected:** keeping it in `providers/` with a loosely typed registry - the flow renders from the registry, so a type error would move to the flow.

### D-0220 · The backtest ships in two halves; `--simulate` waits for the engine

`tools/backtest.py --recorder|--csv` reads history, reconstructs windows (D3 §5.11), bills them (D2) and reports `BacktestMetrics`. `--simulate` needs the engine. The replay puts reconstruction, money and the recorder's quirks under test early, and showed the recorder holds years of hourly register rows but no water-heater power (PLAN §6 R8). Affects D9 §5.4.
**Rejected:** one tool in one go - the replay has no dependency on the engine.

### D-0221 · What a statistics row's `sum` refers to is measured, not assumed

`_anchor` scores both readings of an hourly `sum` (value at the period's start or its end) against the energy the power history shows, and takes the closer; `--register-anchor` overrides. HA files a row under its start with the last value inside it, which for a Norwegian AMS register (one report at HH:00:12, D3 §5.5) is the register at HH. Either fixed assumption shifts every window an hour and moves evening peaks across days. On the reference house: mean error 0.026 kWh per window for `start`, 0.807 for `end`.
**Rejected:** HA's `end` convention - wrong for the one meter this exists for, invisibly in totals, decisively in peaks.

### D-0222 · A window the register didn't measure is filled from power or dropped, never interpolated

Unless register rows sit within `max(60 s, cadence / 2)` of both boundaries, the window is rebuilt from the power history (`estimated`) or dropped with a note. D3 §5.11's helper interpolates across holes, which smears a peak and still reports `exact`. The helper is right for a baseline; for a peak metric the caller decides differently.
**Rejected:** interpolating and marking `estimated` - the dropped windows on the reference house are a recorder outage, not a quiet hour.

### D-0223 · The reader dispatches on the unit, not `has_mean`/`has_sum`

A `statistics_meta` row with Wh/kWh/MWh is a register, W/kW/MW is power. `has_mean` is NULL on current HA and moved to `mean_type`; the unit works across versions. Affects D9 §5.4.
**Rejected:** `mean_type` with a fallback - two schema variants now, three at the next rename.

### D-0224 · The backtest's zone has four sources and no default

`--tz`, then the export's `meta.json`, then `.storage/core.config` beside the database, then the preset's `tz` (D-0111); otherwise refuse, naming `--tz`. The report says which was used. The recorder stores only UTC epochs, and a wrong zone gives a plausible table, not an error. Affects D9 §6.
**Rejected:** the system zone - databases are routinely read on another machine.

### D-0225 · "Over target" is counted per tariff window against the step the period reached

`over_target` counts windows above `target_kw` from D2's `resolve_target_kw` (for `auto`, the upper bound of the step reached); `--target` asks about another bound. `gate` is zero over target per window per period. The top step's bound is infinite and reports `open`. Linear and tier markets fall back to the metric.
**Rejected:** counting windows above the period's metric - on a top-3 mean most windows are above it by construction.

### D-0226 · `BacktestMetrics` carries what a plain replay can fill, and says what it can't

A replay has no controller, so D9 §4's comfort minutes, shifted kWh, writes and dropped sessions are left out rather than reported as zero. It adds `metric_kw`, `target_kw`, `gate`, `confidence`, coarse and estimated window counts, `days` and per-load kWh and quality, so a reader can judge each row. The money columns stay `None` until accounting fills them. Affects D9 §4, §5.4.
**Rejected:** D9 §4 filled with zeros - `sessions_dropped = 0` on a replay isn't a measurement.

### D-0227 · Per-load energy has four qualities, and a climate entity's mode isn't one

A load reads `energy` (own register), `power` (mean power integrated), `on_fraction` (nameplate × on-time from raw states) or `missing`; `reconstruction` summarises them (D9 §2). A `climate` entity's state is its mode: the reference house's tank is a `generic_thermostat` that reads `heat` for months, and nameplate × that would fabricate 2 kW × 240 h. So it reads `missing`, which is true.
**Rejected:** `hvac_action` from state attributes - purged with the states, so ten days of a year.

### D-0228 · The CSV layout is one file per role, and a naive timestamp is refused

`--csv <dir>` reads `grid_register.csv`, `grid_power.csv`, `loads/<id>.{energy,power,onoff}.csv` and an optional `meta.json` (`tz`, `window_min`). Two columns, a documented but unparsed header, and ISO-8601 with offset; a naive timestamp raises (same rule as D-0224). A file name is the one piece of metadata a hand export always has.
**Rejected:** one wide CSV with a mapped header - the roles differ in kind, so it'd need a schema file, and partial exports become special cases.

### D-0229 · The total row sums money and windows and reports the worst month for the rest

`total` sums windows, `over_target`, coarse and estimated counts, days, fees and kWh; takes the max window; reports the highest month's metric, level and target; ANDs `gate`; and takes the worst confidence and reconstruction. Twelve months at 8.7 kW isn't 104 kW, and a mean would be a bill nobody was sent.
**Rejected:** a blank metric in the total - the worst month is what the gate asks about.

### D-0240 · Rotation clocks and zone choices live on `AllocState`, rebuilt from the constraints present

`starved_since` and `zone_choice` are rebuilt each tick from the group and zone constraints present and left alone on a tick without them. A tick without groups isn't one where nobody's starving. Affects D6 §4, §7.
**Rejected:** clocks inside the constraints - constraints are rebuilt from config every tick (D-0162).

### D-0241 · A zone's effect is a cap of zero with reason `zone_substituted`

The sources a zone didn't choose are capped to 0 W and shed with their own reason; the chosen one is uncapped. A cap is the one way a constraint acts (D6 §5.3), and INV-40 wants a reason. Affects D6 §5.7.
**Rejected:** re-ranking priority - can't express "off because the other is cheaper".

### D-0242 · A non-electric zone source's nameplate is its electric draw

A gas or district-heat source declares its pump or fan as its D6 nameplate; its fuel cost enters the zone's ranking through D1's carrier curves. Affects D6 §5.7, §6.
**Rejected:** one nameplate for both - 14 kW of capacity reserved for a 90 W pump.

### D-0243 · Zone hysteresis and dwell are wall-clock instants

`ZoneChoice.since` and `candidate_since` are instants; a switch needs its margin to hold for `switch_confirm_s` and the incumbent to have served `min_dwell_min`. Same behaviour at any tick rate. Affects D6 §5.7, §7.
**Rejected:** tick counts - a dwell that halves when the meter cadence doubles.

### D-0244 · After a switch the new source's dwell starts now

The incumbent's clock isn't carried over, so a pair can't flap around the threshold that caused the switch. A comfort-urgent member overrides the penalty and gets its own source back (INV-42). Affects D6 §5.7.
**Rejected:** symmetric hysteresis alone - the pair flaps every confirmation window.

### D-0245 · A running cycle reserves at least its nameplate

`CycleReservation` holds the profile's power for a running cycle through stages 1-3, never less than a relay load's nameplate; at stage 4 it's released and the cycle may be cut. A dishwasher's mean is 300 W and its heater 1.8 kW, and a cut cycle restarts from zero (INV-59). Affects D6 §5.2, §5.3.
**Rejected:** reserving the profile's mean - under-reserves the heater spike.

### D-0246 · The zone report carries floats and strings, not money

`ZoneReport` shows each source's price per kWh of heat and why one wasn't a candidate (`cop_below_floor`, `no_price`, `absent`). Summed money is D11's (INV-68). Affects D6 §5.9.
**Rejected:** `Money` in the report - two ledgers.

### D-0247 · A constraint's zero is a shed with its reason; a vetoed stop falls through to the floor

A load capped to 0 W is denied with the constraint's `shed_reason`, never a silent zero grant (INV-40). A modulating load whose stop is vetoed isn't denied; it's held at its floor (INV-28, INV-39). Affects D6 §5.3.
**Rejected:** a zero cap as a 0 W grant - the silent zero INV-25/40 forbid.

### D-0248 · The allocator seeds group and zone constraints from `AllocState` before `prepare`

`allocate()` calls `GroupCap.seed(...)` and `Zone.seed(...)` before `prepare(ctx)`, and rebuilds both on the way out (D-0240). Constraints are rebuilt every tick, so their memory has to come from the state or a starving loop is forgotten. Affects D6 §3, §7.
**Rejected:** `prepare(ctx, state)` on every constraint - six of eight have no memory.

### D-0230 · The domain objects are collaborators; `EngineState` carries their persistable projection

`Engine(site, meter, tariff, loads, constraints, accounting)` holds D3's, D2's and D6's mutable objects; what persists between ticks is a frozen `EngineState`, and the collaborators are restored from it before each tick, so the same state and inputs give the same tick. Affects D7 §3, §4.2.
**Rejected:** rewriting three finished domains frozen - no behavioural gain.

### D-0231 · The store sections are spelled twice, and a test compares them

`core/engine.py` defines `Section` with the same ten names as `storage.py`, and `test_sections_agree.py` asserts they match. `core/` can't import `storage.py` (INV-2). Affects D7 §2, §7.
**Rejected:** defining it in `core/` and importing it in `storage.py` - the cleaner direction, left for when the runtime lands.

### D-0232 · `SiteWarning`, not `Warning`

D7 §4.1's `Warning` shadows a builtin in the type the whole surface imports. Affects D7 §4.1.
**Rejected:** keeping the LLD's word.

### D-0233 · A closed window's ceiling for the PI is the flat target

The PI integrates a closed window's use against `target_w_at(window.start) × window_h`. D2 can still answer the flat target for a past window; the slack and free ride are properties of the moment. Affects D7 §5.1, D6 §5.1.
**Rejected:** persisting each window's live ceiling - one more number per window for a term the outlier gate already damps.

### D-0234 · `LoadView.level_now` is the engine's to fill

A thermal load's level is the temperature its comfort is measured on, an EV's its SoC; only the engine sees the reads, so it fills `level_now` from the comfort state or the reads. Affects D5 §4.
**Rejected:** each type exposing `level()` on the view - the reads and the view meet in the engine.

### D-0235 · `Snapshot` lives in `core/model.py` with forward references

HLD §5 places `Snapshot` in `core/model.py`; its section types are domain types, so they're annotated under `TYPE_CHECKING` to keep the import graph acyclic. `meter`, `budget` and `tariff` are optional: a failed tick republishes without them. Affects D7 §4.1.
**Rejected:** defining it in `engine.py` - D8's imports shouldn't depend on the engine module.

### D-0236 · A frozen tick applies nothing

On a frozen tick (stale meter, seam), the allocator returns last tick's grants and the engine skips `apply`: no command, not even a re-assertion, and every load is `held`. Blindness never opens a gate (INV-15, INV-17), and a rewritten limit can't be told from a new decision. Affects D7 §5.1.
**Rejected:** applying the held grants - the gate's interval would still let a write out during blindness.

### D-0237 · `safe_mode` is runtime state that's never stored

Safe mode enters after `safe_mode_after_failures` engine exceptions (every load released, site observing, repair `engine_failing`); it's written as `False`, so a restart clears it (D7 §2). Affects D7 §4.2, §7.
**Rejected:** persisting it until the repair is acknowledged - a restart is the household's acknowledgement.

### D-0238 · Peak warnings are edge events; the live warning has its own key

A coming window's warning fires once and clears once below `clear_fraction`; the live warning for the current window fires one event and one notification on its edge. The accounting hook is a protocol called from `plan()` oldest-first and unreachable from `tick()` (INV-68). Affects D7 §5.2, §5.4, §9 16.
**Rejected:** emitting every tick and de-duplicating in D8 - 360 events an hour for one fact.

### D-0250 · A thermostat's watts in the walk follow the grant, not the kind

Temperature kinds answer `effective_w = None`, which the engine's quantiser read as 0 W: every thermostat was charged nothing, and a tank under its floor "served" a 0 W grant with its resting setpoint. A non-held quantisation is now the element's nameplate when the grant covers it or a comfort violation overrides the ceiling, else 0 W. Affects D7 §5.1, D6 §5.3.
**Rejected:** each temperature kind returning `effective_w` - it doesn't know where the grant sits in the walk.

### D-0251 · Row 3b: a value already sent is held until read back, and a read-back older than the write isn't one

(a) A command equal to `last_value` is `held_settling` until `verify_due`. (b) After that, a read-back whose `taken_at` predates the write holds one more verify window. (c) Row 5 compares an upward move with the value sent. (d) `verify()` stays due for a stale read-back. `Reads.taken_at(role)` is HA's `last_reported`. The Easee limit is a poll old, and every write was being re-sent 60 s later against its own stale read-back: 106 EV writes on `flat_price_night`, 79 after. Affects D4 §5.10, §9 7.
**Rejected:** a two-poll `verify_after_s` - delays every real deviation on every device.

### D-0252 · A thermostatic load with no desired state gets `COMFORT` in its active slots

`plan_all` fills `Desired.COMFORT` into active slots of a thermostatic load when the strategy left it empty. D4 §5.4's tank charges on `desired is COMFORT`, so a `deadline_fill` tank never charged. Affects D5 §4, §5.1.
**Rejected:** every energy strategy emitting it - one line in the walk covers them all.

### D-0253 · An exhausted plan authorises no stop and gives way

D6's EV stop gate also requires the plan to draw again later or the load to owe nothing; otherwise the car is held at its floor until the next cycle re-cuts. In D5's adoption, an old plan with nothing active ahead yields to a new one that has something. On `flat_price_night` the car stopped and resumed two minutes apart over 0.17 kWh, and the tank sat on a plan whose only block had passed. Affects D6 §5.3, D5 §5.9.
**Rejected:** replanning the moment a plan runs out - the tick can't plan (D7), and a reversed stop is what INV-39 prevents.

### D-0254 · `heat_capacitor` holds on a flat day

On a flat local day every slot gets the median rank, so nothing banks or coasts; tariff peaks still coast and the slots before them still bank. Ranking equal prices by clock redrew the bank/coast line every cycle: 50 plan flips and 60 writes per loop on `flat_price_night`, nine after. Affects D5 §5.7.
**Rejected:** float noise as tie-break - INV-32's forbidden case.

### D-0255 · A load with no vote reserves its maximum in the planning headroom while it draws

In `plan_all`, a `PlanMode.URGENT` load reserves `demand.max_w` slot by slot until `required_kwh` is covered; an unknown requirement reserves the current slot. The EV kept re-planning each time the tank crossed its floor and took 3 kW. Affects D5 §5.1.
**Rejected:** reserving the whole horizon - a 40-minute recovery would starve the night plan for 48 hours.

### D-0256 · A tank within `READY_BAND_K` (1 K) of its target owes nothing

`wants` is `level < target − 1 K`, and `required_kwh` is 0 otherwise. At 74.4 °C against 75 no tank thermostat fires, and the loss-to-deadline term kept 0.4 kWh owed forever: re-cut plans every half hour and up to 114 setpoint writes a night. Affects D4 §5.12, §9 20.
**Rejected:** pushing the setpoint above target to force a reheat - setpoints never walk (INV-29).

### D-0257 · Plans are cut to the ceiling the ladder defends

`Headroom.build` uses `(target_w − ε_w) × 0.95 − baseline`: the ε-reduced ceiling at the ladder's stage-2 threshold. Cut to the bare target, every full-power slot opened at 103 % and the ladder went straight to stage 3 and shed the bathrooms for the EV. Affects D5 §5.1.
**Rejected:** letting the tick trim - stage 3 sheds comfort before it trims (D6 §5.4).

### D-0258 · `heat_capacitor`'s rate-limited ramp continues from the delta in force

The ramp starts from the previous plan's `desired_state_at(now)`, not zero. Starting at zero each cycle wrote a sawtooth of two setpoints per quarter hour on a Z-Wave thermostat allowed one per ten minutes. Affects D5 §5.7.
**Rejected:** rate-limiting in the type - the plan owns the ramp (D4 §9 19).

### D-0259 · The floor is physical: the lowest setpoint written is the floor plus half the swing

`floor_heating` adds half of `swing_k` to the shed setpoint and the clamp. A bathroom with floor 21 °C and swing 1 K is never told less than 21.5 °C: a thermostat holds its setpoint ± half its swing, so a setpoint on the floor is a floor violated half the time. Affects D4 §5.4, §6.1, §9 20.
**Rejected:** judging violations with the swing instead - INV-55 promises a temperature, not a dial position.

### D-0260 · The runner plans on D7's triggers and reads INV-32 as unforced commitment breaks

The scenario runner plans at quarter-hour + 20 s, at startup, after a restart and on plug edges. Its price pipeline is the NO site's own (the preset's components as a `tou_schedule`, D-0126). Its INV-32 metric counts a committed slot re-cut without an input change or a smaller requirement; forced re-cuts are `plan_recuts`. The catalogue is Python, the BLE simulator polls every 30 s on a seeded phase, and the tank's differential is 2 K. Affects D9 §5.2, §5.3.
**Rejected:** a YAML catalogue - Python keeps the house builder typed.

### D-0261 · The curve and the plan are indexed; day figures and the period metric memoised

`PriceCurve` and `Plan` build never-compared indices in `__post_init__` and bisect instead of walking. The engine memoises a curve's local-day figures, and the evaluator memoises behind a non-persisted `PeakHistory.revision`. D9 §5.9 wants ≥ 500 ticks/s; slot walks and re-derived day stats were two-thirds of every tick, and the runner went from 110 to 420 ticks/s. Nothing observable changes, and a test pins the indexed lookups to the linear ones. Affects D1 §4, D5 §4, D7 §4.1, D2 §5.
**Rejected:** a coarser tick - D9 §5.9 forbids it. Caching in the runner - HA's tick pays the same cost.

### D-0262 · A start role is pressed, never released

The `switch` kind on a `START` role sends a command only when the grant says go and the programme isn't running. A `start_program` action or a button has no off and no state to read back, so the benchmark dishwasher was writing `START = False` every 60 s all day. Affects D4 §5.6, §5.13.
**Rejected:** a read adapter that answers the press - a button's state is its last press time.

### D-0263 · An appliance with no hours to fill is on call

A `generic_switch` with `hours_per_day = None` (sauna, hot tub, other) wants power only when the household has the relay on, when our shed left it wanting, or under force. Otherwise the walk granted the sauna 6 kW on a Saturday afternoon nobody asked for. Affects D4 §6.7, §9 21.
**Rejected:** on-call appliances only under force - pressing the sauna's own button should heat it.

### D-0264 · The first benchmark baseline controls every load

`nordic_detached@1` steers all twelve loads (`controlled_share = 1.0`); the builder still meters what it doesn't steer (D9 §9 11). Zero-class tolerances start as `not_worse`, and the money columns appear once accounting is wired in. A fully controlled baseline is the strictest gate for later work. Affects D9 §5.9, §5.11.
**Rejected:** EV and floors only - later work would be rewarded for flipping a flag.

### D-0265 · A panel heater's floor is physical too

The `radiator` type lifts its comfort target, shed setpoint and clamp by half a band (`swing_k`, default 1 K), like floor loops (D-0259). Under `away` a plug that closes only below target let a bedroom fall to 16.9 °C against a 17 °C floor. Affects D4 §5.4, §6.5.
**Rejected:** tolerating a tenth of a kelvin - the promise is a temperature.

### D-0266 · A restore that serves a violated comfort floor is urgent

`switch`, `setpoint` and `mode` mark a turn-on or turn-up urgent while `comfort_violated` is set, buying past the interval and dwell (rows 6-7), never past row 3. A bedroom heater sat 30 minutes behind its `min_off` dwell after falling through its floor. Affects D4 §5.6, §5.10.
**Rejected:** a shorter relay dwell - the dwell protects the device, and a compressor's must stay.

### D-0267 · One bridge module between D7 and D11

`core/accounting_hook.py::AccountingAdapter` alone knows both D7's `SlotClose` and D11's `ClosedSlot`/`CloseCtx`; INV-68's AST check forbids the engine importing accounting. The engine meters site import and export under reserved ids, keeps the last 48 closed windows, starts a fresh ledger at the earliest integrated slot (otherwise 1 January opened in December), and hands a window over on the first slot close past its end. D11 sums a window's counterfactual from its own slots, so a late register report doesn't lose it. The section codec moves to `core/state_codec.py`. Affects D7 §4.2, §5.1, §5.2, §7; D3 §5.12; D11 §5.1.
**Rejected:** the engine building `ClosedSlot` from D11's types - "only the types" is the exception every later import would cite.

### D-0268 · `nordic_detached@2`: the loops answer their loss, the car carries its own limit

The benchmark's floor loops answer `loss_coeff_w_per_k` with a fit against their own slab (0.745 W/m²K × area, the one-node value D10 converges to), and `EvSim` gains the car's own app limit (80 %). Without a loss term the slab shadow drew nothing, and a car with no limit charges to 100 % uncontrolled while the shadow stops at the target. D4 §6.4's 0.7 left the shadow 10 % under the floor's real draw. Radiators keep no loss coefficient until D10 fits one. Baseline reset with a changelog line (D9 §5.11). Affects D9 §5.9, §5.11.
**Rejected:** asserting only on the EV - a slab shadow that can't calibrate on the reference house won't anywhere. A D4 derivation for the coefficient - D4 §5.7 leaves it to D10's fit.

### D-0269 · What the shadow holds and what the car asked for

(1) The shadow's target is the load's target profile under the current presence, not `Demand.comfort.target`: under `heat_capacitor` that's the plan's eco setpoint, and the counterfactual reproduced the plan. (2) The plug-in shadow latches `required_kwh + measured_kwh` at the edge, charges what it still owed in the slot `wants` falls, and a later edge re-latches only what the car spent since; the charger's link drops made every return look like a plug-in, and the EV counterfactual read 225 kWh against 64 real. (3) A shadow with no level takes the first it sees. Affects D11 §5.3.
**Rejected:** reading the demand a cycle earlier - the planning loop only sees it at closes.

### D-0270 · The modifier registry decodes what the entry stores

`registry.build(key, options)` decodes stored options against the schema (decimal strings to `Decimal`, lists to tuples), and modifiers with record lists (`tou_schedule`, `day_type`, `cumulative_tier`) declare `from_options`. A preset's `tou_schedule` arrived as dicts and every Tensio site's first planning cycle failed on `period.when`. Affects D1 §6.
**Rejected:** decoding in `runtime.py` - one branch per record-bearing modifier.

### D-0271 · Six things the runtime settles

`runtime.py` holds `build_site(hass, entry)` and `Runtime` (D7 §4.3, §5.3, §5.5, §5.6). (1) Grid power, production and load entities share one 10 s debounce. (2) The window fallback checks the register reported since the boundary (`last_register_at`), since "rolled" is always true by :05. (3) Loads and their devices ride on `SiteBuild`. (4) The first price fetch starts after the first tick, outside the lock (INV-46). (5) Every subscription is released by one unload hook and by `stop()`, which unload and `homeassistant_stop` both call. (6) A stale `engine_failing` repair is deleted at `start()`. Affects D7 §4.3, §5.3, §5.5, §5.6, §8, §9.
**Rejected:** subscriptions only through `entry.async_on_unload` - kept, plus `stop()`, so shutdown is clean too.

### D-0272 · Safe mode releases sheds; a plan's coast setpoint isn't one

Safe mode is D4's `release()` per load: sheds undone, site observing. A setpoint a plan moved within the comfort band stays; the next start's `restore()` corrects it (INV-27). Re-targeting to comfort would be a restore, a write decided by an engine that has just proven it can't be trusted. Affects D7 §8, D9 §5.3.
**Rejected:** restoring comfort in safe mode - the failing engine would be writing setpoints.

### D-0273 · Event payloads follow D8's table, and one that doesn't is dropped

`events.py` has one voluptuous schema per `EventKind` (listed fields required, extras allowed) and the runtime validates before firing. The engine's payloads were brought in line with D8 §5.6 (`stage_changed`, `breach`, per-load `comfort_violation`, `device_unhealthy` with recovery, new `level_changed`, `month_closed` enriched from the ledger). A drifting schema becomes a logged exception in tests rather than a missing key in someone's automation. Affects D8 §5.6.
**Rejected:** documenting the payloads as they were - D8 §5.6 is what automations are written against.

### D-0274 · Notification texts live in code, not in `strings.json`

`notifications.py::TEXTS` holds each category's title and body in `en` and `nb`, filled by `render(category, params, language)`. hassfest validates `strings.json` against HA's own list of sections and fails on a custom `notifications` one, and hassfest is a gate (D8 §9 13). Affects D8 §5.8, §5.11.
**Rejected:** hiding them under `exceptions.*` or `issues.*` - misuses sections whose meaning HA checks.

### D-0275 · The site surface: restored knobs, one repairs catalogue, no empty rows

(1) `switch.<site>_active`, the three selects and `number.<site>_margin_kwh` are `RestoreEntity`s that push their state into the runtime on add; the runtime keeps knobs in memory only (INV-47). (2) `repairs.py` is one catalogue with severity and fixability per id, used for both engine and runtime conditions, with a fix flow for `engine_failing`. (3) The notification policy's `last_sent` lives in `EventsState.last_sent`, the `events` section D8 §7 names. (4) A row with no action behind it yet isn't shown. (5) `select.<site>_target` offers `auto`, the tariff's steps, or the configured kW for a stepless tariff. Affects D8 §5.5, §5.7, §5.9; D7 §5.6, §7.
**Rejected:** knobs in the store's `runtime` section - that shape is the engine's. Disabled placeholder buttons - disabled-by-default is for noise, not for absence.

### D-0276 · The roast is judged at 3 kW, and a cleared warning is a notification too

`oven_sunday_roast` runs with `target_kw = 3.0`: the roast alone (2.5 kW on 0.63 kW base) crosses it whatever the controller does, which makes the row checkable (the PI doesn't move across the breaching window, INV-35; the reserve is back next window; the warning lands 21 minutes ahead). At 10 kW nothing would warn. The EMA's 900 s time constant sets the lead: about 14 minutes after the oven goes in. A cleared warning now also emits `Notification(cleared=True)`, so the policy dismisses the persistent notification. Affects D7 §5.4, D9 §5.3.
**Rejected:** skipping the row until the baseline-aware reserve - the EMA ships until then, and its lead is the number to beat.

### D-0277 · A register report that never comes costs one window, not the rest of the day

When a pending window closes on the integral and had its own anchor, the next window's anchor is `anchor + integral` (`WALL_CLOCK`); a report arriving while the pending window has no anchor closes it on the integral and re-syncs the current one. The meter simulator's repeated frame (2 % of hours, as the real AMS does) otherwise left every later window closed on the integral: the re-syncing report was consumed by a pending close that couldn't use it. Affects D3 §5.5, §8, §9.
**Rejected:** closing the pending window from the next report - same arithmetic on the wrong window, and two repeats in a row still leave it unanchored. Treating a repeated value as a report - a stale frame and a quiet hour look the same.

### D-0278 · The `e2e` day: one stepping, the gate's two definitions, a pytest marker

(1) The household, simulators and meter step in one `HouseDriver`, shared by `run_scenario` and `tests/e2e/fake_house.py`, so both days are the same house at the same instants. (2) `fake_house` publishes after firing due timers, so a tick reads the sample pushed ten seconds earlier, as in a real house. (3) D9 §5.10's numbers: projection error over each window's last quarter at p95, and warning lead measured to the instant the window's energy crosses the ceiling, for windows that raise the metric. A car plugged in at 16:25 makes a 16:00 peak no warning could precede. (4) The site binds power and both registers, not the AMS hour accumulator, which has no `last_reset`. (5) `e2e` is `pytest -m e2e` on a weekly workflow. (6) The mid-day restart writes the store to disk as HA would. (7) The site starts in observe at `step:2`. (8) A bus payload never carries envelope keys: `events.build` refuses them, so the peak warning's kind is `warning`. Measured: 11 339 ticks, 24 windows, 2 over, projection p95 0.292 kWh, 35.5 min lead to the crossing. Affects D8 §5.6; D9 §5.2, §5.10, §6, §9 10.
**Rejected:** lead to the window start for every window - unattainable for the plug-in window, and a waived gate is no gate. A `benchmark.py` tier - the tool has no `hass`.

### D-0279 · A stored `NUMBER` option decodes to the modifier's own field type

`modifiers.build` decodes `NUMBER` options as it already did `MONEY`: to whatever the modifier's dataclass annotates (`Decimal`, `float` or `int`). A flow-made site stored `vat.rate = "0.25"` and the first price fetch raised `TypeError`; no test had built a modifier from stored data. Affects D1 §6.
**Rejected:** `Decimal` everywhere - some fields are compared with D3's floats. Storing numbers as numbers - `Decimal` defaults would become binary floats (D-0123).

### D-0280 · The runtime restores the tariff evaluator at start

`Runtime.start` calls `Evaluator.restore(state.tariff)` after reading the store and before the accounting adapter or the engine take the history. The section was written every tick and never read back, so a reload mid-month billed the month from that moment (12 of 24 windows on the `e2e` day). The pure runner kept the evaluator object across its restart, which is why no scenario saw it. Affects D7 §5.5.
**Rejected:** restoring on the engine's first tick - the adapter has already taken the old history object.

### D-0281 · The `ev` type: connected words, the calendar deadline, the blocked notice, the plug-in edge

(1) `de_authorizing` is connected; the cable is in. (2) The deadline is the earlier of the weekday table and the bound calendar's next event; an empty calendar is silent. (3) A charger that shows granted (limit ≥ `min_a`, enable on) but draws under 100 W for 180 s is blocked: one WARNING per reason naming `blocked_by`. (4) The engine fires `ev_connected` on both edges, never on the first observation, and the runtime plans on it; a plan for a car that has left keeps a reservation for up to a quarter hour. (5) `LiveDevice` is D7's `LoadDevice` over a `BoundDevice` with a fresh view each tick. Affects D4 §5.11; D7 §5.2; D8 §5.6.
**Rejected:** the calendar replacing the table - a household that keeps both wrote both. The blocked notice in the profile - the three-minute rule is the type's, and generic profiles have no `blocked_by`.

### D-0282 · The load flow, the load device and the knob path

(1) `LoadSubentryFlow`: device, match, questions, review; `reconfigure` → questions → a review with the re-derive diff. The subentry stores the answers, `device_id`, the full bindings and `manual_overrides`. (2) The reconfigure review shows re-derived values under Advanced, and an edit is whatever differs from what was shown. (3) One `LoadEntity` base and one builder per platform in `load_entities.py`, plus a `time` platform. (4) Parameter knobs push into `Runtime.load_params`; the engine merges them over the configured load each tick and rebuilds the target profile when a comfort key moved, keeping store and gate state (INV-47). (5) `runtime.build_loads` builds loads and devices from subentries, and a broken subentry doesn't stop the site (INV-53). (6) Labels for every question, option, type, profile and strategy are generated from the registries, en and nb. Affects D7 §2, §5.5; D8 §5.2, §5.4, §5.5.
**Rejected:** rebuilding the `Load` each tick - discards the store model's state. Hand-written translations - eight types, ninety questions, two languages.

### D-0283 · Circuits: the reading rides in `Inputs`, the spec in the subentry

(1) `Inputs.circuits` maps each sub-metered circuit to this tick's `MeterSample`; the engine passes `None` where it's not OK or older than 300 s, and the constraint falls back to its members' figures (INV-17). (2) `CircuitSpec` is the subentry's circuit, and `spec.limit(electrical)` converts amps with the site's `w_per_amp`. (3) `Engine(constraints=)` are the constraints beyond the hard limits; the engine always adds fuse and phases. `Constraint.key` is an instance member. (4) `CircuitMeter` reads one power entity. (5) `runtime.build_circuits` builds specs and meters, dropping a member that's no longer a load. (6) The circuit subentry owns membership; the load's `circuit` key stays `None`. (7) `CircuitReport.sub_meter` says where the figure came from. (8) A circuit breach fires `breach` once per edge. Affects D3 §3; D6 §5.8, §6, §8; D7 §3, §4.1, §5.5; D8 §5.3, §5.6.
**Rejected:** rebuilding `CircuitLimit`s every tick - the engine already holds mutable collaborators (D-0230).

### D-0284 · A circuit is budgeted like the site

Supersedes D-0167's circuit half. (1) `unseen_w = max(0, sub_meter − Σ measured(members))`; `cap_w = fuse − unmetered_w − unseen_w − Σ reserved(members decided before)`. (2) `post()` is a blunt violation only when what the grants leave standing exceeds the fuse; a modulating load counts its grant, a relay its reservation or reading. (3) A relay whose cap doesn't cover its nameplate is denied, never partly granted. (4) A scoped restate authorises a charger's stop on the site's blunt terms and carries `stop_ok`. With D-0167's arithmetic, a sauna lit beside a charger at 32 A breached on the next reading, both went to stage 4, and a 90-minute sauna evening became a 15-minute flap. Affects D6 §5.3, §5.8, §9 14.
**Rejected:** a tolerance or hold before breaching - delays a real breach as much as a transient; the flaw was charging members by measurement while re-granting them.

### D-0285 · The circuit flow is one step and a review

`CircuitSubentryFlow`: `user` (name, fuse A, phases, members, optional power-sensor sub-meter, `unmetered_w` under Advanced) → `review` (D6 §6's sentence, `last_step`) → subentry; `reconfigure` pre-fills. A site without loads aborts `no_loads`. INV-67 wants the review before saving. The scenario runner also returns the sauna's mode knob to `auto` when a session ends; a sticky knob kept it forced until `force_max_h`. Affects D8 §5.3, D9 §4, §5.3.
**Rejected:** the review sentence in the `user` step - it needs the answers. A circuit select in the load flow - two owners for one relation.

### D-0286 · The subentry snapshot is plain values, because `ConfigSubentry` mutates itself

`_subentry_snapshot()` returns `{id: (type, title, dict(data))}` and updates diff the previous snapshot against a fresh one. `async_update_subentry` replaces `data` on the same `ConfigSubentry` object, so a snapshot of the live objects has its "old" values overwritten by the change it's meant to detect.
**Rejected:** diffing `entry.modified_at` - doesn't say which subentry changed.

### D-0287 · A load's entities carry its subentry id; the site's never do

`Runtime.setup_load_platform` adds the site's entities without `config_subentry_id` and each load's in its own call with `config_subentry_id=load.load_id`. HA removes an entity with its subentry only when that id matches; without it the entity outlives the load. Affects D7 §2, §9 9.
**Rejected:** removing by enumerated unique ids - a second copy of D8 §5.5's table.

### D-0288 · The Snapshot carries D11's full site and load figures

`AccountingStatus` carries what D8 §5.5's attributes need: `pricing_confidence` (distinct from savings `confidence`), the cost and savings components, `estimated_share`, previous-month and lifetime figures, `month_start` for `last_reset` and `since`. `LoadStatus` gets `lifetime_kwh` and `energy_source`. `SnapshotSchema` 3. D11 already computed them all. Entities read the Snapshot only (D7 §4.1).
**Rejected:** sensors reading the adapter directly - state could change between coordinator updates with nothing calling `async_write_ha_state`.

### D-0289 · `period_closed` fires on the ledger's month edge, priced by D2's `bill()`

Beside `month_closed`, the engine rebuilds D2's `Period` and prices it with `bill(period)` and `bill(period, history.counterfactual())`, emitting period, level, metric, fee, counterfactual fee and capacity savings. Every shipped preset's period is the month. One code path prices both worlds, as D11's capacity savings already do (INV-69). `TariffModel.history` joins the protocol.
**Rejected:** a separate rollover watch for yearly periods - dead code until such a preset exists.

### D-0290 · `savings_low_confidence` is per load, watched HA-side

`RepairsWatch` tracks per load how long `savings_confidence` has read `low` unbroken and raises `savings_low_confidence_<load_id>` after seven days, named with the load's name. D11 applies the threshold; the duration clock is the same pattern `scaling_mismatch` already uses in `repairs.py`.
**Rejected:** a clock in `core/accounting` - a second mechanism for one repair.

### D-0291 · The review's shadow sentence is a fixed table by type

The load review adds one sentence naming what savings are measured against, by type ("would hold 24 °C on its own thermostat"; for an on-call appliance, "its savings are not shown"). D11 §6's per-load `counterfactual: none` opt-out isn't built: nothing depends on it, so a shadow's presence decides whether savings show.
**Rejected:** building the opt-out now - a new question threaded through every type for no current need.

### D-0292 · The group subentry owns the relation; the default cap is a suggestion

`GroupSubentryFlow`: one step (name, members, `max_concurrent_w`; `from_stage`, `ceiling_fraction`, `starve_seconds` under Advanced) → review → subentry. Like circuits, the group owns membership. D6 §6's default ("the two largest members' nameplates") can't react to members picked in the same step, so the box is pre-filled from the site's loads' stored nameplates as a starting point the review lets the household correct. Affects D6 §6, D8 §5.3.
**Rejected:** reading `runtime_data` for exact nameplates - flows answer from subentry data only. Two steps - D8 §5.3 draws one.

### D-0293 · Groups are rebuilt beside circuits, and a newly grouped load gets its rows without a reload

`_reload_relations` rebuilds circuits and groups together and re-adds the entities of any load newly named in a group; HA skips existing unique ids, so only the new `starved_s` row lands. Loads come first and groups after, so this is the normal path, not an edge case. Affects D7 §2, D6 §6.
**Rejected:** a single-entity re-add - no cheap way to pick one platform's builder, and the whole re-add is proven code.

### D-0294 · `sensor.<load>_starved_s` reads the allocator's own rotation clock

`LoadStatus.starved_s` is `AllocState.starved_since` for the load as elapsed seconds, 0 without a clock; `SnapshotSchema` 4. The row exists only for loads a group names. `GroupCap` already keeps this clock for its ranking (D-0240), and re-deriving it in the HA layer could drift from the number that decides admission. Affects D8 §5.5.
**Rejected:** publishing the queue position - D8 names a duration, and seconds already exist.

### D-0295 · `Observation` gets five flat `legionella_*` fields, not the type's `Legionella`

`legionella_due_at`, `_last_completed`, `_active`, `_in_progress` and `_at_risk`, filled in `Load.observe()` by the same duck-typed dispatch as `connected`/`soc`. `base.py` never imports a concrete type, and `Legionella` holds tank-specific fields nothing else shares; consumers only ever need one or two fields. Affects D4 §5.12, D7 §4.1.
**Rejected:** moving `Legionella` to `core/model.py` - not shared. Recomputing due dates in the engine - a second copy of §5.12's arithmetic.

### D-0296 · `powerplan_legionella`'s four states are four one-way edges

The engine tracks `due`, `started`, `at_risk` and `completed` per load and fires on each rising edge (or a new completion time), never on first observation, the same pattern as circuit events. `due` and `started` currently fire together for the water heater, but D8 names four states and a future type may separate them. Affects D4 §5.12, D7 §5.1, D8 §5.6.
**Rejected:** merging `due` and `started` - D8's schema has four independent values.

### D-0297 · `sensor.<load>_next_legionella` is a table row

A `LOAD_SENSORS` row (timestamp, `applies` when the type is `water_heater`) reading `LoadStatus.legionella_due_at`. Whether a load has a legionella cycle is a fact of its type, decidable from the `Load` alone, unlike group membership. Affects D8 §5.5.
**Rejected:** a dedicated class like `starved_s` - that one needed runtime state; this doesn't.

### D-0298 · The heat pump's outdoor and outlet sensors are bound after `derive()`, replacing only what they answer

`_extra_bindings` turns `heat_pump`'s `outdoor_entity` and `outlet_entity` answers into `RoleBinding`s through the same `numeric_binding()` the match step uses, and replaces a match-step binding only for a role actually answered, so a blank field keeps `generic_climate`'s own detected outdoor sensor. Without it `OUTLET_TEMP` was never bound and defrost detection could never fire. Affects D4 §5.14.
**Rejected:** folding them into the match step - the household may point at a sensor on no device the match step ever saw.

### D-0299 · `heat_pump_defrost_evening` exercises "no shed" end to end, and the PI needs no exemption

`kind_ctx()` already forces `shed=False` while defrosting (INV-29), but it reads `OUTLET_TEMP`, which only D-0298 binds. The scenario runs a simulated defrost through the real binding and engine: six defrost runs, none shed, `over_target` 0, no comfort minutes; the evening's one stage-2 shed lands 22 minutes clear of a defrost. "Window excluded from the PI trim" needs no code: a setpoint load's reservation tracks its measured draw, spike included, so the PI never sees an outlier. Affects D4 §5.14, D9 §5.3.
**Rejected:** a defrost carve-out in the PI trim now - it changes no result, and should be written against a failure when one appears.

### D-0300 · A second read-only action: a schedule helper's week

`providers/schedules/ha_schedule.py` calls `schedule.get_schedule` with `return_response=True`. A `schedule.*` entity's state is only on/off with `next_event`; the weekly table is private and not recorded, and this `SupportsResponse.ONLY` action is the public way to read it. `HaScheduleEntity.target_at(t)` must answer for 24-48 h ahead, which the live state can't. INV-3's single-writer rule is about device writes; `test_single_writer.py` machine-checks `return_response=True` here as for D-0080. Affects HLD §5, D9 §5.7, D4 §4.4, D8 §5.2.
**Rejected:** polling the live state - can't tell the plan about a change before it happens. Reading the component's private `_config` - no contract at all.

### D-0301 · `fetch_windows`: `None` for "couldn't read", `()` for "empty"; on/off reuse `comfort_c`/`vacation_c`

`fetch_windows` returns `None` when the call raised or the response omits the entity, and `()` when all seven days are genuinely empty. `_hydrate_schedule` swaps in an `HaScheduleEntity` only when it isn't `None`, so a transient failure can't turn into a schedule that's off all week. The schedule's on and off values are the load's own `comfort_c` and `vacation_c` ("on means comfort, off means setback"). Affects D4 §4.4, D7 §5.5.
**Rejected:** a separate on/off pair per type - a third pair of numbers nobody asked for. Treating `None` and `()` alike - ignores a real schedule that's empty some days.

### D-0302 · `arrival_sources` reuses the calendar plumbing, made plural

The four thermal types get an `arrival_sources` entity question; `_calendar_events` gathers from both `calendar_entity` and a profile's `arrival_sources` through the same `_one_calendar_event`, and subscribes to all of them. The flow's multi-entity keys become a set (`never_switch`, `arrival_sources`) with domain filters for calendars and schedules. `Answers.as_json` now turns any tuple into a list, since an untouched tuple default didn't survive a store round trip. Affects D4 §4.4, D7 §5.3, §5.5.
**Rejected:** a separate path for thermal arrivals - same question, different cardinality, twice the parsing.

### D-0303 · `switch.<load>_follow_presence` is its own entity class

A `SwitchEntity` (config, disabled by default) for thermal loads with a target profile, writing through the existing `async_set_load_param` path; the engine already rebuilt the profile on `follow_presence`. A boolean isn't a row in the `ParamNumber` table, and `LoadForceSwitch` is the precedent. Affects D8 §5.5.
**Rejected:** a `ParamSwitch` table for one row - a table of one is the class it replaces, plus indirection.

### D-0304 · The `on_request` shadow: `run_started_at` and the default profile

`OnRequestShadow` runs a cycle's programme from the request instant in its ten-segment shape (D11 §9 7). `ShadowState.run_started_at` is separate from `anchored_at`, which the generic driver overwrites on every re-anchor; it stays set after the programme ends until `demand.wants` falls, so a lingering request doesn't restart the programme. `LoadParams.cycle_profile` comes from `appliance_cycle.profile_of(load)`, the type's default profile, like every other shadow's parameters. Affects D11 §5.3, §9 7.
**Rejected:** counting slots since the request - slot length changes across DST and granularity changes.

### D-0305 · `CycleReservation` is built fresh every tick inside the engine

`Engine._cycle_reservations` builds one for each load whose cycle is `STARTED` or `RUNNING`, from its profile, and splices them into `allocate()`'s constraints beside the structural ones. It needs this tick's observation, so it can't live with circuits and groups, which change only on subentry edits; `ContractedPowerLimit` is already built per tick the same way. Until now the allocator's protection had only been tested against a hand-built object. Affects D6 §2, §5.7.
**Rejected:** building it in `runtime.py` - it needs no I/O and would drag `LoadState` across the HA boundary.

### D-0306 · `powerplan_cycle`'s states come from `CycleState.notify_state`

`notify_state` maps the six phases to D8's four: finished, aborted, started (`STARTED` or `RUNNING`), and planned (idle with `requested_at` set). Nothing ever assigns `CyclePhase.PLANNED`, so reading it literally would never fire. `Observation` carries `cycle_state` and `cycle_started_at`; `_cycle_events` detects edges on one string. Affects D4 §5.13, D8 §5.6.
**Rejected:** making `latch()` assign `PLANNED` - changes a tested state machine to simplify one read.

### D-0307 · The smoke baseline's comfort minutes rise with the cycle reservation

`total.comfort_violation_min` went from 10.5 to 12.0 once `CycleReservation` was wired. Re-running the January week before and after the change in isolation: 9.83 vs 10.17 minutes, on the same two bedroom radiators (17 °C floor, 0.2 K swing), the tightest comfort margin in the house by design. Reserving the dishwasher's power ahead of the walk takes flexibility the site shouldn't have had. The same update also records the small, constant delta D-0277 caused, whose baseline update had been missed. Baseline updated with a changelog line (D9 §5.11).
**Rejected:** tuning the house so the radiators absorb it - flatters a metric. Gating the reservation - INV-59 is why it exists.

### D-0308 · `observe` and `opportunistic` aren't strategies; `battery` defaults to `always` for now

Eight types listed `"observe"` among their strategies and two listed `"opportunistic"`, neither registered, so picking one in the flow would `KeyError` on the next tick; `battery`'s default `peak_shave` wasn't registered either, breaking every new battery on its first tick. Observe is a mode (`select.<load>_mode`), and `opportunistic` is a combinator (D5 §5.11). Both are removed, the battery defaults to `always` until its strategies exist, and a test asserts every type's strategies are registered. Affects D5 §3, §10; D8 §5.4.
**Rejected:** registering trivial `Observe`/`Opportunistic` strategies - duplicates the mode select and the combinator. Hiding batteries from the flow - covers one bug with another.

### D-0309 · `ContractedPower` already exists; the benchmark houses split two now, four later

The plan still listed the `ContractedPower` node as open, but the model node, evaluator, allocator constraint and preset loader are all in place and tested (D2 §9 12). What's left is D9 §5.9's other houses: `nl_pv` and `be_quarter` now, `fi_linear`, `es_contracted`, `us_demand` and `fr_tempo` in a follow-up. `nl_pv` alone needs a PV production simulator, and each other house is similar weight again.
**Rejected:** all six at once - six houses of simulator work in one package, where every other package this phase is one feature.

### D-0310 · `WeatherSim` takes a latitude; a PVWatts-style `ProductionSim` joins `sim/`

`WeatherSim.latitude_deg` feeds only the sun-angle formula; the temperature tables stay the default climate's, and the default latitude keeps `nordic_detached` byte-identical. `nl_pv` passes Amsterdam's latitude. `ProductionSim` turns irradiance into export watts: `rated_kwp × irradiance/1000 × 0.859` (PVWatts' default losses), clipped at the rating; `HouseDriver.step` adds it to the meter. The house's point is the roof, the tariff and the price shape, not a Dutch climate.
**Rejected:** a Dutch climate-normal table - research for a claim this house doesn't make. A temperature-coefficient derate - PVWatts' losses already include one.

### D-0311 · `sim/prices.py` gains `SOLAR_GLUT`; `be_quarter` reuses `SPOT_LIKE` in EUR

`SOLAR_GLUT` has a midday trough, an annual mean of €87/MWh (TenneT's 2025 market update) and extra volatility April-August, so the deepest hour goes negative on roughly a third of summer days and rarely otherwise, tuned against TenneT's negative-hour counts rather than fitted, which the module's `SOURCES` say. `be_quarter`'s point is 15-minute windows and a rolling 12-month average, not a Belgian price curve. `_curves` now reads the house's currency instead of hardcoding NOK, which stopped both houses at the first slot close.
**Rejected:** fitting exactly to the yearly negative-hour count - a market-wide count isn't a per-hour probability. Forking `_curves` - it reads everything else from the house already.

### D-0312 · `nl_pv` and `be_quarter`: TN 400 V, real holiday calendars, their own price regimes

Both reuse `nordic_detached`'s twelve loads and simulators ("same generators", D9 §5.9) behind a `TN_400`, EUR site and their own presets (`nl/connection`, `be/fluvius`) with the country's calendar. Their benchmark wrappers take the shared year's start and weather events but not its Norwegian price regimes, which would overwrite the one thing each house exists to prove.
**Rejected:** their own synthetic year - shared fault and weather instants are what make runs comparable. Researching NL/BE fuse conventions - the fuse is wiring realism, not the house's point.

### D-0313 · A `ForecastHook`, mirroring `AccountingHook`, feeds D10's baseline from slot closes

`ForecastHook.close_slot(SlotClose) -> ForecastClose` is called from `_close_slots` off the same object as accounting; `plan()` folds its state into `EngineState.forecasts` and fires `baseline_ready` on its edge. `ForecastsAdapter` accumulates controlled loads' slot energy and updates `HourOfWeekBaseline` only when a window closes, then reads the confidence of the bin it just updated (the window's start, not the slot's end, which is the next bin). The engine imports neither accounting nor forecasts (INV-2). Affects D7 §5.2, D8 §5.6.
**Rejected:** folding it into the accounting hook - couples D10 and D11. `baseline_ready` as a level - D10 §8 says "first time".

### D-0314 · `weather_entity.py` joins the read-only-action allowlist; confidence decays linearly to 0.6 at 48 h

A weather entity's state holds only the current condition, so `WeatherEntitySource` calls `weather.get_forecasts` (hourly, `SupportsResponse.ONLY`), the fourth file on `READ_ONLY_ACTION_CALLERS`. Confidence is 0.9 for 24 h, then linear to 0.6 at 48 h and held there, so age alone never makes a forecast "synthesised". The physical outdoor sensor override (D10 §5.4) waits for `Inputs.outdoor_c` to be populated, which is D4's binding work. Affects D10 §5.4, §9 6.
**Rejected:** wiring the sensor override from the forecasts provider - reaches into D4's role binding from the wrong direction.

### D-0315 · `recorder_baseline.py` seeds from the site register; per-load sources and fits stay unwired

`async_seed` reads the import register's short-term and long-term statistics and folds them through `uncontrolled_history` into the same `update()` calls the live adapter makes. No controlled load's history is read yet, so the seed is D10 §2's `none` grade: biased high, conservative for the reserve. The live path already subtracts every controlled load's measured energy, so what the baseline learns from install on is exact. `fit_all()` isn't called; its consumer is D6's reserve. Affects D10 §5.2, §9 5.
**Rejected:** per-load sources now - a second recorder surface for a one-time bias the 28-day half-life works off anyway.

### D-0316 · `Inputs` carries `forecast_confidence` and `forecast_ready`

The runtime fills both from the real baseline and the engine republishes them in `ForecastStatus`. `Inputs.forecasts` is D5's narrow protocol, which deliberately has no confidence (D-0217). Affects D7 §4.1.
**Rejected:** importing `OFFER_CONFIDENCE` into the engine - the first crack in the one-way import rule.

### D-0317 · `rebuild_baseline` is a full reset, not a blend

`button.<site>_rebuild_baseline` (config, disabled by default) and `powerplan.rebuild_baseline` replace the baseline with a fresh one and re-seed. D10 §8's use case is a lifestyle change (new EV, new tenant), where the point is skipping the month-long wait. `rebuild_peak_history` isn't built yet and the services docstring says so. Affects D8 §5.5, §5.7.
**Rejected:** blending old bins at reduced weight - the half-life already is a continuous blend.

### D-0318 · The site review names the detected weather entity and the baseline's timeline

The review's "Forecasts" paragraph names the first loaded `weather.*` entity and, with a meter, that the baseline is offered after about two weeks. It doesn't count months of recorder history: that would be a throwaway recorder read at flow time. `detect_weather_entity` lives in `providers/forecasts/base.py`, since reading `hass.states` from `flow/review.py` breaks INV-3 (the grep test caught it), and the runtime uses it too. Affects D10 §6.
**Rejected:** querying the history span at review - a second recorder read for phrasing.

### D-0319 · `budget()` takes `controlled_planned_kwh`; projection and reserve switch at `BASELINE_CONFIDENCE`

D6 §2's formula needs the loads' planned energy over the window's remainder, which `budget()` can't compute: the tick sums `plan.kwh_between(now, now + t_rem)` over the plans and passes it as a defaulted seventh argument. `Plan.kwh_between` prorates envelopes; a `None` envelope counts nothing (INV-30). With a baseline at confidence ≥ 0.6 (`BASELINE_CONFIDENCE`, kept separate from D10's `OFFER_CONFIDENCE`), `projected = used + planned + baseline.energy_kwh(...)` and σ is the baseline's residual; otherwise the smoothed-power path is unchanged. The σ floor clamp holds on both paths (INV-62). Affects D6 §2, §3.
**Rejected:** passing `plans` into `budget()` - it runs before `AllocCtx` exists. Separate confidences for σ and projection - D6 §2 names one.

### D-0320 · `BudgetForecast` bridges D10 to D6's `Baseline` protocol

D6's `Baseline` wants a bare `confidence`, `energy_kwh(start, hours)` and `residual_sigma_w(t)`; D10's baseline takes a time argument for each. `Forecasts.for_budget(at)` binds one instant, like `for_planner()` (D-0217), and satisfies the protocol structurally, so neither domain imports the other. `Inputs.forecast_baseline` is the third field in D-0316's pattern, built from the same per-tick `Forecasts` the runtime already assembles. Affects D6 §2, D10 §3.
**Rejected:** handing `budget()` the raw `HourOfWeekBaseline` - ties D6's protocol to one concrete model.
