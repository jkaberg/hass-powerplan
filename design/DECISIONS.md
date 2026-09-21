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

### D-0321 · The tick memoises two recomputations that don't change between replans

Profiling the smoke tier (187 ticks/s against D9 §5.9's 500) found `_warnings` re-asking the same plan the same planned-kWh question every tick between replans, now cached per load on `plan.built_at`, and `_accounting_status` decoding the ledger's money through the generic reflective decoder every tick, now an `lru_cache`d constructor. About 8 % faster, results unchanged. The larger cost, `dataclasses.replace` across forty-odd state transitions, needs its own pass.
**Rejected:** hand-optimising `replace` now - forty call sites, each needing its own correctness check. Caching `LoadView.of()` - its inputs change every tick.

### D-0322 · The test suite runs on `pytest-xdist --dist=loadgroup`, with scenario fixtures grouped

xdist's default `--dist=load` recomputes a module-scoped scenario fixture on every worker that draws one of its tests (`test_phase0.py` took 25 minutes on four workers), and `loadscope` pins a whole file to one worker. `loadgroup` plus an `xdist_group` per module-scoped fixture fixes both: `test_phase0.py` runs in 67 s. The two thirty-day accounting runs (`controlled`, `twin`) were most of the suite's wall time on one core, so they run as two `spawn` processes via `ProcessPoolExecutor`: 29 minutes to 17. `forkserver` is blocked by `pytest-socket`, and `fork` from xdist's threaded worker can deadlock. The PR command gains `-n auto --dist=loadgroup`.
**Rejected:** splitting a scenario run by month - each tick depends on the last. xdist in `addopts` - a targeted three-test run would pay worker startup.

### D-0323 · `ZoneSpec` holds a zone's configuration; the engine builds a `Zone` from it every tick

A `Zone` needs this tick's prices and outdoor temperature, so unlike circuits and groups it can't be built once. `ZoneSpec` holds the subentry's half (members, sources, `never_substitute`, tuning) and `spec.build(prices, outdoor_c)` builds the tick's `Zone`, like `CircuitSpec.limit()`. `Engine.set_zones` and `_zone_constraints` mirror `set_constraints` and `_cycle_reservations`; `allocate()` already seeds any `Zone` from `AllocState.zone_choice`.
**Rejected:** rebuilding zones inside `allocate()` - the allocator decides, the engine composes. A mutable `update_prices` - every other per-tick constraint is a fresh value.

### D-0324 · The zone flow asks for members and which are never substituted; carrier and efficiency come from each load

`ZoneSubentryFlow` asks a name, the members (also the candidate sources), `never_substitute` and the five tuning numbers under Advanced, the same one-step-plus-review shape as groups and circuits. `runtime.build_zones` reads each member's `load.config.carrier` and, for a heat pump, its COP curve (`heat_pump.curve_of`); everything else is resistive, as D6 §6 says ("from D4"). No D4 type lets a household set a non-electric carrier yet, so zones built through the UI rank electric sources (a heat pump against resistive backup). The cross-carrier `Zone` logic is implemented and tested against hand-built sources. Affects D6 §6, D8 §5.3.
**Rejected:** a separate sources pick - doubles the form for a hydronic case D4 defers. A per-source efficiency field - meaningless without the carrier's price curve.

### D-0325 · `arbitrage` and `peak_shave` rank both ends of the horizon and simulate the state of charge once

`_candidates()` walks the price ranking from both ends together (Kth cheapest against Kth dearest) and stops at the first pair where `p_discharge × round_trip_eff − p_charge` doesn't clear the threshold; profit is non-increasing in K. Pairs aren't literal, since energy is fungible through storage: `_simulate()` walks the horizon once in time order, applying `peak_shave`'s forced reservation first and then the ranked sets, clamped to `[reserve_soc, max_soc]` and the inverter's limits. `peak_shave` reads negative `PlanContext.headroom` as the slots it must cover and forces the cheapest slots to charge for them. The battery defaults to `peak_shave` again (D-0308). Affects D5 §5.8.
**Rejected:** exhaustive pair search scored by clamped energy - O(n³) on a 190-slot horizon for no visible gain. Two separate simulations - the second could double-book slots the first claimed.

### D-0326 · The ladder's tick-level battery discharge and a battery shadow wait

D6 §5.3's "(design)" note, a stage ≥ 1 discharge before any comfort shed, is new allocator-walk logic whose placement relative to steps 3-8 the note doesn't settle, in code INV-1 governs tightly. `peak_shave` already protects the ceiling at the planning cadence. A battery has no registered shadow, so `shadow_for` answers `None`: cost shown, savings not (D11 §5.3). A battery's honest counterfactual is "no battery at all", closer to a whole-site question than a per-load one.
**Rejected:** building the tick-level discharge now - precedence code shouldn't be a rushed addition. A trivial idle shadow - not what a battery's savings mean.

### D-0327 · `_external_limits` bridges `Inputs.events` to `ExternalLimit`; the DSO provider and its UI are v1.x

`ExternalLimit` (D6 §5.8) and `Mode.DELEGATED` (D4 §5.2) were already built and tested. `Engine._external_limits(events, now)` turns each active `load_limit` event into one `ExternalLimit` from its payload, built fresh per tick like cycle reservations and zones. D1's `EventKind` is imported as `PricingEventKind` beside the engine's own. D8 already defers the DSO limit's configuration UI to v1.x. Note: the runtime doesn't yet hold an `EventStore`, so none of D1's event kinds reach a live tick; tests drive `_external_limits` with hand-built events. Wiring that belongs to the first work that needs a live event kind.
**Rejected:** a `providers/events/` module and a publish service now - DSO and aggregator shapes differ enough to build the wrong one.

### D-0328 · Tick and planning budgets are gated at 20 loads; the 500 ticks/s benchmark figure isn't met

`tests/perf/tick_budget.py` and `plan_budget.py` build a 20-load engine and gate P95 against D9 §5.1: tick 6.0 ms (budget 50), plan 14.6 ms (budget 500). They're marked `perf` and named explicitly in the command, since their filenames don't match pytest's default glob. D9 §9 8's ≥ 500 ticks/s for the full benchmark is a different property and isn't met: the smoke tier runs at 151 ticks/s. The profile is diffuse: `dataclasses.replace` across forty-odd call sites, `derive_rng` building a seeded RNG per draw (D9 §8's determinism depends on it), and tz-aware datetime arithmetic everywhere (a core convention). D9 §9 8 is annotated with the gap.
**Rejected:** lowering the assertion to the measured rate - the number is normative. A systemic rewrite now - cuts across the determinism and safety properties the design exists for.

### D-0329 · Four bugs a live install surfaced

(1) `build_site()` reads preset files and builds holiday tables, blocking I/O HA's loop detector flagged on every setup; `async_setup_entry` now runs it in the executor. (2) Load devices used the deprecated `via_device` tuple; the site device is registered up front in `Runtime.start()` so every load's `DeviceInfo` carries `via_device_id`. (3) One malformed `loads` entry in the store made `decode` raise and took the site down on every restart; each entry now decodes independently, and a dropped one degrades to `LoadState()` as a missing one already did. (4) `sensor.<load>_plan` read a `PlanSlot.w` that never existed; it reads `envelope_w`, keeping `None` distinct from `0.0` (INV-30). Each now has a test.
**Rejected:** fixing only the two crashes - the warnings name a breaking release or already cost latency.

### D-0330 · The site's own config entry gets `async_step_reconfigure`

The site had no gear icon: changing the fuse, meter roles or tariff target meant removing it. `async_step_reconfigure` restores every attribute `_assemble()` would set and re-enters at `name`, never the path menu. Seven schema functions gain a `values`/`default` parameter to pre-fill. The finish calls `async_update_entry` then aborts `reconfigure_successful`; the existing update listener reloads. `async_update_reload_and_abort` would reload twice. The stored fuse is numeric while the select needs `"63"`, so the restore converts back. Modifier and carrier options aren't pre-filled yet. Affects D8 §5.1.
**Rejected:** letting reconfigure change the onboarding path - adds steps a site never walked; a different, larger flow.

### D-0331 · The test pipeline stops repeating work

No `core/` change, no test removed, no assertion changed (D9 §5.13). (1) Benchmark spans run in `spawn`ed processes (`POWERPLAN_BENCH_PROCESSES=1` keeps them in-process for a profiler): smoke 662 s to 310 s, same result. (2) The determinism test runs its two runs concurrently and still compares digests. (3) `tests/conftest.py` schedules the groups and tests `tests/durations.json` names longest-first. (4) `tests/scenarios/cache.py` caches every scenario result under a SHA-256 of `core/`, the test support code, the asking module, the lockfile, `pyproject.toml` and the Python version; `POWERPLAN_SIM_CACHE=off` bypasses it, and the determinism test's second run is never cached. (5) CI splits into `lint`, `ha`, `core`, `scenarios`, `bench` and `coverage`, with a nightly uncached run. PR command: 24 min cold to 20, 8.5 min warm.
**Rejected:** comparing one run against the committed baseline for determinism - every in-tolerance change would force a re-baseline. Caching inside the benchmark tool - it measures time.

### D-0332 · The HACS limitation sits in the README until `docs/limitations.md` exists; the brand generator stays in `tools/brand/`

HACS's blank store icon (hacs/integration#5171) goes in the root README's "Known limitations" until the docs tree exists, then moves. `tools/brand/make_brand.py` reads Inter from `tools/brand/fonts/` (from `npm pack @fontsource/inter`) and writes to `tools/brand/out/`, both git-ignored; copying the six PNGs into `brand/` is a manual step so a regeneration never replaces a shipped image unseen. `quality_scale.yaml` marks `brands: done`.
**Rejected:** a one-line `docs/limitations.md` stub - ships before the docs tree has headings and link tests.

### D-0333 · The incremental tick: byte-identical and 11 % faster

Every change is pure and keyed on identity or stamps: the evaluator keeps `level()`/`state()` until history moves; `LoadCtx` and `KindCtx` are built once per load per tick; a step that would write back what a state already holds returns it as is; `model.epoch()` caches the last `timestamp()`; `free_blocks` filters its previous answer; `RollingStd` keeps its intervals; `LoadView.of` copies only params. D9 §9 13's gate: `POWERPLAN_DIGESTS` makes the cache record every digest, and `tools/digests.py --against <rev>` fails on any moved result; CI runs it on PRs labelled `speed`. All 22 digests identical. One simulated day: 196 → 218 ticks/s, planning −25 %. `gc.freeze()` adds 1 %. About 27 % of a tick is dataclass `__init__`, and most of the rest is `replace` on states that genuinely change every tick, so 500 ticks/s isn't reachable this way (see D-0334). Affects D7 §5.1, D9 §5.13, §9 13.
**Rejected:** a committed `digests.json` checked on every PR - every in-tolerance change would force a re-record. A `TickClock` threaded through every signature - the epoch cache gets most of its gain.

### D-0334 · mypyc isn't adopted: byte-identical results, but 1.00-1.08× faster

The whole `core/` (and then the simulators and runner) was compiled with mypyc on CPython 3.14 in scratch copies and compared side by side with pure Python. It needed seven patches to mypyc itself to be faithful: annotations as source text (mypyc builds them from type objects, which segfaults on `Snapshot`'s `TYPE_CHECKING` forward references and silently breaks `state_codec`'s `get_type_hints`), non-native exception subclasses, `Protocol` kept among bases, CPython's compensated `sum()`, a `Final` float tuple read, and pickling of native frozen dataclasses. Measured: 1.00-1.04× on three scenarios, 1.08× at best, one benchmark day 1.04×. `perf` shows 27 % of samples in the stdlib-generated dataclass `__init__`, 42 % in interpreted code called from compiled code, ~17 % allocation and GC; mypyc speeds up a small share of a tick built from frozen dataclasses, tz-aware datetimes and `Decimal`s. A mypyc compiling dataclass methods too would cap near 1.4×. Affects D9 §5.13.
**Rejected:** adopting it for 4-8 % - seven compiler patches pinned to one release, a 5-8 minute build and a second artifact to keep equal, for a gain inside the machine's noise. Cython - meets the same dataclasses and datetimes.

### D-0340 · Assembled words live in four `selector` vocabularies; `flow/text.py` formats numbers per language

Every word Python assembles into a label is a translation in `selector.text`, `selector.tariff_text`, `selector.review` or `selector.load_text`, since hassfest's schema has no free-text section. `flow/text.py::Text` reads them in `hass.config.language` (D8 §5.15 H7). `nb` groups thousands with a no-break space and uses a decimal comma; other languages get English numbers, as HA gives them English words. Money gets a symbol per shipped currency and its minor unit (øre, cent, p…); an unlisted currency shows its code. A price keeps its written scale: Tensio's 0.3604 is "36,04 øre/kWh". Affects D8 §5.11, §5.15.
**Rejected:** a Python `{"nb", "en"}` table - words outside the translation files are how the English leaked in. `babel` - a new requirement for two languages.

### D-0341 · `TariffSummary` covers every tariff root; the country's generic tariff shows only as "not listed"

`loader.summarize(spec, at)` replaces `render_plain_language` with data: the metric's shape, bands, Linear's price and free kW, eligible filters and weights, contracted limits and whether they trip, the grid energy charge, source, verified date and whether anything is assumed. `flow/text.py::tariff_table` renders it, so every shipped market can be checked against a bill before saving (INV-67). The generic preset appears only as "Finner ikke mitt nettselskap". Affects D2 §3, §6.
**Rejected:** summarising step tables only and saying "see your bill" otherwise.

### D-0342 · `step_<i>` is read in both spellings; nothing is migrated

`runtime.step_index()` reads `step_<i>` and the older `step:<i>`, used for the stored target, the restored select and reconfigure; an entry keeps the old spelling until the site is saved again. The select carries the chosen step's bounds, fee and currency as attributes, since a state translation takes no placeholders (H1). Affects D8 §5.15.
**Rejected:** a config-entry migration - the restored recorder state needs the reader anyway.

### D-0343 · The advice state is the first warning, else the first info; the charging session is read from the plug edge

`sensor.advice` is an enum over `all_good` and D2's advice keys; its state is the first `warn` item in the evaluator's order, else the first `info`, else `all_good`, never unknown. `sensor.<load>_session` is `no_car · waiting · charging · done`: `done` on the latch, `no_car` unless the engine's last plug edge says connected (a dropped link keeps the last state), `charging` when the car draws. The engine's reason stays as an attribute. Affects D8 §5.15.
**Rejected:** a `connected` field on core's `Demand` - a core field for a label.

### D-0344 · Carriers get a step id each; `unsafe_switch` becomes reachable

The carrier step's title was filled with the carrier's key; `carrier_<gas|district_heat|oil|pellets>` join `modifier_<key>`, both generated from their registries. In the load flow `derive()` now runs inside the `try` that catches `AnswerError`, so the water heater's `unsafe_switch` (INV-64) names the field instead of failing the step. Affects D8 §5.15.
**Rejected:** one carrier step titled with the translated name - a lower-case name looks like a key.

### D-0345 · Codes stay codes: Nord Pool areas, price formats, time zones

A registry `SELECT` gets a translation key only when every option can be one (hassfest's `[a-z0-9_-]`), so `NO3` shows as itself. Price formats show the integrations' own brand names in both languages. The time-zone list drops tzdata's lower-case file entries (`localtime`, `posixrules`). Affects D8 §5.15.
**Rejected:** naming the Nord Pool regions now - the step is rebuilt later, and a key can't be `NO3`.

### D-0346 · What the text tests accept as "the same in both languages" and as "English"

D8 §9 18a's allow-list: words identical in both languages by construction (PowerPlan, Nord Pool, OK, LFP, kW…), whole numbers with a unit, and brand-named price formats. 18c's "no English word in nb" is a list of about ninety English words that aren't Norwegian, scanned after removing the household's own names, grid companies, time zones, URLs and inline code. Numbers: no point decimal in `nb`, no comma decimal in `en`.
**Rejected:** a dictionary-based language detector - a dependency, and a device named in English would be a false alarm.

### D-0350 · The period seed: live windows first at setup, the recorder's on the rebuild button; a seeded window is its own counterfactual

`_seed_peak_history` runs after the first tick of every setup and from `button.<site>_rebuild_peak_history`: the register's hourly statistics for the open period (D-0352) become windows at the tariff's length and fold in under the lock. Setup adds only windows the history lacks, so a live-closed window is never overwritten; the button replaces everything it has. Overrides survive both (D2 §9 16). Every window a seed adds also goes to the counterfactual book unchanged: nothing was steered then. Without that, a site created on the 22nd bills the month's real peaks against a counterfactual that starts on the 22nd and reports negative capacity savings. Affects D2 §5.12, D11 §5.4, D8 §5.5.
**Rejected:** seeding once per period - the recorder can't fill a gap the live meter left either. Always replacing - a wrong row placement would overwrite good live windows.

### D-0351 · A fresh baseline is one that has folded no window; `seed()` records its reconstruction

The startup guard is `baseline.state.last_update is None`; `not baseline.state.bins` was never true, since the baseline materialises 168 empty bins, so a fresh site never seeded. `HourOfWeekBaseline.seed(history)` folds every window through `update()` and records `history.reconstruction`. The runtime passes every configured load as a `LoadSource` with no entity, so the mark says `none` (D-0214) where no loads would say `full`. Affects D10 §3, §5.2.
**Rejected:** returning empty bins until the first update - changes the persisted shape of every restored baseline.

### D-0352 · Hourly statistics only, each row placed by the register's own reporting cadence

`async_register_history` reads the register's hourly `sum` rows and places each at `S + 1 h − min(c, 1 h)`, where `c` is the median gap between the last 50 state rows whose value changed; a value republished after `unavailable` isn't a report. With fewer than five gaps, rows sit at `S` (D3 §5.3). A statistics row is filed under its hour's start but holds the register at the last state inside it: for an AMS meter reporting once at HH:00:10 that's S, for a fast register S + 1 h. Counting republished values as reports put the reference month at 8.70 kW instead of 8.98. Affects D3 §3, §5.11; D2 §5.12.
**Rejected:** scoring both placements against power statistics - needs a power role. The 5-minute table - two tables and a seam for no reported case.

### D-0360 · "On record": what release and restore undo, and when the lifecycle writes

INV-26 and INV-27 (PLAN §7 dec. 30) need a record and a value to put back. (1) `LoadState.prior` records, per role, what the device held just before powerplan's first write since it last let go; a later write never replaces it. (2) `release()` and `restore()` are one undo: with a record they write it back (urgent, past dwell and interval) and clear it once held; without one they do nothing. `restore()` adds the restore dwell (INV-29). A shed left in the store with no record is handed back the old way. (3) `release_all`/`restore_all` (startup, unload, stop, a removed subentry, the `release` service) act only on loads under control: site on, not in safe mode, load `auto` or `force`. A site that's off writes nothing. (4) Since release undoes every write of ours, a plan's coast is undone on the edge to off too. A start used to write every load's hand-back value in observe, whatever the device held and whoever set it. Affects HLD INV-26, 27, 29, 48; D4 §4.1, §5.1, §5.2, §5.4, §5.5, §7; D7 §5.5, §8.
**Rejected:** the kinds' hand-back values gated on the last sent value - releasing a paced charger would still write 32 A over the household's 10 A. A stack of earlier values per role - an earlier write of ours never needs restoring.

### D-0361 · The site switch's position is recorded every tick and read back before startup writes

The tick records `edges["site_active"]` from the switch, and `_mode_edges` compares against it; `Runtime.start()` sets `active` from the record before step 4 of D7 §5.5. Nothing wrote `site_active`, so the site→off release (PLAN §7 dec. 20) never fired; and the switch entity restores only after platforms load, so startup read the entry's "start in observe" instead of what the household last set. Affects D7 §5.5.
**Rejected:** reading the switch's restore state in `start()` - needs the entity id before the platform exists.

### D-0362 · The tick reads every bound entity by id; a stale role carries its `since`

`LiveDevice.reads` uses `DeviceView.from_states(hass, bound.entity_ids)`. The reference tank's power and energy sensors live on no device (a template and an `integration` sensor), so a device-scoped view read them as missing from the first tick; the flow binds off-device entities on purpose. `LoadState.stale_since` latches when a bound role stops answering, and `Health.transient_since` is the earlier of it and the gate's clock, so a stale load can escalate. Affects D4 §4.1, §5.9, §7.
**Rejected:** a device view patched with the off-device bindings - two reads of one thing.

### D-0363 · Observe decides against the device and reports each would-be value once

Row 1 runs row 3 first in observe: a device already at the value is `same`. An observe decision stores its value in `GateState.observed` (persisted, cleared by a write), and the engine passes it to the executor only when it changed, so the log is one line per change, across restarts. `ApplyResult.current` puts the old value on the line: `old → new`. Observe was logging one line per load per tick (11 366 in three hours), including "would write 21.0" to a heat pump already at 21.0. Affects D4 §4.1, §5.10, §7.
**Rejected:** de-duplicating in the executor - forgets at every restart, and the executor has no logic of its own.

### D-0364 · A load subentry update swaps the load in place; entities are added once

`_update_load` rebuilds the `Load` and its device and swaps them into `SiteBuild`, keeping the `LoadState`, knobs, meter rows, plan and entities; accounting refreshes the load's params and shadow. `_add_entities_for` adds only unique ids not already added, and `LoadEntity.load` reads the current `Load` by id. Remove-then-add logged an ERROR per re-registered entity and lost the load's mode, knobs and state. Affects D7 §2.
**Rejected:** removing entities first - ids, dashboards and history would churn on every reconfigure.

### D-0365 · A one-time listener forgets itself when it fires; the store's `schema` stamp isn't a load

(1) `_track_once` wraps `homeassistant_started` and `homeassistant_stop` and removes its own unsubscribe before running, since HA drops a one-time listener on firing and unsubscribing again logs an ERROR. (2) `from_sections` decodes `loads` without its `schema` key. (3) `stop()` stays once-only, but an unload after `homeassistant_stop` still unloads the platforms, or a later setup added every entity twice. Affects D7 §5.5.
**Rejected:** catching the error around the unsubscribe - the log line is HA's own.

### D-0366 · The read-back reads a load's role through its bindings

`StateReader` is `(load_id, role) → value`; `Runtime._read_state` answers with `device.reads(now).current_of(role)`, the same reading the next decision makes (attribute, scale, quirks). Amends D-0141. The entity-state reader compared a climate's `heat` with 22.0, so every setpoint write would have counted a deviation; the tests' own reader was already correct, which is why nothing caught it. Affects D4 §5.10.
**Rejected:** resolving the binding in the runtime from an entity id - one entity can carry two roles.

### D-0370 · A `DeviceCall` carries `device_id` beside its `entity_id`

`DeviceCall.device_id` and a `target` property (`{"device_id": …}` when set, else `{"entity_id": …}`); `entity_id` stays as the read-back's witness (INV-22). `BoundDevice.device_id` comes from the subentry in `runtime.device_from_subentry`. `easee_cloud` writes `easee.set_charger_dynamic_limit` with whole amps and `time_to_live: 0`, and is unaddressable without a device id. Affects D4 §5.10.
**Rejected:** `bind(bindings, device_id=…)` - every profile takes an argument one uses. An optional `entity_id` - a call with no witness.

### D-0371 · Fixtures written from an integration's source live in `captured/`, in the capture format, and say so

`zaptec_charger.json` and `easee_cloud_charger.json` are hand-written from the integrations' source (zaptec v0.8.7, easee_hass v0.9.74) in `tools/capture_fixture.py`'s format, with a `source` key and `written_at`, no `captured_at`, a `device_id` and, for Zaptec, the installation as a nested `parent`. The production loader has to read them (D9 §9 7), and the keys tell them apart from real dumps. The Easee dump includes the three disabled-by-default sensors the profile asks to enable. Affects D4 §5.9.
**Rejected:** `tests/fixtures/formats/` - that's for format-adapter payloads.

### D-0372 · Both cloud chargers stop and start on the limit alone, via a `limit_pauses` capability

Neither profile binds `ENABLE`. Zaptec's *Charging* switch is unavailable whenever its command is invalid, and its off leaves `Connected_Finished`, which would park every pause as a finished session. Both sources say the limit is the switch (0 A holds, 6 A or more charges). Both matches declare `limit_pauses`; `ev.derive` materialises it, and the type builds `MODULATE` with no enable role, so a stop is one write at 0 A and "enabled" means the limit is at or above `min_a`. Affects D4 §5.9.
**Rejected:** binding the switch and treating an available `connected_finished` as a pause - rests on an entity also unavailable when a poll fails. Inferring "no enable" from a missing read - a fixture could switch it off.

### D-0373 · A charger's vocabulary reaches the core as the core's words; an unmapped status is a lost link

`SessionState.status_word` maps states to `offline`, `disconnected`, `car_connected`, `charging` and `completed`, words `types/ev.py` already uses. `ChargerDevice(BoundDevice)` rewrites the status read to that word and applies the link-loss rule. `StatusVocabulary.state` returns `LINK_DOWN` for an unknown word (INV-15); `SessionState.UNKNOWN` goes. A second charger is a second table, not a conditional (D4 §5.11). Affects D4 §5.9.
**Rejected:** adding each charger's words to the core's sets - every charger would edit `core/`, and words would collide.

### D-0374 · The Easee cloud re-arm is the read-back

After a plug-in or reboot the charger resets its dynamic limit, the sensor reports it, row 3 sees it differs from the grant, and the held limit is written again. The integration drops a call whose current equals its own record, which is the sensor's source, so a blind re-send can't reach the charger when the sensor is stale anyway. The reset value isn't documented; the test's fake uses the maximum, marked assumed. Affects D4 §5.9, §5.10.
**Rejected:** marking the limit unknown for a tick after plug-in - edge memory in a stateless provider, ending in a call the integration drops.

### D-0375 · The runtime raises each load's gate to its profile's row

`Quirks.raised(cfg)` lifts tolerance, interval and settle to the profile's floors, and `runtime.load_from_subentry` applies it to the load's gate. The profile's row had only reached tests; no profile was stricter than its kind until Zaptec's 900 s. Existing loads get identical gates. Affects D4 §5.10.
**Rejected:** three new `LoadConfig` fields for numbers that belong to the profile.

### D-0376 · `DeviceView` knows its parent; the tick reads bound entities that live off the device

`DeviceView.parent` is the `via_device`'s view. `get()` falls back to it; `find()` doesn't, so a parent's entities never answer another profile's heuristics. `LiveDevice.reads` appends a `from_states` view of bound entities the device doesn't own. Zaptec's *Available current* is on the installation device, and heat-pump sensors from other devices need the same. Affects D4 §5.9.
**Rejected:** merging the parent's entities in - a Z-Wave controller's entities would join every thermostat's view.

### D-0377 · `zaptec_slow_trim`: the cloud takes every write; a change inside 15 minutes may drop the session

`ZaptecChargerSim` wraps `EvSim` behind the installation's *Available current*: every write lands, the read-back is prompt, 0 A pauses, 6 A or more resumes. A change less than 900 s after the last is counted and interrupts charging with a seeded probability of 0.25 (assumed), per Zaptec's note that frequent changes may interrupt a session. The scenario asserts `over_target == 0`, no raises inside 15 minutes, and at least one such trim.
**Rejected:** a sim that ignores a second change inside the window - Zaptec doesn't refuse, and urgent sheds would be modelled as lost.

### D-0380 · The tank shadow's dial is `anchor_c`; η paid on the way in

`TankShadow` holds `anchor_c` (`min(ready_temp_c, max_c)`, D-0203) with a 2 K hysteresis, heats at nameplate over `C_tank` and loses standby and draw-off over `C_water = C_tank · η`, in one-minute steps (D-0177). `draw_off_of(params)` builds the draw-off profile in one place for the sensorless model and the shadow; the adapter fills `ShadowCtx.draw_off_kwh` per slot. One `C_tank` for both terms would understate every kWh by 2 %. An idle day: 1.469 kWh against the simulator's 1.443. Affects D11 §5.3.
**Rejected:** `ready_temp_c` directly - `anchor_c` is already bounded and is the sensorless model's anchor. A per-tank hysteresis question - changes when reheats happen, not how much.

### D-0381 · A tank shadow takes a measured level once and is never pulled onto a steered one

`TankShadow.reanchor` adopts `level_now` only while it has none. A plain tank's thermostat keeps the shadow in its band, so there's no drift to correct, and the level powerplan steers the real tank to (45 °C most of the day) is the difference being measured; pulled onto it, the shadow would book a 10 kWh reheat no plain tank needed. `_first_level` falls back to what `init` would give (`None` for a tank). Note: `_blind_for_a_day` measures time since the last anchor, not time without a level, so thermal shadows are re-anchored daily in `auto`; fixing it moves the thermal loads' money columns and is left for its own change. Affects D11 §5.3.
**Rejected:** re-anchoring as §5.3 says and letting the thermostat recover - the recovery is the spurious reheat.

### D-0382 · `legionella_active` is the tank's in-progress latch; the shadow keeps its own trajectory through it

`SlotLoad.legionella_active` comes from the tank's `legionella_in_progress_since` at the slot's close and fills `ShadowCtx.legionella_active`. While set, the shadow's kWh for the slot is the real slot's, so cost and counterfactual match; its level carries on under its own thermostat, leaving the cycle where a plain tank would be. The latch opens with the 24 h lead window, so up to a day in seven shows zero tank savings, the conservative direction. Affects D11 §4, §5.3.
**Rejected:** passing through only the slots the element drives - needs a new load-state field for a savings figure, the wrong direction for INV-68.

### D-0383 · The schedule shadow divides `hours_per_day` by 24 every day; the idle shadow carries an unused SoC

`ScheduleShadow` draws `nameplate · hours_per_day / 24 · dt`, reading the same subentry key `store_kind_of` uses; a DST day runs 23/24 or 25/24 of the quota. `IdleShadow` draws nothing and carries the measured SoC so its state has the common shape. Affects D11 §5.3.
**Rejected:** a per-day length - needs the site zone in the shadow for ±4 % on two days a year.

### D-0384 · `tests/sim/tank.py` served every draw twice

`DrawProfile.litres_at_55(t0, t1)` read the draws of the day before, `t0`'s day and `t1`'s day, and within one day that's the same day twice: 270 L a day for three persons instead of 135. It now reads each day once, with a tick-by-tick test. Found because `observe_calibration` with the new tank shadow read a calibration error of 0.455. Every scenario with a tank moves, and the smoke baseline is updated once with a changelog line.
**Rejected:** dropping the tank from `observe_calibration` - narrows a test to make a change pass. Doubling D4's profile - the simulator was the one breaking its own spec.

### D-0390 · A modifier that replaces the energy price runs first

`chain_from` puts every modifier whose component is `spot` (`fixed_price`, `export_price`) ahead of the rest; the others keep their order. A site that ticked VAT before Norgespris composed VAT on spot and then replaced spot, pricing Norgespris at 0.40 + 25 % × spot instead of a flat 0.50 (it's VAT-inclusive by law). Stored order is tick order, not tax law, and sorting fixes every entry without a migration.
**Rejected:** migrating stored entries - `chain_from` makes it moot.

### D-0391 · A form list's nested selectors are plain mappings, for the 2026.3 floor

`_period_fields`, `_tier_fields` and `_rate_fields` write each `ObjectSelector` field as a dict. HA 2026.3 validates nested fields with a function that takes only mappings; the fix accepting `Selector` instances (core PR #170453) came later. Tests importing current HA don't show it.
**Rejected:** instances until the floor moves - the manifest promises the floor.

### D-0392 · A meter role's unit and state class are checked on submit

`_ROLE_FILTERS` carries each role's device class, units and, for a register, cumulative state classes, and `meter_data` raises `StepError` naming the field on a mismatch. The picker filters on device class only: the floor's entity selector has no unit filter, and no version filters state class.
**Rejected:** a unit filter on newer lines only - a filter that changes shape between versions.

### D-0393 · A cross-field bound is declared per questionnaire and checked after coercion

`Questionnaire.bounds` names which answer must sit between which others (floor heating's comfort between min and max; the tank's temperatures against comfort min and max). `_check_bounds` raises `outside_bounds` on the value's field, only when the household answered both sides. Declarative, in the core, with no HA import (INV-49).
**Rejected:** checking in `flow/load.py` - validation belongs with the schema.

### D-0394 · `generic_switch` never claims a config or diagnostic entity, and a light needs a power sensor

`EntityView.entity_category` carries the registry's category, and `generic_switch` drops `config` and `diagnostic` entities before matching; an on/off `light` counts only if its device has a power sensor. A network access point's status LED was being offered as a load. Affects D4 §9 16.
**Rejected:** a manufacturer denylist - the registry already states each entity's purpose.

### D-0395 · A derived default shows what `derive()` would make of it; leaving it isn't an override

`suggestions()` runs `derive()` on the answers so far and shows a derived-default question's value as the suggestion. `answers_from_form` drops a field equal to its suggestion before validating, so the derivation keeps following the other answers (a floor's comfort follows its area). The same mechanism covers Nord Pool's publication clock and the per-phase limit. Affects D8 §5.15.
**Rejected:** storing whatever is submitted and re-deriving only on reset - freezes values nobody chose.

### D-0400 · The entity pass covers the site and home entities; per-appliance entities wait for device attachment

The device-attachment spec moves per-load entities onto the appliance's own device with a smaller set, as separate work. This pass: site entities and translations, window names by `window_min`, kW display precision, `stage` numeric and diagnostic, the savings name, the peak warning's next window, `sensor.<load>_measured`'s default-enabled flag (D-0403), the home device's info, plus recorder and decoding fixes that touch any entity. Affects D8 §5.15.
**Rejected:** building the full per-load map now - entities built in the old shape would have to be undone, and INV-50 guards removals.

### D-0401 · `_accounting_status` decodes every field the hook encodes

`AccountingStatus` had fifteen fields and the hook encoded them all, but the engine decoded four, so every money sensor's components and history read `None` and `last_reset` had no month start. Every key is decoded now; the hook also sends `month_start` and `since`. Affects D8 §5.5, §9 14.
**Rejected:** backfilling old status blobs - the next slot close republishes the section.

### D-0402 · The recorder-volume budget follows the state that changes; two exemptions, each with a reason

`peak_warning`'s `next_window_start` re-sorted several times a tick with two windows over target, so it joins the volatile attributes. `sensor.<load>_session` exceeds budget because its state toggles near the simulated charger's 6 A cliff, a real state change; damping it is per-load state logic for the device-attachment work. `event.<site>_events` is one row per domain event by contract. Both exemptions are named in the test with their reasons (D8 §9 19).
**Rejected:** fixing the session hysteresis here - it's appliance-entity state logic.

### D-0403 · `measured`'s power-role rule is the registry default, never existence

`LoadSensorRow.enabled` becomes `Callable[[Load, Runtime], bool]`, like `meter_health`, checked against a bound power role. HA reads the default only at first registration, so an existing `sensor.<load>_measured` keeps its state; only new loads without a power role start disabled. Gating existence would silently remove entities (INV-50).
**Rejected:** reading `measured_w` at construction - no snapshot exists yet, and a transiently unavailable role would read as absent.

### D-0410 · An entity id without the device-name prefix comes from `suggested_object_id`

A preset `self.entity_id` becomes `suggested_object_id` on registration, which the registry ranks after a user rename and before the translated name (which is device-prefixed when `has_entity_name=True`). HA's own collision handling adds `_2` as needed. Affects D8 §5.16.
**Rejected:** another route to `suggested_object_id` - `self.entity_id` is the only hook before registration.

### D-0411 · Priority becomes three fixed numbers, 15 / 30 / 45, split at the midpoints

The `priority` select (Lav · Normal · Høy) maps to 15, 30, 45; a stored number rounds at 22.5 and 37.5. Every type's default lands where D4 §6 would lead a household to expect: EV, pool pump, sauna, cycles → Lav; ventilation, floors, radiators, battery → Normal; water heater, heat pump → Høy. Affects D4 §5, §6, §9.
**Rejected:** five levels, or the free number with a select over it - the walk's `(priority, load_id)` tie-break already orders loads on one level.

### D-0412 · `always` stays a strategy; it leaves the household's select only where the type has a real alternative

The spec folds "Alltid på" into `control = Ikke styr` (`mode = off`), which is right where the type can be price-steered. For `generic_switch`'s on-call subtypes (sauna, hot tub, other), `always` is the right model for something started by hand: no plan, capacity still governs (HLD §3). Folding them into `off` would silently drop fuse protection from 6 kW loads. So `always` stays registered, the strategy select is hidden for those subtypes, and they keep `control = auto`. Migration moves `always` to `Ikke styr` only when the type has another strategy. Affects D4 §5.2, §6.7, §9; D5 §3, §6; D8 §5.16.
**Rejected:** applying the spec literally everywhere - a silent regression on the loads where it matters most.

### D-0413 · `control` keeps all five `Mode` values; three lead the list

The merged `control` select keeps `auto`, `force`, `observe`, `delegated` and `off` (labelled Ikke styr), with `observe` as Prøvemodus and `delegated` as Styres av noe annet. `observe` still computes, logs would-be writes and calibrates; `delegated` still reserves nameplate for an outside controller. Affects D8 §5.16.
**Rejected:** trimming to three - `select.<load>_mode` is the only per-load way into `observe` and `delegated`.

### D-0414 · Comfort-override detection reuses the gate's record of its own writes via HA's `Context`

`writegate.py` sends each command under an explicit `Context`, and `GateState.last_context_id` records it. A setpoint whose state carries that id is our write settling; one that doesn't is the household's, once the record is reconciled after a restart. Before that it's held, neither adopted nor ours. This is what INV-27's "user-originated only" needs, using H.2's existing record. Affects D4 §4.1, §5.4, §5.10; D8 §5.16.
**Rejected:** comparing state against what we commanded - can't tell our eco setpoint from a hand-turned dial after a restart, which is the setpoint-walk incident.

### D-0415 · A hardware device's removal detaches our entities; it never deletes them

HA deletes an entity on device removal only when it belongs to the removing integration's own entry; ours are kept with `device_id = None`. So the runtime listens for device removal among its bound devices, creates the fallback device, re-points the load's entities to it and raises a repair. No entity id, history or statistic changes: the recorder tables carry no `device_id`. Affects D7 §5.3, D8 §5.16.
**Rejected:** recreating the entities on the fallback device - removal and re-adding is what loses history.

### D-0416 · The fallback device is the old `load_device_info`, now the exception

The fallback device (the load's own identifiers, `via_device_id` = the site) is kept as is, used when a load has no hardware device and while a removed device awaits re-attachment. Its model becomes the translated type name. Affects D8 §5.16.
**Rejected:** a new identity or icon for it - nothing asks for one.

### D-0417 · The migration's writes are ordinary gate writes, after release and restore

The one-time comfort write in the device-attachment migration runs in setup after `release_all` and `restore_all` and before the first tick, as a normal `SETPOINT` decision through the gate. While the site is off it's skipped, and the device's own setpoint becomes the target on the first `auto` tick; a migration write is exactly what puts a fresh `last_context_id` on record. Affects D7 §5.5, D8 §5.16.
**Rejected:** writing at migration regardless of site state - a site left off must stay silent through an upgrade.

### D-0418 · The brand icon as entity picture replaces the "PowerPlan-" prefix

An appliance entity on a hardware device gets `entity_picture = /powerplan_static/icon.png`, served from a static path registered in `async_setup`; its name has no prefix, so its entity id is HA's default and D-0410's helper isn't needed. The picture marks the row wherever it's drawn without cluttering the name. HA's own brand endpoint needs auth or a rotating token an `<img>` can't carry. Entities on PowerPlan's own devices have no picture. Affects D8 §5.16.
**Rejected:** a custom icon set via a frontend module - loads before every frontend, and at 24 px the brand is bars and a bolt, indistinguishable from MDI. Keeping the prefix - clutter in every row.

### D-0419 · `device_missing` is fixed in the gear flow, so the repair isn't fixable

`device_missing_<load>` is an ERROR repair pointing to the appliance's "Endre oppsett". Reconfigure opens on a device step when the bound device is gone, re-matches roles with the type kept, then asks the usual questions; `Runtime.settle_devices()` moves the entities back, removes the empty fallback device and clears the repair. A repair fix flow can't open a subentry's reconfigure, and one that only confirms would read as fixed while the appliance stays uncontrolled. Affects D8 §5.16.
**Rejected:** a repair flow that asks for the device itself - a second copy of the gear flow's steps.

### D-0420 · The override test: context, then our value, then settling, then the first sighting

`gate.setpoint_origin` says whose change a setpoint is. Ours if it carries our last write's context or equals the value we last wrote, within tolerance. Held while our write settles, or before the first sighting after a start (which is only recorded). Otherwise the household's: it becomes the subentry's `comfort_c`, joins `manual_overrides` and applies next tick. Context alone fails for a sleepy Z-Wave thermostat reporting after HA's five-second context window. A persisted `reconciled` flag would never turn true for a load we haven't written since start. Affects D4 §4.1, §9 28; D8 §9 28.
**Rejected:** context only - can't tell a late report of our write from a hand.

### D-0421 · No migration for device attachment: nothing is released yet

D8 §5.16's migration steps, merge repair and golden-registry test aren't built: no released install exists, and a new install gets the new entity set directly. INV-50's merge clause has no case yet. Affects D8 §5.16, §9 25.
**Rejected:** building it anyway - tested against a store shape no user has.

### D-0422 · Priority: the select shows the nearest level; derived numbers stay

`select.<appliance>_priority` shows the level nearest the stored number and writes that level's number when chosen. `derive()` keeps each type's own numbers (car 10, tank 40, heat pump 50), so allocation doesn't change: snapping would make tank and heat pump equal and move every digest. Affects D8 §5.16, D4 §6.
**Rejected:** snapping every derived priority now - an allocation change dressed as a screen change.

### D-0423 · `plan_status` adds `observing` and `idle`; its `reason` is the engine's

Beside D8 §5.16's states, `observing` (effective mode `observe`, including a site switched off) and `idle` (the load wants nothing: tank full, room warm). Precedence: device, override, mode, the car's session, shed, idle, then running or waiting. `reason` is the engine's `action_reason`, since the frontend shows attributes untranslated. Every attribute of the merged rows keeps its name, so no automation loses a key. Affects D8 §5.16.
**Rejected:** mapping observe to `not_controlled` and a satisfied load to `waiting` - both untrue on the entity a household reads first.

### D-0424 · Where the entity set differs from D8 §5.16's table

(1) `temp_min`/`temp_max` exist for every thermal type with a target, even with a bound `floor_min_limit`, because the profile writes the configured floor to that role (INV-64). (2) No battery `charge_min`: the kind reads its reserve at build time. (3) No strategy select for `generic_switch`: one choice remains. (4) `energy` is diagnostic and enabled. (5) `measured_power` only where the power role isn't on the appliance's own device. (6) `appliance_cycle` keeps `run_now`. (7) No appliance binary sensors. Affects D8 §5.16.
**Rejected:** for (1), reading the device's floor role back as the source - changes INV-64's provisioning.

### D-0425 · The gear flow asks level 3 only; `ENTITY_SETTINGS` says which is which

`const.ENTITY_SETTINGS` lists, per type, what an appliance entity owns (comfort, floor and ceiling, follow presence, charge targets, hours per day, ready by, run-now limit). The gear flow doesn't ask those, keeps their stored answers, leaves strategy and priority out of the review and the re-derive diff, and reads them back under a link to the device page. The add flow still asks them as starting values. The map lives in `const.py` to avoid an import cycle, and a test checks every knob's parameter is in it. Affects D8 §5.16, §9 29-31; INV-66.
**Rejected:** deriving the map from `load_entities` - circular import.

### D-0430 · What the grid company's preset prices isn't offered as an add-on

The add-on step comes after the grid company and omits add-ons the preset already prices (Tensio's day/night `tou_schedule`), naming them instead: "Already included from your grid company: …". On reconfigure a hand-ticked copy is dropped in favour of the preset's numbers. `_preset_modifiers` had let a ticked add-on silently replace verified numbers. `SelectSelector` can't disable one option, so leaving it out and naming it is the closest. Affects D1 §6, D8 §5.15.
**Rejected:** offering them ticked with a manual override - that's the custom preset's job.

### D-0431 · The contract question: Norgespris is an answer; spot's markup stays an add-on

"Hvilken strømavtale har du?" offers Nord Pool spot, Norgespris (Norway), spot from a sensor, and a fixed price. Norgespris stores the Nord Pool source with `fixed_price` pre-ticked, and reads back from that pair. A per-kWh markup is the `spot_scale`/`levy` add-on; a monthly fee changes no decision and isn't asked. `entry.data` keeps its shape (INV-66). Affects D1 §6.
**Rejected:** a `norgespris` source - a second path to keep equal to the add-on.

### D-0432 · A detected Nord Pool area isn't asked on a first setup

With one Nord Pool entry whose entities name the area, the first setup skips the step (currency from HA, area detected); reconfigure shows it for correction. Areas are labelled by region ("NO3 - Midt-Norge") from `selector.nordpool_area`, keyed lower-case since `NO3` can't be a key. D8 §5.15 rule 3: detect first. Affects D1 §6.
**Rejected:** always showing it pre-filled - a screen with nothing to answer.

### D-0433 · A price per kWh below one øre is refused

A money field in the minor unit per kWh refuses values between 0 and 1 with `price_in_minor_unit` ("The box is in øre, not kroner"), and the help says the fixed price is without VAT. A site stored Norgespris as 0.004 NOK: "0,4" typed into the øre box. Affects D1 §6.
**Rejected:** guessing kroner or øre by size - stores something nobody typed.

### D-0434 · Type first; the flow's own device list; labels for non-slug values

The load flow asks the type first ("Hva vil du styre?", all eight types). Its device list is a flow-built select: devices with a controllable entity, never PowerPlan's own, sorted in the language's alphabet, already-added ones marked and refused. A device no profile claims for the type is refused with `no_profile`; the match step no longer asks the type. Values that can't be keys (a 1.5 kW element) are labelled from the slug's translation. HA's `DeviceSelector` can't exclude or mark (H8). Affects D8 §5.2, §5.15.
**Rejected:** device first with a filtered type list - detection had offered an access point's LED as an appliance.

### D-0435 · A mode-steered thermostat's own setpoint is its comfort target too

`Runtime.comfort_role` covers a setpoint-steered thermostat's setpoint and now also a mode-steered one's where bound (the Heatit, switched between heat and eco). Such a load gets no `number.<load>_comfort`; turning its dial is adopted as `comfort_c`. PowerPlan never writes a mode-steered thermostat's setpoint, so that number is the household's comfort. Affects D8 §5.16.
**Rejected:** keeping PowerPlan's own comfort number - two knobs for one truth.

### D-0436 · The control select keeps five states; its labels use the glossary

`select.<load>_control`: Automatisk · Kjør nå · Ikke styr · Prøvemodus · Styres av noe annet. The old "Bare se på" and "Enheten styrer selv" sounded like "Ikke styr". Each state behaves differently: `off` lets go, `observe` plans and calibrates, `delegated` reserves nameplate without writing (D6). Affects D4 §5.
**Rejected:** dropping per-appliance observe - removes trialling one appliance while the rest are steered.

### D-0437 · The dashboard's version table is read from each release's frontend

`layout.FEATURES`: `distribution` from HA 2026.2.0, `repairs` and view `footer` from 2026.3.0, the "Add dashboard" listing (`window.customStrategies`) from 2026.5.0, read from the frontend build each release pins. At the 2026.3.0 floor only the listing is missing, and `docs/dashboard.md` gives the YAML. Affects D12 §5.5, §9 3.
**Rejected:** raising the floor to 2026.5 - D12 §2 says the dashboard doesn't move the floor.

### D-0438 · The appliance tiles follow D8 §5.16's entity set

The `loads` view draws `control`, `plan_status`, the type's own knobs, `ready_by`, `granted_power` with a trend where enabled, and the month's cost and savings. "Where the power goes" shows enabled `granted_power` rows and is left out when none is. D12 was drafted against the entity keys device attachment replaced. Affects D12 §5.1.
**Rejected:** `granted_power` on by default - it's diagnostic and off by design (D-0424).

### D-0439 · Several sites: four views each, on one dashboard

Without `entry_id` the builder builds every loaded site. One site keeps paths `overview`, `plan`, `loads`, `history`; with several, each gets them as `<path>-<entry_id>`, titled "‹site› · ‹view›". `entry_id` narrows to one. The strategy regenerates on every open, so new sites and appliances appear next time. Affects D12 §2, §6, §8.
**Rejected:** a site picker in each view's header - no built-in card switches a whole view.

### D-0440 · The dashboard headings live in the integration's translations

Headings are `selector.dashboard.options.<key>` in `strings.json` and `translations/*.json`, read with `async_get_translations` in the frontend's language. The layout is built in Python, so it reads words the way `flow/text.py` does, with HA's English fallback. Affects D12 §3.
**Rejected:** `frontend/src/i18n` - a second place for every word.

### D-0441 · The site plan's slots are the runtime's, rebuilt on adoption

`Runtime.plan_slots` is computed after every `engine.plan`, from the current slot to 48 h ahead: `start`, `end`, `ceiling_kwh` (from `Engine.window_ceiling_kwh`), `baseline_kwh` and `planned_kwh` by load from each plan's own slot kWh. `sensor.<site>_plan` publishes them with `window_min`, recorder-excluded. Only the runtime has curve, plans, tariff and baseline together, and adoption-time rebuilds add nothing to the tick. Affects D12 §5.6, D8 §5.5.
**Rejected:** a pure core function returning the rows - needs everything only the runtime holds.

### D-0442 · The plan calendar: "Planned runs", with cost and without a reason

`calendar.<site>_planned_runs` has one event per contiguous block with `envelope_w > 0`: summary "‹load›: ‹kWh› kWh", description with kWh and the approximate cost. The planner's reasons are English trail text (INV-50) and never shown raw. It writes on adoption and when its first event changes, not per tick. "Plan" alone would be the same word in both languages. Affects D12 §5.6, D8 §5.5.
**Rejected:** translated reasons - needs a closed set of reason keys from D5.

### D-0443 · A hand-written dashboard shim before the bundle

`frontend/dist/powerplan.js` starts as plain JavaScript: the `ll-strategy-dashboard-powerplan` element (one `callWS`, a markdown fallback linking troubleshooting), placeholder cards and the `customCards`/`customStrategies` entries, served as one file at `/powerplan_static/powerplan.js?v=<version>` beside the brand icon. It works with built-in cards only; TypeScript and esbuild come next. Affects D12 §3, §5.5.
**Rejected:** setting up the build now - the next step's module list.

### D-0444 · A charger with no car is parked at 0 A

`Ev.kind_ctx` sets `park` when `connected is False`, as for the session-done latch, so `MODULATE` stops the charger (0 A, switch off where it has one). A link loss parks nothing. Before, a zero grant with no session was a hold, and the next car drew the last session's 16-32 A until a replan, up to 15 minutes on Zaptec. With no car there's no session for INV-28's cliff to protect. Row 3 makes parking every tick free. Affects D4 §5.11.
**Rejected:** writing 0 A only on the unplug edge - a limit raised by hand while empty would stand until the next car.

### D-0445 · The frontend sources live at the repository root

TypeScript sources, `package.json`, the lockfile and the esbuild config are in `frontend/`; the build writes to `custom_components/powerplan/frontend/dist/`, which is committed. HACS installs the whole integration directory, so sources and `node_modules` don't belong there. Affects D12 §3.
**Rejected:** `custom_components/powerplan/frontend/src` - every household would carry the sources.

### D-0446 · The cards' words come in their config, from the integration's translations

The timeline and gauge take a `labels` map the builder fills from `selector.dashboard.options.card_*`, plus load names and the currency. Only the strategy's "could not load" text is in the bundle, since it shows when the translation fetch itself failed. The frontend doesn't load an integration's `selector` strings for a card. Affects D12 §4.
**Rejected:** `hass.localize` in the card - works only for categories the frontend happened to load, and breaks in a hand-made dashboard.

### D-0447 · The timeline draws power, not energy

Every series is average kW: a slot's planned kWh over its length, the baseline likewise, a window's ceiling over the window. A 60-minute ceiling in kWh beside 15-minute bars in kWh misleads fourfold; in kW, a bar above the line is over. Estimated prices are a shaded, labelled band. Affects D12 §5.2.
**Rejected:** kWh per slot with the ceiling divided down - reads smaller than the number on the bill.

### D-0448 · The bundle is split, served as a directory and keyed by content

esbuild splits: `powerplan.js` (~12 kB) registers the elements on every page, and ECharts (~565 kB) is a chunk the timeline imports on first draw. `frontend/dist` is served at `/powerplan_frontend`, the module's cache key is its SHA-256 prefix, and chunks carry their own hashes. The module URL is added only when `frontend` is loaded, or a headless HA fails setup. HA loads an extra module on every page for every user. Affects D12 §3, §5.5, §8.
**Rejected:** one file - 565 kB on every page load.

### D-0449 · The glossary scan is a word list per language, over every rendered screen

`DESIGN_WORDS` holds one pattern per language with HLD §2's design terms and their Norwegian counterparts, skipping placeholders, inline code and the household's own names, with two written exceptions ("tidssone", "sats"). Scanning the rendered step also catches labels Python assembles. Affects D8 §5.15 rule 7.
**Rejected:** an allow-list - can't be written for free text.

### D-0450 · Every "observe" label reads trial mode

The `observe` strategy option and `plan_status`'s `observing` read Prøvemodus / Trial mode, as the control select does (D-0436). One concept, one label (HLD §2).
**Rejected:** "Ser bare på" on the status - the select sets what the status reports.

### D-0451 · The attention tile shows `meter_health`, hidden at `ok`

The meter tile is `sensor.<site>_meter_health` with `visibility: state_not: ok`, so it also shows while the sensor is unavailable; left out on a price-only site. Affects D12 §5.1.
**Rejected:** showing only for `degraded` and `stale` - an unavailable health sensor is itself worth a look.

### D-0452 · The month gauge colours steps by the target select's `target_kw`

Steps whose lower bound is under the target's kW are green, the next amber, the rest red. Every target option, `auto` included, publishes its kW. Affects D12 §5.3.
**Rejected:** parsing `step_<n>` - says nothing for `auto` or a kW target.

### D-0453 · History's logbook targets `event.<site>` and every `plan_status`

`logbook.py` files each appliance's events under its `plan_status`, so a logbook targeting only the event entity would drop them. Affects D12 §5.1, §5.6.
**Rejected:** the event entity alone - hides the per-appliance rows.

### D-0454 · The appliance colours are HA's 53-colour palette, in order

`HA_GRAPH_PALETTE` is HA's `--color-1…53` from the 2026.9 frontend, repeating after 53; the timeline's fallback is the same list, so a load keeps its colour between our cards and HA's graphs. Affects D12 §5.8.
**Rejected:** a ten-colour list that only looked like HA's.

### D-0455 · "One day above X kW" is measured against the target

`card_day_that_tips` reads "One day above {kw} kW takes you over your target". `days_that_matter.kw` is the largest peak today that keeps the metric at or under the target (D2 §5.6), which equals the next step's boundary only when the target is the current step. Affects D12 §5.3.
**Rejected:** "moves you up" - wrong once a household picks a lower target.

### D-0456 · The "why this plan?" table uses the flows' strategy words

Strategies are named with `selector.strategy.options.<key>`, the load flow's labels; `schedule` gains its label there. One concept, one label (D8 §5.15 rule 7), and a test holds every registered strategy to a label. Affects D12 §5.9.
**Rejected:** shorter dashboard-only words - two names for one setting.

### D-0457 · `powerplan.js` has no static import; the cards load by hashed name

esbuild runs twice: `chunks/cards-<hash>.js` split with ECharts, then `powerplan.js`, which defines the strategy, registers listings and dynamically imports the cards by the name passed in via `define`. HA gives a custom strategy 5 s to define itself, and a static import of a shared chunk was one more round trip on cold load, enough to time out. A test reads the built module for static imports. Affects D12 §3, §5.10.
**Rejected:** one pass with a literal `import("./cards")` - esbuild hoists a helper into a chunk the entry imports statically.

### D-0458 · Until the month gauge lands, Now's capacity section is two built-in tiles

The `level` and `advice` tiles under the `projected_level` badge, replaced by the window card's `mode: month` when it exists. Each step ships a working dashboard. Affects D12 §5.1.
**Rejected:** leaving the section out meanwhile - the household would lose the level.

### D-0459 · History's summary shows the capacity basis as the month's `max`

The metric's `statistic` card uses `stat_type: max` over the calendar month; the card has no "state", and within a period the metric only rises. The period summary card replaces it later. Affects D12 §5.1.
**Rejected:** a tile on `metric` - out of place among three `statistic` cards.

### D-0460 · The timeline's legend and touch readout are HTML

The legend is a row of buttons under the canvas toggling series through ECharts' hidden legend; the touch readout is two lines of text under the price strip. An ECharts legend takes a height the grid can't know in advance, so the price strip couldn't sit at a fixed place, and buttons are larger tap targets. Affects D12 §5.2 rules 9, 11.
**Rejected:** ECharts' plain legend - its wrapped height moves the grids at every width.

### D-0461 · The price strip is a custom series with a canvas-pattern hatch

One rectangle per price run via a `custom` series on a second grid, filled with `--primary-color` at the run's alpha; an estimated run gets a second rectangle with a 45° canvas pattern. ECharts' `decal` applies only to series item styles and doesn't reach custom series or mark areas. Affects D12 §5.2 rule 5.
**Rejected:** bars with a decal - bars on a time axis can't span unequal slot runs edge to edge.

### D-0462 · The y scale steps through 1, 2, 3, 4 and 5 × 10ⁿ

`niceScale(max)` picks the smallest step from 1, 2, 3, 4, 5, 10 × 10ⁿ covering `max` in at most five ticks: 10.8 becomes 12 in steps of 3. A 10 kW limit then sits inside a 12 kW plot instead of 15 or 20. Affects D12 §5.2 rule 3.
**Rejected:** `max: 'dataMax'` - puts the limit on the plot's edge.

### D-0463 · The month gauge runs to the bound two steps above the current step

The arc ends at the upper bound of the step two above the current one, or the highest finite bound. With the metric at 8.97 kW in the 5-10 step, the scale ends at 20 kW, showing the next step in full with room past it. Affects D12 §5.3.
**Rejected:** ending at the next step's bound - the needle sits at 60 % and the next step fills the rest.

### D-0464 · Over more than a day, History's timeline draws daily energy without the limit

For one day, `mode: history` draws hourly grid kWh against the limit and the month's third-highest day, marks the highest hour and adds the price strip. For longer ranges it draws daily kWh by source with a dot on each day that counts, and no limit line: 77 kWh a day and a 10 kWh/h limit share no axis. The peaks gauge shows each day's highest hour. Affects D12 §5.7.
**Rejected:** each day's highest hour as the bar - the usage section is about how much.

### D-0465 · The layout carries the grid statistics; the cards fetch statistics themselves

`ws.py` reads the Energy preferences' grid import statistics in both of HA's shapes and passes them as `grid_entities`. The picker cards take only the period from `energy_powerplan` and fetch `recorder/statistics_during_period` themselves, falling back to the calendar month without a picker. The collection's `stats` are HA-internal. Affects D12 §5.7.
**Rejected:** reading `data.stats` - an internal shape that has already changed once.

### D-0470 · A month total publishes nothing until the ledger has priced a slot

`AccountingHook.status()` publishes `month_start` and `since` only once the ledger is `opened`; until then cost and savings read `None` with no `last_reset`. After that, `last_reset` is `max(month_start, since)`. HA's recorder zero-points a total's sum at its first valid state, and a changed `last_reset` starts a new cycle from 0: a placeholder `0` with the ledger's epoch as `last_reset`, followed by the real month total, booked the whole month's capacity fee as one hour's change. `test_monetary_statistics.py` runs the real recorder. Affects D12 §5.6, D8 §5.5.
**Rejected:** keeping the month start and hiding only the placeholder - claims a month the total never covered.

### D-0471 · A load's month rows reset at the ledger's start

`cost_month`, `savings_month` and `energy_month` use the site's `accrual_reset`; D11 keeps no per-load start. A load added mid-month starts at 0, which HA zero-points whatever `last_reset` says. Affects D12 §5.6.
**Rejected:** a per-load start in `LoadMonthRec` - nothing reads it yet.

### D-0472 · `level.steps` stays out of the recorder

The ladder is static per tariff version, but `level`'s other attributes change with each closed window, and each row would copy the ladder. The gauge reads live state (INV-61's spirit). Affects D12 §5.6.
**Rejected:** recording it to redraw past months - the tariff version history already holds it.

### D-0473 · The logbook's entity rides in the bus event's data

`Runtime.fire_event` adds `entity_id` to the bus payload: the appliance's `plan_status` for an event with a load, else `event.<site>`. HA's logbook filters external events by `entity_id` in the data, in both query and live stream; an `entity_id` attribute on a state would make HA treat it as a group. Events fired before the event entity is registered don't show. Affects D12 §5.6, D8 §5.6.
**Rejected:** every event on `event.<site>` only - the lines belong under the appliance.

### D-0474 · Logbook lines are `selector.logbook.options`, in HA's language

Each line is a template under `selector.logbook.options.<key>`, `<kind>` or `<kind>_<state>`; `logbook.MESSAGES` lists every key and a test holds both languages to it. Numbers and money go through `flow.text.Text` in `hass.config.language`. A describer is synchronous with no user language, and one key per state keeps Norwegian word order right. Affects D12 §5.6.
**Rejected:** `exceptions.<key>.message` - these lines aren't errors.

### D-0475 · A logbook line is named after the appliance as the household named it

The line's `name` is `load.config.name`, or the home's name for a site event. `plan_status` sits on the hardware device, which keeps its maker's name. Affects D12 §5.6.
**Rejected:** letting the frontend name it from the entity - "Charger Plan status".

### D-0480 · The action reason has a key, `ActionReason`, beside the English sentence

`core/loads/kinds/base.py` declares `ActionReason` (43 keys) and `ReasonParams`. `Quantised`, `Hold`, `Command`, `Decision` and `ApplyResult` gain `reason_key` and `reason_params` beside `reason`, and every producer names a key (gate rows, the four kinds, letting go). `plan_status` publishes them, volatile. `SnapshotSchema` 6. The dashboard can't translate "3 s of 600 s elapsed", and parsing English back breaks with every sentence change. An AST test holds every construction in `core/loads/` to an explicit key. Affects D8 §5.16, D12 §5.6, D7 §4.1.
**Rejected:** one `Reason(key, params)` rendering its own English - every log line and test would change for a surface need.

### D-0481 · Each reason key is translated twice: a label for HA, a sentence for the dashboard

`entity.sensor.plan_status.state_attributes.reason_key.state.<key>` holds a plain label; `selector.action_reason.options.<key>` holds the sentence with params. hassfest refuses placeholders in `state_attributes`, and HA's more-info dialog translates from the plain label; the dashboard renders the sentence with `hass.localize`. Affects D12 §5.6.
**Rejected:** the sentence only under `selector.dashboard` - the more-info dialog would show a raw key.

### D-0482 · `sensor.<site>_plan`'s day starts at the window `now` is in

The state sums every adopted plan's `slot.kwh` over the 24 h from the start of the current window, prorating straddling slots; `by_load` still carries whole plans. It used to sum up to 48 h under a name read as the next 24 h. Starting at `now` itself would change the value every tick and write a recorder row each time. Affects D12 §5.6.
**Rejected:** `[now, now + 24 h)` - moves every tick and drops energy the timeline still shows.

### D-0483 · The baseline seed subtracts each load's bound power history

`_seed_baseline` passes every load's `POWER`-role entity as its `LoadSource.power_entity_id`; the query asks for kWh and W so units arrive as `reconstruct` integrates them. A `mean` row becomes two points (its start and end) and long-term rows come before short-term ones, so the trapezoid integrates each period's own energy. Supersedes D-0315 and D-0351's "nothing is subtracted". The reference baseline was the whole register (a night hour at 4.9 kW against a live 1.3 kW uncontrolled, because the car and tank charged at night), and one more week would have fed that into D6's projection. Affects D10 §5.2.
**Rejected:** each load's energy register - D10 §2's table has no energy row and fewer loads bind one. Auto-reseeding old baselines - a schema change for a one-time button press.

### D-0484 · A plan slot's `baseline_kwh` is what D10 offers, `None` below the gate

`_plan_slots` reads `Forecasts.baseline_kwh`, which is `None` below `offer_confidence`, instead of the ungated `Baseline.energy_kwh`. The timeline was drawing a baseline neither planner nor budget used. Affects D12 §5.6.
**Rejected:** drawing it hatched as "still learning" - a visual state for a fortnight's warm-up, of a number the plan wasn't built on.

### D-0485 · Setup binds an answered off-device sensor a subentry was saved without

`async_setup_entry` runs `_bind_answered_roles`: for each load subentry, `extra_bindings` binds the type's answered off-device sensors (`soc_entity`, the heat pump's outdoor and outlet) where the subentry lacks the role. An entity with no state yet binds on a later start. A charger saved before the flow bound `soc_entity` had no SoC, so `deadline_fill` planned nothing.
**Rejected:** a config-entry migration - runs once, often before the car's cloud sensor has loaded.

### D-0486 · A reconfigure offers the power and energy meters under Advanced

On reconfigure, `question_schema(meters=…)` adds `role_power` and `role_energy` pickers, pre-filled and filtered as in the match step. A new entity is re-bound off its own unit, a cleared picker unbinds an optional meter, and an unreadable one leaves the binding. Reconfigure skips the match step, so a heat pump with a plug meter had no way to bind it and reserved its 7.5 kW rating. Affects D8 §5.2.
**Rejected:** sending every reconfigure through the match step - re-asks every role of an unchanged device.

### D-0487 · The shared style sheet is a CSS string

`frontend/src/styles.ts` exports `ppStyles` as a string each card puts first in its shadow root, with `tooltipStyle()` for ECharts. The cards are plain `HTMLElement`s writing `innerHTML`; Lit isn't a dependency. Affects D12 §3, §5.11.
**Rejected:** moving the cards to Lit - rewrites four elements for no visible change and ~15 kB.

### D-0488 · The month gauge colours steps by index against the target step

`targetStep()` finds the target's index (`step_N`, else the step whose lower bound is `lower_kw`, else the one holding `target_kw`, else the current step); steps at or below it are `ok`, the next `warn`, above `alert`. The select publishes `target_kw: null` for a step option, and `Number(null)` is 0, which drew a healthy month as an alarm; D-0452's rule read a number not always there. Affects D12 §5.3, §5.11.
**Rejected:** publishing `target_kw` for every option - the next missing attribute breaks it again.

### D-0489 · `plan_status` carries the next run and the deadline as clock strings

`next_run` is local `HH:MM` of `next_start` when it's in the future, else `""`; `deadline_time` likewise. Tiles show `[state, next_run]` (the car `[state, deadline_time]`), and the subview shows "running now" instead while running. A tile renders a datetime attribute as a full timestamp, and `next_start` followed the clock during a run. Affects D8 §5.16, D12 §5.1, §5.6.
**Rejected:** rounding `next_start` in core - changes what automations read (INV-50).

### D-0490 · The logbook line names the appliance

`async_describe_events` no longer returns `entity_id` (the bus payload keeps it for filtering): HA's logbook titles a line with the entity's own name when one is given, "Plan status". A plan adopted while its load draws reads `plan_adopted_now`. The card matches events by the same target list that keeps `plan_status`'s state rows, and no target keeps one without the other. Affects D12 §5.6, §5.11.
**Rejected:** logging everything on one quiet entity - moves every appliance's events out of its own logbook.

### D-0491 · The timeline sizes its own canvas; the card is `rows: auto`

The canvas is the plot (250 px on desktop, 180 px under 500 px) plus fixed margins; toggle, readout and legend flow around it and the card grows. A fixed seven-row card on a phone with a four-line legend left about 120 px of plot. Affects D12 §5.1, §5.2, §5.11.
**Rejected:** a fixed height with the legend behind a toggle - the legend is where each appliance's kWh is written.

### D-0492 · Next runs is its own element; the tables' words move to `card_*`

`custom:powerplan-runs-card` replaces the next-runs markdown, and the period summary's views replace the per-appliance graph and cost markdown; their words become `card_*` so they arrive through `labels`. The "why" card writes `currency_short` ("kr") for a NOK site and the ISO code otherwise. No table or list of values is a markdown card (D12 §5.11). Affects D12 §4, §5.1, §5.9, §5.11.
**Rejected:** keeping the markdown with a "now" column - the wide bordered table stays.

### D-0493 · `unrecorded` reaches the recorder through HA's per-class set

HA folds `_unrecorded_attributes` once per class in `__init_subclass__`, so a per-instance assignment never reached the recorder: `sensor.<site>_plan` (29 kB) and `price_forecast` (34 kB) exceeded the recorder's 16 kB limit, and large attributes landed in every row (INV-61). `PowerplanEntity._set_unrecorded` also sets the private `_Entity__combined_unrecorded_attributes` on the instance, and a recorder-level test fails if HA renames it.
**Rejected:** one subclass per row with a class-level set - dynamic classes for a row table.

### D-0520 · The entry keeps the tariff itself; a template is never a `TariffSpec`

The tariff step stores the preset's JSON, filled with the household's numbers, as `entry.data.tariff.spec`, and `runtime._spec` builds from that copy, falling back to the file only for older entries. D2 §8 said presets are copied at setup, and nothing did: retiring a file would have stopped sites on it from setting up, and the contracted kW asked in the flow was read by nothing. A template (`"template": true`, nulls where the bill answers) stays raw: `loader.load()` refuses it, `fill_template()` completes it (D2 §9 21 by construction). Affects D2 §4, §6, §8.
**Rejected:** copying into the site store - the entry is what the flow writes and reads back, and the store is rebuilt from it.

### D-0521 · Spain and the Netherlands ship as templates; their kW are the household's

`es/2_0td` (2.0TD's two power periods, BOE-A-2020-1066 art. 7.4) and `nl/connection` (ACM) carry the rule with `limit_kw: null`; the flow starts from `LIMIT_DEFAULTS` (ES 4.6/5.75 kW, NL 17.25 kW) and keeps the answer. No tolerance is assumed: the interruptor and the main fuse trip at the contract, the safe direction (INV-15). The kW had been flow defaults written into the files as facts (PLAN §7 dec. 21). Affects D2 §6.
**Rejected:** keeping a 10 % ICP tolerance - widely described, but no operator document says so.

### D-0522 · What a release removes keeps running: `RETIRED`

`loader.RETIRED` maps each removed file to what its sites run on until reconfigured, with `preset_outdated` raised: `no/tensio` → `no/tensio-ts`; the others → `custom`. Removed because they couldn't be one operator document per version: APS R-3 and SRP E-27 (seasonal), Ausgrid (c/kVA/day), Finland (no national household tariff before 2029), Helen (needs an nth-highest term), Ellevio's old effect terms (the page is gone). The US and Australia return later from their own sources, season by season. Affects D2 §3, §8, §10.
**Rejected:** mapping a retired national file to the largest operator - bills a household on another area's rate.

### D-0523 · A grid charge published with levies included says so

A preset's `energy_components` may carry `includes` (`["vat", "levy"]`), and the add-on step then doesn't offer `levy`; VAT stays. Norwegian sheets publish the energy charge including VAT, forbruksavgift and Enova, and the chain already ran the preset after `vat`, but Norway pre-ticked `levy`, counting about 10 øre/kWh twice. Affects D1 §6, D2 §6.
**Rejected:** storing the charge exclusive and composing - the inclusive figure is the one a household checks against its bill (INV-67).

### D-0524 · Tensio's two areas, from Tensio's own documents

`no/tensio-ts` and `no/tensio-tn` carry three versions from Tensio's PDFs and price page, cross-checked with NVE's tariff data. The earliest version's energy rates are as printed, though forbruksavgift changed mid-period. The benchmark's next-year version is a fixture (the latest steps × 1.06), labelled synthetic. The old file's steps matched neither area and its top steps were guessed. Affects D2 §3, §9 22, D9 §5.9.
**Rejected:** splitting a version at the levy change - the sheet prints one rate.

### D-0525 · The flow shows money to the cent

`Text.money` renders at most two decimals. VREG's sheet carries 49,4036563 EUR/kW/year; the bill shows 49,40. Per-kWh prices are unaffected. Affects D8 §5.15.
**Rejected:** rounding in the preset - the file keeps the regulator's number.

### D-0526 · The template's steps form: twelve rows, bounds offered, empty means no capacity

`tariff_steps` asks up to twelve rows of "up to kW" and "per month"; bounds 2/5/10/…/100 are offered, fees never pre-filled; the last row with a fee is the open top. No fee at all falls back to `custom` and the summary says so; one row or non-rising bounds are refused. Every answer needs a default (HLD §7.9). Affects D2 §6, D8 §5.15.
**Rejected:** a text field for the table - never free text for a tariff (HLD §7.9).

### D-0527 · One Fluvius preset per VREG area

Eight `be/fluvius-<area>` files, each with the area's average-monthly-peak rate from its own VREG sheet (49,40 Antwerpen … 57,10 West) and the 2.5 kW minimum from VREG's capacity-tariff page, confirmed by each sheet's minimum contribution. The single file carried a regional average no one is billed; areas differ by 16 %. `be_quarter` runs on Imewo. Affects D2 §3, D9 §5.9.
**Rejected:** one file with the rate asked in the flow - the areas are published facts.

### D-0494 · A slot's reserve is D10's residual σ at the 90th percentile

`slots[].baseline_p90_kwh` = `baseline_kwh` + 1.2816 · σ · the slot's hours, with σ from `Forecasts.residual_sigma_w`; `None` without either. The forecast draws the difference hatched as "Reserve". D10 already learns and persists σ per bin, which D6's reserve uses; a second hour-of-week profile would disagree with the planner's. Affects D12 §5.6.
**Rejected:** an empirical per-bin 90th percentile - D10 keeps σ, not samples. (See D-0498.)

### D-0495 · The price card reads the curve: spot per slot, and a curve without the fixed price

`price_forecast`'s slots gain `energy` (the spot component with its VAT share) and, with a `FixedPrice` modifier, `reference`: the same slot from a second curve built without it. The curve already holds raw prices, VAT and grid tariff in the household's own configuration, whatever the source. Affects D12 §5.6.
**Rejected:** a websocket that calls Nord Pool directly - a second price path and a service call outside INV-3's allowed files.

### D-0496 · The dashboard prototype's cards are ported, not dropped in

The appliances card, dialog, price card, forecast and status keep the prototype's markup and CSS. Their words come from `card_*` translations (D-0446), the status reads `plan_status`'s states, the forecast is the timeline card's plan mode, and the dialog's mini plan is the timeline element. The prototype's backend pieces are replaced by D-0470/0471, D-0494/0498 and D-0495; it was written against assumed entity ids. Affects D12 §3, §4, §5.1.
**Rejected:** porting its status guard as is - it changes how the engine detects a hand on the dial (see D-0497).

### D-0497 · A hand on the dial: any recent write of ours, a person at once, the device after 2 min; `display_status` holds 90 s

`GateState.recent_context_ids` keeps the 8 contexts before the last; a change whose context or parent is among them is ours, one with a `user_id` is the household's at once, and one with neither (made at the device) is adopted only if it still stands `OVERRIDE_GRACE` (2 min) later. `plan_status` gains `display_status`: a new state shows once it has held 90 s, except device, hand and mode changes. Floor loops were flipping between `running_plan` and `manual_override` every few minutes (86 changes in 6 h on one) as devices re-reported under fresh contexts. Affects D4 §4.1, §9 28; D8 §5.16.
**Rejected:** debouncing only in the card - the flaps reached the logbook, the comfort target and automations.

### D-0498 · The reserve is the empirical hour-of-week P90, σ only without it

`HourOfWeekQuantile` folds the last 28 days of the uncontrolled history D10 is seeded from into a 90th percentile per local week-hour (day-hour under 3 samples; hours over 30 kWh dropped). `async_seed` returns that history, so one recorder read feeds both, rebuilt with every seed. `baseline_p90_kwh = max(profile, baseline)`, D-0494's σ form where the profile has no bin. A real quantile, not a normal assumption. Affects D10 §5.2, D12 §5.6.
**Rejected:** a separate store and hourly refresh - the seed reads 60 days at every start, and a 28-day quantile doesn't move in an hour.

### D-0499 · What the fixed price saved this month is a sensor

`sensor.<site>_fixed_price_savings` (monetary, `total`, reset at the local month's start), only with a `FixedPrice` modifier: each metered hour × (its price without the fixed price − its price), both through the site's own chain so VAT and grid cancel. Past days' prices come from the sources' `fetch(day)` once each, kept in memory; refreshed at :07. Attributes `today`, `kwh`. A sensor gives the figure a history and keeps service calls in the sources (INV-3). Affects D8 §5.5, D12 §5.6.
**Rejected:** a D11 counterfactual tariff - what-if views are deferred, and the ledger keeps no raw prices.

### D-0500 · The fits run daily and reach the load as a parameter the engine rebuilds its store from

`_refresh_fits` runs `fit_all` at 03:17:30 local daily (and at start when none is stored) over a `LoadHistory` per fittable load, assembled by `recorder_fits.py` from 60 days of power statistics and state history (attributes included, for `current_temperature` and the weather entity). A passing fit puts `effective` into `load_params` under the store's parameter; a failing one takes ours back (INV-63). `_apply_load_knobs` rebuilds the load through the registry when such a parameter changes, so the store and D11's shadow see it next plan. Fits persist in the `forecasts` section, and each is published as a disabled diagnostic `sensor.<load>_learned_<key>`. `nameplate` is published but not applied. Affects D10 §5.6, §9 19.
**Rejected:** carrying fits in D4's `Learned` - nothing that builds a store reads it.

### D-0501 · What holding a thermal store costs is in the plan, apart from what it moves

`PlanSlot.hold_kwh`: the standing loss of a slot `heat_capacitor` holds or banks in, or `best_save` leaves free, at its setpoint: loss coefficient × (target − forecast outdoor) where known, else the loop's measured mean draw for that local hour over 14 days (`Forecasts.hold_w`), else 0. Coasting and postponed slots hold nothing. It's priced in the plan's cost and counted in the window projection, but not in `planned_kwh`, so it never makes a need look covered. Floor loops at target showed nothing planned while drawing all day, and since D-0483 that draw was in no projection. D-0216 expects the slab fit to fail, so the measured draw is the usual source. Affects D5 §5.5, §5.7, §9 22; D7; D10; D12 §5.6.
**Rejected:** holding energy in `PlanSlot.kwh` - a floor still catching up would read covered.

### D-0502 · An EV's charging sessions are cut from its charger's power and the car's SoC

`sessions_from` makes sessions from the charger's power (above 10 % of nameplate, gaps under 30 min joined, at least 15 min), energy from the charger's register where bound, else integrated power; SoC is the car's reading near each edge (up to 6 h before the start, 2 h after the end), or the session is dropped. `charge_efficiency` fits over them, and a gated result becomes `charge_eff`, rebuilding the EV's `EnergyStore` (D-0500). Affects D10 §5.6, §9 8.
**Rejected:** sessions from the charger's status history - vocabularies differ, and some report charging at 0 W.

### D-0503 · Holding energy never decides adoption, and a kept plan carries it

`should_adopt` compares `moved_cost`, the plan's cost less holding, and a plan the hysteresis keeps gets the fresh plan's `hold_kwh` per slot. A plan that prices holding is always dearer, so thermal plans built before holding was known were never replaced and published zero holding. Affects D5 §5.9, §9 22.
**Rejected:** comparing total cost - holding is the same whichever plan wins.

### D-0504 · The logbook describer reads `time_fired_ts` from the row

`logbook._fired` takes `time_fired` from a bus `Event` and `row[TIME_FIRED_TS_POS]` from the `LazyEventPartialState` the logbook processor hands over, which has no `time_fired_ts` attribute. Every PowerPlan logbook line was failing to render. The test builds the real class via `async_event_to_row`.
**Rejected:** a stand-in object in the test - it's what hid the bug.

### D-0505 · The baseline seed cuts quarter-hour windows where the 5-minute statistics reach

`async_seed` uses 15-minute windows over the last 10 days' 5-minute statistics and hourly windows beyond; the P90 profile sums quarters back into hours. A store seeded earlier (`seed_version` absent or 1) is re-seeded once at startup, then records `seed_version: 2`. Hourly-only seeding gave each week-hour one sample a week, n_eff ≈ 4.6 after decay, confidence 0.57, under the 0.6 gate: no baseline on any slot. Four quarters of one hour aren't four independent samples, so n_eff overstates recent evidence, as it already does for a quarter-hour site's live updates. Affects D10 §5.2.
**Rejected:** drawing the baseline below the gate on the dashboard - D-0484 removed exactly that.

### D-0506 · Adoption compares both plans on today's curve

`moved_cost(plan, curve, now)` is the plan's estimate less holding, plus each remaining slot's moved energy times its price change since the plan was built. Equal prices leave the comparison as it was; a kept plan keeps its own prices, so noise never changes it (D5 §9 3). Plans built on yesterday's cheaper curve looked cheaper than any fresh plan priced today and were never replaced. Affects D5 §5.9.
**Rejected:** each plan on its own prices - a stale plan wins forever.

### D-0507 · A planned pause is drawn, not left blank

`slots[].paused` lists loads whose plan stands still in the slot (envelope 0: a coast, a postponement; INV-30). The appliances card draws each such stretch as a hatched band with "Senket til 22:00" where it fits. Five loops coasting before a 22:00 price drop read as missing data. Affects D12 §5.6.
**Rejected:** leaving pauses blank - indistinguishable from no data.

### D-0530 · "Tariff model" means the typed rules; the evaluator's protocol becomes `TariffEvaluator`

D2's "grammar" becomes the tariff model (`grammar.py` → `model.py`, `Grammar` → `TariffRule`, `TariffVersion.grammar` → `.rules`), since "grammar" read as text parsing (D13 O25). The evaluator's protocol, already called `TariffModel`, becomes `TariffEvaluator`, so one term doesn't mean two things. D2 §9 42 asserts nothing imports the old names. Affects HLD §2, §6.2; D2 §3, §4.
**Rejected:** "tariff rules" in `rules.py` - `core/tariffs/rules/` already holds the rule templates.

### D-0531 · Only a changed setpoint value is a hand on the dial

`_adopt_setpoint_overrides` remembers the last value seen, not `(value, context)`; a report with the same value is no change whatever its context. After a restart that found the tank's dial at 75 °C with our record at 45 °C, the next temperature report under a fresh context was adopted as a 75 °C comfort target, and the tank held 75 °C all day. Affects D4 §4.1, D8 §9 28.
**Rejected:** asking `setpoint_origin` on every report - a fresh context isn't a change.

### D-0532 · `max_age` is one day-ahead cycle: 36 h

`compose.DEFAULT_MAX_AGE` goes from 12 h to 36 h. A day-ahead source is fetched once a day, so today's rows are ~11 h old at midnight, and 12 h marked every final auction price `STALE` from 01:00, doubling the hysteresis all day. Affects D1 §3, §6.
**Rejected:** re-fetching today at every publication - a call a day to learn nothing, since the auction result is final.

### D-0540 · The entity tables are read from the reference house's registry, through pytest

`entities.md`'s `entities:home` and `entities:appliance` blocks come from the entity registry after `tests/docs/test_generated.py` adds the reference house's eight appliances through the flow; that test is their stale check, and `POWERPLAN_DOCS_WRITE=1` (via `tools/docs.py --write`) writes them. Unit, category and default are the registry entry's. Which entities exist and which are enabled is decided at setup in five places; only the registry holds the answer. Affects D14 §5.6.
**Rejected:** introspecting descriptions statically - the per-runtime `enabled` callables can't be evaluated without a site.

### D-0541 · The pending list is by page; the checks a page needs wait for it

`tests/docs/pages_pending.txt` names pages, and each check skips what a pending page covers (built URLs, key families, a flow's step-link and budget rules; the option-label budget until no flow page is pending). No-URL and placeholder rules run now. `step_placeholders` takes the type, since an appliance's steps link its type's page; the `fields` block is `fields:<flow>.<step>`. The budget violations and missing links are the rewrite's work. Affects D14 §3.2, §5.5, §5.6, §9 1, 3, 4.
**Rejected:** an allow-list of today's violations - 91 entries churned twice.

### D-0542 · The move to `design/` is one idempotent command

`HLD.md`, `PLAN.md`, `DECISIONS.md`, `lld/`, `reviews/`, `benchmarks/` and the design roster move from `docs/` to `design/` with `git mv`, `docs/brand/` to `docs/images/brand/`, and every citing file is rewritten by one command an open branch can re-run before it merges:

```
git grep -lE 'docs/(HLD|PLAN|DECISIONS)\.md|docs/(lld|reviews|benchmarks|brand)\b|"docs" / "(HLD\.md|PLAN\.md|DECISIONS\.md|lld|reviews|benchmarks)"' \
  | xargs perl -pi -e 's#\bdocs/(HLD|PLAN|DECISIONS)\.md#design/$1.md#g; s#\bdocs/(lld|reviews|benchmarks)\b#design/$1#g; s#\bdocs/brand\b#docs/images/brand#g; s#"docs" / "(HLD\.md|PLAN\.md|DECISIONS\.md|lld|reviews|benchmarks)"#"design" / "$1"#g'
```

`docs/README.md` becomes the user index. Affects D14 decision 1, appendix A.
**Rejected:** moving by hand per branch - 140 files.

### D-0543 · American spelling on every English screen

`en.json` and `strings.json` values use liters, meters, millimeters, program and recognizes (D14 appendix A); keys keep their spelling. `events.md` shows event labels verbatim, and the pages' spelling check would fail on "programme".
**Rejected:** changing only the units - the pages quote the rest.

### D-0580 · The price refresher lives on the runtime and reports through the catalogue

`price_refresh.PriceRefresher` (back-off 60, 120, 300, 600, 900 s; `prices_stale` after 30 min; `powerplan/refresh_prices`) is `runtime.price_refresher`, fetches through `Runtime.refresh_prices` (fetch, then replan) and tests `prices_known_now`; the runtime's own fetches report to it, so startup doesn't fetch twice. The issue is a catalogue row with `{site}` and its learn-more link (D8 §5.9). The reference house once planned 37 minutes on estimates after a restart. Affects D12 §5.15 F12, D8 §5.9.
**Rejected:** `hass.data[DOMAIN]` - integration state lives in `runtime_data`.

### D-0581 · A price slot's energy part carries the VAT that covers the energy

`slots[].energy` = `spot × (1 + r)`, `r` the summed rate of the `Vat` modifiers covering `spot` (`energy_vat_rate`). D-0495's total ÷ (total − VAT) is exact only when VAT covers every component; on the reference house VAT covers the energy alone (the grid tariff is entered incl. VAT), and the price card's split disagreed with the house's own sensors. Affects D12 §5.12, §5.15, §9 24.
**Rejected:** keeping the ratio - wrong whenever VAT doesn't cover everything.

### D-0582 · `powerplan/spot_prices` answers from the runtime, not from Nord Pool

The command returns area, currency, VAT, the fixed price and its source, `slots[{start, end, spot}]` for today and tomorrow, `tomorrow_available` and `effect`. `spot` is each slot's spot component on `Runtime.reference_curve` (D-0495); `fixed_price` is `FixedPrice.price × (1 + energy_vat_rate)`; `effect` is D-0499's saving. INV-3 keeps Nord Pool's action in `nordpool_action.py`, and the curves already hold spot and the fixed price. Affects D12 §5.15 F5.
**Rejected:** its own option chain for a fixed-price entity - a second source that can only disagree.

### D-0583 · A load's savings are unknown without a reference

`sensor.<appliance>_savings_month` is `guarded_savings(cost, cf_cost)`: `unknown` with `reason: no_reference` when `cf_cost` is missing or ≤ 0 under a positive cost, `no_cost` without a ledger row, else the ledger's savings. Five loads read exactly −cost: a missing counterfactual reported as a loss. D11's counterfactual never yields 0 kWh for a load that ran. Affects D12 §5.15 F10, D8 §5.5.
**Rejected:** treating 0 as a real reference - it's a missing one.

### D-0584 · The seed finds the meter's lag and skips windows it can't separate

`uncontrolled_history(..., meter_lag_h=None)` detects lag by correlating the hourly meter with the controlled sum at lag 0 and 1 (gain ≥ 0.10, ≥ 24 hours), and skips windows where every load starts. `_integral_kwh` bisects to the window's rows. An evening bump in "other usage" (3.5 kWh against ~1) came from a one-hour lag. Affects D10 §5.2, D12 §5.15 F11.
**Rejected:** a second baseline to cross-check - D10's windows suffice.

### D-0585 · The dashboard's price and appliance cards take the prototype's files as they are

The eleven TypeScript files of the second dashboard prototype go into `frontend/src/` unchanged (one fallback, D-0586), with its compiler options (`strict`, `noUnusedLocals`, no `noUncheckedIndexedAccess`, which alone raised 187 errors) and its nb and en word tables. The cards that now carry their own words get no `labels`, and 92 `card_*` keys only the earlier cards read are removed. What the earlier cards had that these lack (holds and pauses in the lanes, strategy words, a third language) is listed in D12 §5.15. Affects D12 §5.15, §11.
**Rejected:** porting the look onto the earlier cards - a rewrite of a design that was delivered as code.

### D-0586 · The bundle's key is the loader's own `?v=`; the fee falls back to the level sensor

`version-check.ts` reads `__PP_BUNDLE__`, which `bundle.ts` (the loader's first import) sets from `import.meta.url`'s `v` parameter; `powerplan/version` answers `dashboard.module_key()`, the same hash. The hash is of `powerplan.js` itself, so the build can't write it in. `price-card.ts` reads `capacity_fee`, else the level sensor's fee text. Affects D12 §5.15.
**Rejected:** a fee sensor - a new entity for one number on one card.

### D-0587 · Now's Plan card is `renderPlanMode` inside the timeline card

`timeline-card.ts` keeps its element, config and 12/24/48 toggle; with `rail_width` it hands a host `<div>` to `observePlanHost` and `renderPlanMode`. ECharts gains its `SVGRenderer`. A new element would have changed the layout's card type for nothing. Affects D12 §5.15.
**Rejected:** a separate plan element - same code, one more card type.

### D-0588 · Observe is its own counterfactual

A slot in `observe`, `delegated` or `off` settles with `cf := actual`, savings exactly 0; `observe` still steps and calibrates the shadow. In observe the device was uncontrolled, so any other figure would be the reference's own error credited to a plan that did nothing. Only `delegated`, `off` and kind `none` count as excluded slots. Affects D11 §5.9.1, §9 28.
**Rejected:** placing observe slots too - shows savings to a household that switched control off.

### D-0589 · Capacity savings are billed through the last settled day

A closed window waits in `pending_windows` until none of its slots is in an open buffer, then is recorded and `settled_through` moves. Capacity savings bill both books through the last complete day before that, from a `PeakHistory` view cut in `close.py`; the live capacity fee stays for cost (D-0179). A thermal reference settles at midnight and an EV's when the car stops wanting, so an open day's new peak would otherwise read as a capacity loss. Affects D11 §5.9.4, §9 29.
**Rejected:** cutting in D2 - only the ledger needs it.

### D-0590 · A month closes with every buffer settled; a session over the 1st is split

`_rollover` settles every open buffer into the closing month and flushes the windows that frees before freezing. An EV session open across midnight on the 1st settles with its energy so far and continues as a new session. A removed load's buffer settles at removal; one whose load stops arriving settles when over. A frozen month can't hold a cost without its counterfactual. Affects D11 §5.9.2.
**Rejected:** carrying the session - October's cost with November's savings.

### D-0591 · The savings sensor is the settled figure; the model figure is an attribute when trusted

`sensor.<appliance>_savings_month` is `cf_cost − settled_cost`, guarded on `settled_cost`, so an open day reads 0 with `pending: true`. New attributes `pending`, `model_confidence` and `model_savings` (only when `ok`). Guarding the live cost would read `no_reference` most of every day, and a model figure shown before calibration was the red bar this replaces. Affects D8 §5.5, D12 §5.15.
**Rejected:** the model figure as the state - uncalibrated numbers in the headline.

### D-0592 · A schema-1 accounting section is discarded, not migrated

`AccountingState.schema = 2`; an older section is logged and the ledger restarts, `partial`, on the next slot. Its month figures are the shadows' that the reference replaces, and its deferred EV slots have no settled counterfactual to migrate. Nothing released carries it (PLAN §7 dec. 36). HA's statistics take the restart as one negative change. Affects D11 §5.9.6, §9 32.
**Rejected:** a migration - nothing to migrate into.

### D-0550 · Where the copy by party lives, and how an entry reaches it

`HouseholdPrice`, its codec, `spec()`, `from_preset()` and the state's dated rates are `core/tariffs/household.py`; the chain by party (`GridEnergy`, `StateLevies`, `StateVat`, `chain()`, `PARTY`) is `core/pricing/party.py`. The config entry goes to minor version 2: `tariff.price` holds the copy and `tariff.review` kept overrides; `tariff.spec` goes. One pure function, `storage.migrate_tariff`, migrates older entries and builds new ones in the flow, so both are alike. `presets/` becomes `rules/`, company files staying as the migration's source until removed. The copy's schema is its codec. Affects D13 §3, §10, §12; D2 §3.
**Rejected:** keeping `tariff.spec` beside the copy for a release - two tariffs in one entry (INV-66).

### D-0551 · A price published with the state's share is split, not left alone

The grid stage writes the grid's own charge: a copy published incl. VAT and levies has them taken out at the national rates for the slot's date, and the state stage adds back the household zone's. `spec()` does the same to fees. Both copies of one tariff then give identical components, a Nord-Norge house pays Tensio's figures ex VAT, and the grid's VAT is the state's line. Source bases: `octopus_energy` and `amber` quote all four parts, other formats spot alone, and a row may state its own. Affects D1 §5.3, D13 §8, D11 §5.8.
**Rejected:** adding only what's missing - a Nord-Norge household would pay VAT it doesn't owe.

### D-0552 · Country modules: ISO codes, dated rates, conditions

33 modules keyed by ISO 3166 as `hass.config.country` holds it (`GB`, `GR`), each with its TEDB code. A date before a module's first rate uses that rate. A rate may be limited to a contracted power (`upto_kw`: Spain's 10 %) and carry an announced end (`until`: Ireland's 9 %), which `preset_age.py` lists as it nears. Zones are keys (`NO`: `nord`, `tiltakssone`; `SE`: `norr`). Levies ship for NO (forbruksavgift, Enova) and SE (energiskatt). `vat_check.py` reads TEDB's electricity rate, ignoring exempted rows. Affects D13 §5.1, §9, §9.1.
**Rejected:** refusing slots before the first rate - a backtest of older history would fail.

### D-0553 · Savings by party: the grid's VAT is the state's; the flow stops offering VAT

A slot's price carries its split by party (`SlotPrice.parts`); each load's month accrues savings by party at close, settlement and re-price, and capacity savings go to the grid. A Norgespris month saves on grid and state, never on the supplier. The add-on step no longer offers `vat` or `levy` where the module knows them, nor the grid charge the copy has (INV-71). Affects D11 §5.8, §9 21; D8 §5.1.
**Rejected:** booking the grid's VAT to the grid - a VAT change would move the grid's savings.

### D-0554 · What the first tariff-sources step leaves for later

`GridTariff` gets `currency`, `basis` and `capacity_id` and leaves out `per_load`, `switched` and `feed_in` until they have types; `SupplierContract` carries kind, basis and provenance until the supplier step moves the markup and fee into it. The next-year Tensio fixture stays in the rule format: the scenario runner builds curves from it, and it moves with the benchmark houses. Affects D13 §3, §12.1, §19 11.
**Rejected:** rebuilding the fixture now - changes every simulation for a file that's rewritten later.

### D-0555 · Where a country's time zones and its credit live

Each country module lists the IANA zones that pre-select it; `test_no_timezone_literals.py` allows zone names in `core/tariffs/countries/` too, since they only choose a flow default. The credit note is built from the sources registered for the country, each declaring its `Credit`. The country is asked in the electrical step, only without HA's. Two lists of one fact drift. Affects D13 §5.1, §6, §6.1; D8 §5.1.
**Rejected:** the zone table in `markets.py` - a market's clock and a country's zones are different facts.

### D-0556 · The flow by party

Electrical → `postcode` (where the module has a directory; Kartverket for NO) → `tariff` (the country's operators, then shipped rule files, then "not listed" and "enter it myself"; the credit under the list) → `tariff_product` → `tariff_zone` (when the operator spans zones and no postcode settled it) → `tariff_confirm` (the source's gaps) → `tariff_steps` (figures incl. VAT) → `tariff_preset` (the table, what the plan does, source and credit) → target → `prices` → `modifiers` (the supplier's own lines, including `supplier_tou`; Norgespris asked with the agreement) → `state` (VAT and levies stated, schemes asked) → export → carriers. State overrides (O4) are an options flow. An unresolved postcode skips the step; it's stored and redacted from diagnostics. Affects D13 §6; D8 §5.1, §5.17; D1 §5.4.
**Rejected:** `tou_schedule` with a party switch - one add-on writing two parties' lines.

### D-0557 · The renewal's clock and what it writes

The timer fires at 03:17 local on `renew_at`, never within an hour of start (INV-73). A failed renewal retries after an hour, doubling to a day; `tariff_stale` when it has failed and `valid_to` has passed. The renewal walks the same ladder with the household's confirmed answers; a source that disagrees with a confirmed field keeps the household's answer and raises `tariff_review`. The entry is written with the runtime's `_known_data` set first, so the listener doesn't reload; spec and chain are swapped in place. Affects D7 §5.9, D13 §10.
**Rejected:** reloading on renewal - releases and restores every load for no wiring change.

### D-0558 · Reasons and money by party on the surface

`plan_status` carries `why_party`, `why_until` and `why_difference` while a load waits: the party whose price falls most before its next start. Price slots carry `parties`, and the synthesising forecaster writes the grid charge, levies and VAT as components so the tail splits too. The month's cost by party puts capacity on the grid and export credit off the supplier; cost and savings sensors carry `by_party`. Affects D12 §5.13, D11 §5.8, D1 §5.5.
**Rejected:** naming a party only when it explains the whole difference - on spot every wait mixes two.

### D-0559 · The tariff canary is a tool and a scheduled workflow

`tools/tariff_canary.py` fetches every registered source's live endpoint with the integration's User-Agent, requires operators and an accepted tariff, and compares each company across the tiers that list it, as the household pays it. It exits 1 on any finding; `tariff-sources.yml` runs it nightly and opens one issue per day of findings. Affects D9 §5.15, D13 §5.7.
**Rejected:** comparing raw documents - sources publish on different bases.

### D-0560 · fri-nettleie as one archive, held in memory for one flow

`fri_nettleie.py` downloads the repository tarball (405 kB) and reads `tariffer/*.yml` and Elhub's `grid_owners.json` member by member without extracting, skipping `tariffer/old/`. The YAML is parsed in the executor once per `Http`, so a flow's list, copy and confirmation share one download. Every download is released once the copy is taken, a renewal returns or the flow ends; nothing reaches disk (D13 §5.2 rule 6). NVE's county list for the month gives each company its zones. Affects D13 §5.2, §5.4, §11.
**Rejected:** caching the archive in `.storage` - downloaded data doesn't outlive its use, and the copy in `entry.data` already survives an outage.

### D-0561 · A yearly step fee per month, to 1/100 øre

fri-nettleie prices steps per year; the copy stores them per month, rounded to 0.0001 NOK, since `Decimal(1214)/12` doesn't terminate and the copy read back must equal the copy written (D13 §19 4).
**Rejected:** storing the yearly figure - every other source gives monthly steps.

### D-0562 · What fri-nettleie leaves out is asked once; the site's fuse never

An unknown method is asked as a choice of four (`TRE_DØGNMAX_MND` pre-selected), a missing threshold rule as yes/no, a stale file as "these match my latest bill". An answered gap isn't asked again. `OV_TREFASE`'s fee is chosen by the main fuse the electrical step already holds; only a site without one is asked. Affects D13 §5.6.
**Rejected:** sending such companies to the template - asks more than the one missing fact.

### D-0563 · A company across tax zones asks the county

Where NVE lists a company in counties of several zones, step 1b offers those counties and stores the chosen one's zone; a postcode that settled it skips the step. NVE's county names are trimmed. Households know their county, not zone names. Affects D13 §6.
**Rejected:** asking the postcode again - a household that skipped it chose not to give it (O17).

### D-0564 · Norway's cross-checks: fri-nettleie's contract only, for now

The canary checks every company in the archive gives a copy the model accepts. D13 §5.4's cross-checks aren't built: Strømpriseridag sources its data from fri-nettleie, so comparing them checks fri-nettleie against itself; Tensio's price page carries content signals in its `robots.txt` that restrict automated use, so no adapter reads it; Digin (Elvia, Glitre) needs API keys as CI secrets that don't exist yet, and without them no fixture can be captured (§5.2 rule 4). Affects D13 §5.4, §17.
**Rejected:** building the Strømpriseridag comparison - it would flag lag as error every time fri-nettleie publishes first.

### D-0565 · A cabin in Nord-Norge is exempt like a home

Merverdiavgiftsforskriften § 6-6-1 a) counts holiday homes and cabins as household use, so the Nord-Norge exemption (mval. § 6-6) covers a cabin exactly as a home. Affects D13 §9.
**Rejected:** a cabin zone - the regulation doesn't separate them.

### D-0566 · A test sees only the tariff sources it registers

An autouse fixture takes every shipped adapter out of the registry per test; a test that wants fri-nettleie registers it and serves the captured archive (`tests/builders/tariff_sources.py`). Otherwise every Norwegian flow test would reach GitHub with sockets closed. Affects D9 §5.15.
**Rejected:** serving every adapter's fixtures to every test - 73 real companies bury the named fakes.

### D-0567 · Eltariff: which tariffs a household can have, and where a version starts

Household tariffs are `consumption` tariffs whose name names a fuse up to 63 A or a flat, not high voltage, with no reactive-power charge. A version starts wherever a non-tax component's validity does; a cut where nothing paid changes is merged; the copy ends where energy prices stop being published, so renewal fetches the rest in time. Prices are `priceExVat`; `reference: "tax"` components are the SE module's; two priced power components are refused until multiple peaks exist. Validity to 2100 or later is open-ended. The standard has no customer-type field. Affects D13 §5.11.
**Rejected:** offering every consumption tariff - E.ON lists 38, eight of them high voltage.

### D-0568 · A Swedish household is asked whether it pays the northern energy tax

Every Swedish company from Eltariff or Ei's file lists both SE tax zones, so step 1b asks once: the national rate or the reduced northern one. Neither source places customers by municipality, and Sweden has no postcode directory in v1. Affects D13 §5.3, §9.
**Rejected:** asking only for companies known to serve the north - nothing published says which.

### D-0569 · Ei's household file is a T6 source

`ei_household` reads Ei's `Hushållskunder.xlsx` (link read from Ei's page each time, parsed with the standard library) as a document source, below every API (INV-75): per company and network area and customer group, authority and fixed fees per year, one or two energy rates, excl. VAT. One version per year from last year; the power fee is asked; two rates ask the first one's hours. D13 §5.5 names this file as Sweden's source for companies without Eltariff, which is O15's sign-off for this adapter. Affects D13 §5.1, §5.5, §5.11.
**Rejected:** leaving 130 companies on the template - the file is published and the adapter is one registration to remove.

### D-0570 · A grid charge on a share of spot (D13 §18 G22)

`EnergyVersion.spot_share`: the grid energy charge adds that share of the slot's spot price excl. VAT, taxed with the rest of the grid charge. Kraftringen and Skånska Energi charge 5 % of spot, E.ON's capacity tariffs 1.6 %. Affects D13 §18.2, D1 §5.3.
**Rejected:** folding it into a fixed rate at the mean spot - the share is what makes an expensive hour dearer on the grid too.

### D-0571 · Denmark: elpris.dk for the directory and the tariff in force, Datahub for the next season

`elpris_dk` lists the 34 grid areas (postcode → area), builds the area's tariff hour by hour, adds Energinet's system and transmission tariffs and subscription, and appends each later season Datahub has registered (`DatahubPricelist`); Datahub down leaves the season in force. elpris.dk shows only the tariff in force, and a copy without the next season would price winter at summer rates until renewal. Affects D13 §5.3, §5.9, §5.11, §19 3.
**Rejected:** Datahub alone - no postcode directory and no subscriptions.

### D-0572 · Elafgift in the DK module; Energinet's tariffs in the copy

The DK module carries elafgift with its dated rates (skat.dk; elpris.dk agrees) and its announced end. Energinet's tariffs and subscription are company prices and come with the copy (INV-70). Affects D13 §9.
**Rejected:** fetching elafgift - a fetched levy changes silently.

### D-0573 · Several peak charges by composition; nth, per day, kVA

G4: a spec with more than one `PeakTariff` gets a `Combined` evaluator, one `Evaluator` per charge over the spec narrowed to it, each with its own history. The bill is the sum; ceiling, target and slack are the lowest; eligible windows the union at the heaviest weight; level and period the first charge's; the others' histories ride in the first's store section. The rule file carries them as `peaks`. G5: `per_period = "nth"`. G9: `price_period_unit = "day"`. G10: `unit = "kva"` divides by `power_factor`. Helen's rule (the month's third-highest hour, night at 80 %) is checked on a hand-computed month. Affects D2 §3, §4, §5.2, §5.3, §9.
**Rejected:** a tuple of charges inside `Evaluator` - every method branches, and the one-charge case pays.

### D-0574 · A bundled US rate or Australian retail plan is the grid party whole

URDB rates and CDR plans price supply and delivery together; the copy holds them whole as the grid party, excl. (the AU module adds GST). The household's supplier step adds only its own extras. Affects D13 §3, §5.11.
**Rejected:** inventing a split - neither document says whose each charge is.

### D-0575 · Wallonia and Brussels from the regulators' comparators

`cwape` covers CompaCWaPE and BruSim: the postcode's entries, each entry's grid company from one single-rate simulation (the same request the pages send, supplier offers dropped), and a copy from one simulation per meter type, rates as each line's yearly amount over its kWh, excl. VAT. Grid and transmission lines are included; excise and the energy contribution wait for the BE module's levies. BruSim prices a connection-power segment picked from the site's fuse. Affects D13 §5.9, §5.11, §9.
**Rejected:** the price tables directly - they need a supplier login.

### D-0576 · A source's list keyed by the postcode, and products on demand

`TariffSource.operators(http, postcode=None)`: URDB (ZIP) and the CWaPE platform list by postcode, passed by the flow and taken from `entry.data` on renewal; the US and BE modules name their postcode directory (O17). A source may define `products(http, operator, postcode)`, asked once an operator without products is chosen (CDR brands). `Operator.source` names the adapter; `Http.post` sends JSON under the same conduct. Affects D13 §5.1, §5.3, §6.
**Rejected:** a directory step per country - the postcode step already asks it.

### D-0577 · VREG's sheet as a document source; one workbook reader

`vreg_xlsx` reads the year's distribution-tariff workbook linked from VREG's tariff page, overview sheet only: per area and meter, rows excl. VAT. The digital meter's capacity rule is the regulator's (15-minute windows, the month's highest, a 12-month mean, `min_kw` 2.5); the analogue meter a fixed term. O15 names VREG's XLSX as a candidate. `core/tariffs/sources/xlsx.py` reads a sheet with the standard library for both workbook adapters, rounding number cells to nine decimals to return what was typed. Fluvius areas are listed by name. Affects D13 §5.1, §5.11.
**Rejected:** a private copy of Ei's reader - the same parsing and float noise fixed twice.

### D-0578 · URDB: a version per season, the window asked, the importer moved

`openei_urdb` (`DEMO_KEY`: 50 requests a day, a flow is two) lists the utilities serving a ZIP with their rates in force; a rate's demand becomes a version per season over the next twelve months (all-hours and time-of-use charges; several in a month are several peak charges, G4); block demand is refused; a missing `demandwindow` is asked (60 pre-selected); the fixed charge is the fee (G21). The 12 × 24 energy importer moves from `core/pricing/modifiers/tou_urdb.py` into the adapter, since tariff sources mustn't import pricing. Affects D1 §3, §9 7; D13 §5.11, §12.
**Rejected:** keeping the paste importer - every approved rate is in URDB, and a pasted document skips the checks.

### D-0579 · CDR: the demand measurement always asked, 30-minute windows

`cdr_energy` lists the register's 84 brands; a brand's residential plans for the postcode come once chosen (D-0576), and tariff periods become versions from the season in force. A demand charge's measurement (day, month, last 12 months) is always asked with the description's reading pre-selected, because the structured field contradicts the description on the captured plan. A charge named in cents asks the per-kW-per-day price; a kVA charge asks the power factor (0.9). The window is 30 minutes, the NEM's metering interval. Affects D13 §5.6, §5.11, §19 9.
**Rejected:** trusting `measurementPeriod` - bills a twelve-month maximum as a daily one.

### D-0600 · No company's prices ship; an entry still on one is fetched

The twelve company files (Tensio TS and TN, Elvia, eight Fluvius areas, Ellevio) move to `tests/fixtures/presets/`; the integration ships national rules and templates only (INV-70). An entry still naming one migrates offline to a copy naming where it's fetched (`storage.FETCHED_FILES`), `assumed`, with `renew_at` set so the renewal fetches an hour after start at the earliest (INV-73). Ellevio has no capacity charge and migrates to the no-capacity copy. `tools/backtest.py --tariff <file>` replaces `--preset`. The price: a site that skips the release carrying the migration runs up to an hour without capacity control after its first start. Affects D13 §10, §12; D2 §3; D9 §3, §5.4.
**Rejected:** keeping the files a release longer - handled by merge order instead.

### D-0601 · The tests pick a grid company from a fixture source

`tests/builders/presets.py` reads the company files kept as fixtures, and the evaluator's goldens on real tables stay. Flow and e2e tests pick "Tensio TS" or "Fluvius Imewo" from fixture sources registered under fri-nettleie's and VREG's keys, the way a household picks from a source's list. Affects D9 §3, §5.15.
**Rejected:** letting the loader search fixtures in tests - tests a path no household can take.

### D-0602 · ElCom: the category's flat average, the local charges on the grid line

`elcom` (CH) reads the price site's GraphQL: a postcode's municipality by the site's search, else all of them; per municipality, category (H4 first) and year, grid usage and charges become one flat rate and metering a yearly fee; next year joins once published. `aidfee` (the federal Netzzuschlag) is the CH module's levy; municipal and cantonal charges stay on the grid line since they differ by municipality. ElCom publishes no high/low split. Affects D13 §5.3, §5.10, §5.11.
**Rejected:** asking high/low from the bill - makes the household type what ElCom doesn't publish.

### D-0603 · RO: the grid lines from ANRE's comparator, the state's lines in the module

`anre` reads eight distribution zones and one zone's low-voltage household offers; distribution, transport and system service (the same in every offer) are the grid copy excl. VAT. Cogeneration, green certificates and excise are the RO module's levies; the adapter logs when the comparator has moved on. INV-72 names each line's party. Affects D13 §9.1, §5.9.
**Rejected:** carrying the three levies in the copy - they'd be billed as the grid company's.

### D-0604 · ZSDIS: every HDO code a switched window; the two rates asked

`zsdis` (SK) reads the switching-times page's 32 codes into `SwitchedWindow`s (G14); the meter's own code gives the grid's low-rate windows. High and low prices are asked from the bill, and a receiver on winter time all year is asked and read on standard time (G11). The page is the only open statement of the windows. Affects D13 §5.10, D4 §5.16.
**Rejected:** only the meter's code - a water heater's relay often has its own.

### D-0605 · ČEZ's HDO service asks a captcha: excluded

ČEZ Distribuce's switching-times service now answers a request without a verification code with an error. D13 §5.2 ends an adapter at a captcha, so CZ has no HDO source; a CZ household types its windows (G15's `unknown` meanwhile). Affects D13 §5.10.
**Rejected:** solving the captcha - the working-around §5.2 forbids.

### D-0606 · Tauron: the card as published, incl. VAT; OZE and KOG in the PL module

`tauron` reads the calculator page's rate card, which is incl. VAT (its own 23 % row), so the copy's basis is VAT and the OZE and KOG levies, whose net amounts the PL module holds. G11, G12 and G12w are offered, their hours checked against the page's own sentences on every fetch; G13's hours are an image and G14dynamic follows PSE's day-ahead (G19), so neither is offered. Affects D13 §5.10, §18 G19.
**Rejected:** G13 with the commonly quoted hours - the page doesn't state them.

### D-0607 · A load's own tariff: its curve per key, without the house's grid add-ons

`GridTariff.per_load` (`LoadTariff`) and `switched` (`SwitchedWindow`) join the copy. `party.chain_for_load` swaps the grid energy for the load's and drops the household's grid-party add-ons; the runtime builds `Curves.per_load` only for keys a configured load names; `plan_all` plans a bound load on its curve, falling back to the house's. The load review offers `grid_tariff` and `switched` only where the copy has either. Affects D1 §5.3; D4 §5.16; D5 §4.
**Rejected:** a curve per load always - a full composition per load per price tick.

### D-0608 · A switched load: headroom closed, the plan clamped, the grant hard-capped

Outside its windows a switched load's headroom is 0, its plan's slots 0 (`grid_switched`), and D6's `GridSwitched` (a hard scope, sorted first) grants 0 at every stage, a comfort floor included. An unknown code has no window (G15): the load is `delegated`, never written or planned, and reserves its nameplate only while it draws. The grid's relay is open whatever is granted. Affects D4 §5.16, D5 §5.1, D6 §2, §5.2.
**Rejected:** a preference scope so a violated floor could try - the relay is physical.

### D-0609 · The power tier on a flat day; LU's surcharge billed at window close

On a flat day with a priced limit, `deadline_fill` fills by (price, index, tier), so the night fills under the limit first (D5 §9 23). D11 prices each world's excess at window close from `priced_limit_now` and the window's own kWh, actual and counterfactual, inside the capacity line. The counterfactual book keeps days, not windows, so D2's bill can't see the shadow's windows. Affects D2 §5.8, D5 §5.1, D11 §5.4.
**Rejected:** the surcharge in D2's bill - only the actual world's windows exist there.

### D-0610 · PT's VAT band on the month to date

A country module may carry a `VatBand` (PT: 6 % on the first 200 kWh, 300 for a large household, up to 6.9 kVA; the islands 4 %), read by `StateVat` on `ctx.mtd_kwh_at`, the boundary belonging to the full rate. The runtime's month to date was still 0 (as for Norgespris's cap and `cumulative_tier`) until D-0612. Affects D1 §5.4, D13 §9.1.
**Rejected:** feeding the month to date here - it touches every modifier that reads it.

### D-0611 · Templates for IT, PT, FR and IE; AT, SI and DE Modul 3 wait

Rule templates `it/contracted` (kW, +10 % tolerance per ARERA, G20), `pt/contracted` and `fr/kva` (kVA, trip) and `ie/nopeak`, each the module's `rule_template`. Not built: AT's SNE-VO tables (document source, O15), SI's five blocks (a union of season × day type × hours per block that one `PeriodLimit` per block can't express without asking a kW several times, G3), DE Modul 3 (a commercial API, O19). Affects D2 §9 41; D13 §5.10, §17, §18.
**Rejected:** SI with repeated limits - asking one agreed power three times (INV-74).

### D-0612 · D1's month to date comes from D3's closed windows

`WindowState.month_key` and `month_kwh` sum the local month's closed windows; `WindowMeter.month_to_date_kwh(now)` adds the open one; `pricing.context.month_to_date` projects at the month's mean rate and restarts a later month from zero. The runtime's price contexts read it, so Norgespris's cap, `cumulative_tier` and PT's band see the month. No meter reads 0; year to date stays 0. Affects D1 §2, D3 §4.
**Rejected:** the ledger's `import_kwh` - accounting feeding the planner, against INV-68's spirit.

### D-0613 · A load on its own meter is billed on its own curve

`CloseCtx.load_curves`: the load's cost, counterfactual and re-pricing read its own curve, and the site's line moves that load's kWh from the house's price to its own, by party too. D4 §5.16 promises D11 bills a tariffed load on its own meter (G13). Affects D11 §5.1, §5.8.
**Rejected:** billing everything at the house's price - a §14a heat pump's cheap kWh would read as dear.

### D-0614 · Eltariff's two power prices are two peak charges

A version with several power prices (night and day) becomes several `PeakTariff`s on their own hours, billed side by side by the `Combined` evaluator (G4). Hours that differ by day type within one price are still refused. Affects D13 §5.11, §18 G4.
**Rejected:** refusing until asked - households have the tariff today.

### D-0615 · A site with no capacity component publishes no ceiling, not an infinite one

The ceiling sensor is unknown when the ceiling is infinite, and `metric` when the level has none; a price-only or no-peak site failed to add both (`inf` into a numeric sensor, `round(None)`). Affects D8 §5.5.
**Rejected:** publishing 0 - reads as a closed gate.

### D-0616 · The tariff-sources series joins main: the settled ledger and the new cards

Merging the tariff-sources work with main: D11 is the settled-reference ledger, with the series' additions on top (savings by party as each slot settles, energy cost by party per slot, a priced limit's surcharge in both worlds, a tariffed load on its own curve). The frontend is the second card set as delivered: the price strip's party stack and source credit aren't drawn yet, though the sensors still carry `parties` and `credit`. The price card's energy part uses the state stage's VAT where the chain is by party. Affects D11 §5.4, §5.8, §5.9; D12 §5.13.
**Rejected:** re-applying the party stack to the new cards in the merge - a card change belongs in its own step.

### D-0620 · The dashboard layout is a response action

`powerplan.get_dashboard` (`SupportsResponse.ONLY`: `site`, `language`, `hidden_views`, `hidden_cards`) answers `async_dashboard_config`, the former websocket command's body. `strategy.ts` sends HA's `call_service` with `return_response: true`. A response action is HA's channel for "answer me something", as `dump_state` already uses (PLAN §7 dec. 42), and the strategy's `entry_id` option keeps its name. Affects D12 §5.16, §9 4; D8 §5.7, §9 38.
**Rejected:** keeping the private websocket command - a private channel where HA offers a public one.

### D-0621 · The spot rides on `price_forecast`; the card computes `tomorrow_available`

`_slots` adds `spot` (ex VAT, from the reference curve where a fixed price exists), the row adds `fixed_price`, and `fixed_price_savings` adds `today_kwh`. `price-card.ts` builds its `Spot` object from these states, so its rendering is untouched; `tomorrow_available` is a known slot from the next local midnight. A state pushes on change where the card polled every 15 minutes. Affects D12 §5.16, §9 27; D8 §5.5.
**Rejected:** the separate spot command - a snapshot of the same data.

### D-0622 · The frontend module is a Lovelace resource the integration keeps

`dashboard/resource.py` reads `hass.data[LOVELACE_DATA]`; in storage mode it keeps exactly one `module` item for `/powerplan_frontend/powerplan.js` with the current `?v=`, using the collection's own create, update and delete, otherwise `add_extra_js_url`. `lovelace` joins `after_dependencies`; the item is removed with the last entry. HA 2026.9's dashboard panel loads resources itself, so the strategy's module is fetched by the page waiting for it. It's one row the integration owns and the household can see and delete, not a dashboard created unasked. Affects D12 §5.5, §5.16, §8, §9 26.
**Rejected:** `add_extra_js_url` alone - the page can ask for the strategy before the module arrives.

### D-0623 · The stale-bundle check reads `lovelace/resources`

`version-check.ts` reads the resource item's `v` from `lovelace/resources` and compares with the loader's own; with no item (YAML resources) it does nothing. The version command goes; `module_key` stays to write the key into the resource. Affects D12 §5.15, §5.16.
**Rejected:** a command that repeats a key the store already holds.

### D-0617 · A tariff download is read to its end

`Http` read a body with `StreamReader.read(MAX_BYTES + 1)`, which returns whatever has arrived: fri-nettleie's 405 kB archive came back as 4.8 kB and the flow failed with "Unknown error". The body is now read chunk by chunk to its end, stopping one chunk past 5 MB; a cut archive is a `QualityError`, so the ladder falls to the next tier. The rule schema is read once at import, not in the event loop. Tests served captured documents and never met a real stream. Affects D13 §5.2, §11.
**Rejected:** `read()` and a size check after - buffers any size before the cap applies.

### D-0618 · The grid summary no longer says what the plan does with each rule

The tariff summary drops "Your grid company decides this, and PowerPlan plans by it:" and its lines for every tariff, keeping the rules, the source and the credit. The step is about what the grid company charges. Affects D13 §6, O10.
**Rejected:** keeping a line for the capacity step - the plan's behaviour belongs in the docs.

### D-0625 · The loader's key moves to `__ppKey`; `__ppBundle` is the shim's

`bundle.ts` writes the loader's `?v=` to `globalThis.__ppKey`, and `__PP_BUNDLE__` reads it there. `__ppBundle` is left to `strategy-shim.ts`, which detects an older bundle loaded first by comparing it with its own `BUNDLE`; with the key on `__ppBundle`, the new bundle overwrote the old value first and the toast never showed. Affects D12 §5.17.
**Rejected:** the shim reading a "previous" global - the shim stays as delivered.

### D-0626 · The third card set's styling, without its backend

The card files are copied as delivered, except `price-card.ts`, which keeps `spotFromStates` and `button.press`. Its backend pieces (a month-cost sensor, a compatibility command for the deleted websocket, D12 §9 25) aren't taken. Its Now-view layout is applied to `layout.py` as layout only (no peak-warning tile, one heading badge, no price track); its live-card patches go into the window, timeline and period-summary cards. The step fee comes from `level`. Unused `card_*` keys go. Affects D12 §5.17, §5.1, §9 29.
**Rejected:** leaving `layout.py` alone - the removed tiles are laid out there.

### D-0627 · The peak warning counts only what D6 won't hold under the ceiling

A coming window's `expected_kwh` is the uncontrolled term (D10's baseline, else the EMA) plus, for each no-vote demand, `max_w × window_h` bounded by `required_kwh`. Plans don't count: D6 caps every plan-driven grant at the ceiling (INV-1), and only violated comfort floors and running cycles pass it. A warning of 11 kWh against 10 turned out to be 6.3 kWh of an EV plan with no car connected plus the tank's plan. `urgent` plans now count as no-vote. The roast scenario's lead falls to the EMA's (about 33 min to crossing, first seen 3 min before the window); D9 §5.3's ≥ 20 min stands for a confident baseline. A shorter EMA constant would warn on 5-minute bursts. Affects D7 §5.4, §9 12, §11.
**Rejected:** keeping plans in the sum and fixing the planner - plans fill to within 0.95·ε of the warning threshold, so any baseline error trips it nightly.

### D-0628 · A kept plan that no longer fits the room above it is replaced

`plan_all` keeps a previous plan past the hysteresis (INV-32) only while every slot ahead reserves no more than the headroom the higher-priority loads left now, plus ε_w; otherwise the fresh plan is adopted. An EV plan kept from before the water heater re-planned twice left an hour at 10.7 kWh against a 10 kWh target. INV-32 protects against price noise, not against a plan overlapping room its betters took. Affects HLD INV-32; D5 §5.1, §5.9, §9 26, §11.
**Rejected:** clipping the kept plan to today's room - the clipped energy is lost and the next cycle replans anyway.

### D-0629 · A slot reserves what it plans to draw, not the cap it publishes

A planned slot takes from the loads below it its planned mean draw, `(kwh + hold_kwh) / hours`, bounded by `envelope_w`; an envelope of 0 reserves nothing. For `deadline_fill` it's the same number; for `heat_capacitor`'s bank and hold rows it's what the store will actually take. Three banked floors reserved 2.1 kW all night while planning almost nothing, and the EV lost that room. `urgent` is unchanged (D-0255). Affects D5 §5.1, §9 27, §11.
**Rejected:** reserving the cap - the ceiling is energy per window, and D6 serves priority live anyway.

### D-0630 · A load waiting for its run says the run

While `plan_status` is `waiting` or `idle` with a run ahead, `reason`, `reason_key` and `reason_params` describe it: `planned`, `{time, kwh}`, "Planned from 22:00 · 7.6 kWh"; otherwise they stay the gate's `ActionReason`. `planned` is `plan_status`'s own key beside the closed set. The appliances card adds "from 22:00". A tank read "Waiting for cheap power" with "Reason: Already set", which sounded like waiting to cool to its resting setpoint. Affects D8 §5.16, §9 39; D12 §5.6, §9 30.
**Rejected:** a new `planned` state - `waiting` already means that, and the state set is what automations hold.

### D-0631 · A price entity source is built by its row's kind, with its stored options

`runtime._price_source` builds `ActionSource` or `EntitySource` by `formats.entry(format).kind`, each from `formats.build(format, options)`, which decodes stored options through the modifiers' `decode_options` and drops names the schema doesn't have. `EntitySource` takes an optional `second_entity_id` for Octopus's tomorrow entity. `formats.build(key)` without options raised for five of fourteen keys, and action rows would have been wrapped as entities. Affects D1 §2, §6.
**Rejected:** a second `EntitySource` for tomorrow - both would share the class key, and the flow would store two rows for one choice.

### D-0632 · The format select has no default, and an unrecognised entity is refused

The format select is `vol.Optional` with no default on first setup; empty means the detected format. An entity no row claims, with nothing chosen, is refused with `price_format_unknown`. The selector covers `sensor` and `event`, unfiltered by platform. The old default (the first key, `amber`) was submitted as the answer on every setup. Affects D1 §6.
**Rejected:** filtering by platform with a "something else" path - two paths for one question.

### D-0633 · One format-options step for every row; derived answers never asked

`prices_format` is one step rendered from the row's schema, shown only when the row fits any entity, publishes tomorrow on a second entity, or has a required field with no default and nothing derived. `formats.derived(key, EntityFacts)` answers `config_entry`, `currency` (from the unit) and a row's own `from_entity` hook (Tibber names the home only with two price devices). Only 3 of 20 rows show the step on first setup. Affects D1 §6.
**Rejected:** a step per row - 20 steps × 3 language files for repeating fields.

### D-0634 · Six more price rows; `tibber_prices` keeps `tibber_action`'s basis

`epex_spot`, `zonneplan_one`, `frank_energie`, `cz_energy_spot_prices`, `tibber_prices` and `stromligning` are registered, each with a fixture from the integration's own source. `symbol_unit` reads `€`, `£` and `Kč` as codes and nothing else. `generic_list` gains `tomorrow_attribute`, a dotted `value_key` and `scale`. Both Tibber rows read the same `total`, so their basis stays spot alike. Affects D1 §2.
**Rejected:** reading `kr` from the site's currency - a Swedish house with a Danish sensor would be priced wrong silently.

### D-0635 · A vocabulary charger is one `VocabularyCharger`, registered from its own module

`VocabularyCharger` is a frozen dataclass saying where a charger's limit, stop/start, status and reads are (`Find`: domain, name tokens, device class, exclusions), with its status vocabulary and quirks; it implements `match`, `bind`, `provisions` and `quirks` once. `ocpp`, `wallbox`, `peblar`, `v2c` and `goecharger_api2` each register an instance. `DeviceProfile.key` becomes a read-only property. Affects D4 §5.9.
**Rejected:** one class per charger - none has a quirk that isn't data.

### D-0636 · OCPP pauses by its station-wide limit; charge control isn't bound

`ocpp` binds *Maximum Current* (a `ChargePointMaxProfile`, 0 A up) with `limit_pauses`: 0 A holds the car, and it holds with or without a transaction, so nothing is re-sent on plug-in. *Charge Control* sends `RemoteStopTransaction`, ending the session as `Finishing`, which would latch every pause as done (as with Zaptec, D-0372). The per-transaction limit is the separate *Session Current Limit*. Affects D4 §5.9, §10.
**Rejected:** binding *Charge Control* as ENABLE - some chargers need re-authorising to start again.

### D-0637 · ENABLE in the charger's own spelling; V2C's status is its plug, go-e's the API code

`EnableSpec(find, on, off)`: the first value of each is written and every value reads back; an unknown value reads unavailable. V2C's *Pause session* is on while paused (`on=("off",)`); go-e's stop/start is a select (`on=("2", "0")`, `off=("1",)`). V2C publishes no charge-point state, so its status is the plug sensor; go-e's *Car state* is translated into HA's language, so the status is *Car state [CODE]*, which ships disabled. Affects D4 §5.9.
**Rejected:** the mapping in `Quirks.option_names` - changes `BoundDevice` for every profile to serve two.

### D-0638 · KEBA waits for a device

No `keba` profile in v1: the core integration has no config flow and none of its entities sets `device_info`, so the load flow, keyed by device, can't pick it (D4 §5.9's own condition). Affects D4 §5.9, §9 16, §10.
**Rejected:** offering it through the entity picker - the subentry is keyed by a device.

### D-0639 · The day-type add-on names its announcing entity; its events persist beside the slots

`day_type` gains `entity` and `day_offset` (Advanced) fields; the runtime builds an `EntityEventSource` from them (`runtime.EVENT_MODIFIERS`). Rates are named as the sensor writes the type, case-folded, so no mapping is asked. The event store persists as the `prices` section's `events`. D1 §6's day-type entity had no field anywhere, so Tempo priced only its fallback. Affects D1 §6, §7; D7 §5.5.
**Rejected:** a separate event-sources step - the DSO-limit UI is v1.x, and a day type without its entity can't be configured.

### D-0640 · The tick sees events in force; a changed announcement reprices and plans

`Inputs.events` is `EventStore.in_force(now)` in the tick and `all()` in the plan; the planning cycle prunes events ended over an hour ago. A state change on an event entity polls every source, and a changed store rebuilds the curves and plans. Each source is polled once at start. Curves are built after a fetch, so a colour announced between fetches would otherwise wait for the next one. Affects D7 §5.2, §5.3, §5.5, §9 18.
**Rejected:** rebuilding curves on every plan - four full compositions an hour for an input that changes daily.

### D-0641 · Three market houses on one builder; Tempo colours announced into the runner's event store

`fi_linear`, `es_contracted` and `fr_tempo` are `nordic_detached`'s twelve loads under another market, built by one `_market_house`. `fr_tempo` prices energy as a `day_type` add-on over a flat source, and `TempoSim` announces each day's colour at 10:40 the day before; the runner builds an `EventStore` from the announcer and passes what's in force as `Inputs.events`. ES and FR are TT 400 V three phase, since a single-phase supply can't carry the three-phase loads. The FI and SRP shapes are benchmark-only rules. Affects D9 §5.9.
**Rejected:** Tempo prices as a known price regime - tests nothing about acting on an announcement.

### D-0642 · `us_demand` waits for the heat pump's cooling mode

Three market houses ship; `us_demand` and its cooling scenario wait for the cooling mode D4 §5.14 specifies (D4 §9 34). The scenario's first assertion is pre-cooling before 15:00, and `HeatPumpType.comfort` and its store only heat so far; D9 §5.9 specifies the house's climate, tariff and season. A baseline built now would move as soon as cooling lands. Affects D4 §5.14, §9 34; D9 §5.9.
**Rejected:** `us_demand` with only its demand half - a Phoenix summer house whose largest load can't run.

### D-0643 · Cooling is a setting of the appliance, not the unit's live `hvac_mode`

A heat pump whose match offers `cool` is asked *Plan for cooling*; the answer sets `params.direction`, and the profile, room store and setpoint kind are built for it. The household switches it at the season's change. The store, profile and kind are built once from the subentry, so a live flip would leave a heating store under a cooling profile, and the unit's mode also changes for an open window or *dry*. Affects D4 §5.14, §9 34.
**Rejected:** reading `hvac_mode` every tick and rebuilding - drops the plan's store mid-plan.

### D-0644 · `us_demand` holds its air conditioning; pre-cooling is cooling before the window and coasting through it

The heat pump cools at 24 °C with `follow_presence` off; the scenario runs two August weekdays. Pre-cooling: the unit's energy 10:00-14:00 exceeds 14:00-20:00 each day. Demand: every on-peak 30-minute window stays at or under 5 kW. `WeatherSim` takes its climate normals as fields. With the away setback the planner doesn't know when the household returns, so can't cool ahead; with the target held it cools until 14:00 and lets the room coast 24.2 → 24.9 °C through the peak. Affects D9 §5.3, §5.9.
**Rejected:** teaching `heat_capacitor` to bank ahead of a capacity window - a D5 change the demand doesn't need yet; noted as a gap.

### D-0645 · Flow text within budget, the detail on the page; aborts link to troubleshooting

Every non-review step of the home, circuit, group and room flows ends with `[How this works]({docs})`, supplied by `doclinks.step_placeholders`. Step texts are cut to 30 words and 2 sentences, field texts to 15 words and one sentence, in both languages; the rest is on `setup.md` or `circuits-groups-rooms.md`. Aborts whose fix is elsewhere link `troubleshooting.md#<reason>`. D14 decision 7 and §5.4. Affects D14 §5.4.
**Rejected:** raising the budgets - HA renders a field's description under the field, and the budgets come from the review's screenshots.

### D-0646 · The appliance flow links its type's page; a type page lists its own questions

`LoadSubentryFlow.async_show_form` merges `doclinks.step_placeholders("load", step_id, self._type)`: the index before a type is chosen, the type's page after. `tools/docs.py` gains `questions:<type>`, that type's questionnaire in order. The five-word budget for option labels skips the word-bank selectors read through `Text.word`, which are sentence parts, not choices. Over-long dropdown labels are shortened, the detail moved to `setup.md`. The eight types share steps, so a per-step table would list every type's questions on each page. Affects D14 §5.4, §5.6, §9 4.
**Rejected:** a step id per type - multiplies steps and translations by eight for a table.

### D-0647 · Troubleshooting holds the aborts and symptoms; actions carry `{docs}`

`troubleshooting.md` has a section per repair, per linked abort, for three symptoms (`dashboard`, `not_running`, `over_target`) and for diagnostics. `services.py` registers every action with `description_placeholders={"docs": doc_url("actions", name)}`, and descriptions end with the link; the generated `actions` table strips it. Every `docs-*` quality-scale rule is done or exempt. HA 2026.9's `async_register` takes `description_placeholders`, so the action editor shows the link. Affects D14 §3.1, §5.4.
**Rejected:** no link on actions - the action editor is where an automation author meets them first.
