# How the strategies were designed (October 2026)

This review explains how the strategies that steer by price and capacity came to be what they are in v0.0.11. It names the evidence behind each choice and the decision that records it, so every planner and every steering number can be traced back to its source. The LLDs say *what* is built ([D5](../lld/D5-strategies.md), [D4](../lld/D4-loads.md), [D9 §5.17](../lld/D9-testing.md)). [DECISIONS](../DECISIONS.md) D-0766 … D-0839 say *why*, one choice at a time. This page is the thread that runs through them, in the order the work happened.

## 1. The goal and the constraints

**The goal.** Save the household as much money as possible, on the capacity step and on the energy price, with **no comfort loss anyone notices**. Comfort is judged against the comfort *target*, not only the floor that `comfort_violation_min` counts: a room that sits a degree under its target all evening has lost comfort even when no alarm fires.

**The constraints**, from [HLD §1](../HLD.md) and [PLAN §2](../PLAN.md):

| constraint | what it ruled out or in |
|---|---|
| Runs inside Home Assistant, on Raspberry Pi-class hardware, Python 3.14, Alpine/musl wheels | a library or solver must install from HA OS wheels; numpy is already in every HA install |
| Planning budget: 500 ms p95 for 20 loads (D9 §5.1) | plain-Python search was too slow; the plan moved to an executor (D-0815) |
| Loads planned one at a time in priority order, no global solver (INV-33) | per-load optimisation, not a whole-house MILP |
| Every plan explains itself (HLD §1.1, "Observable") | the planners' routes are read back into reasons ("bank to 25.6 °C before the price rises") |
| Easy to use: sane defaults, no new setup questions, an override under *Advanced* | a number is derived from an answer the flow already asks or from the recorder fits, never asked anew |
| Physical values are measured from the recorder (D10 §5.6) | the evidence sets only what sensors can't measure; for the rest it sets the bounds that reject a bad fit |
| One maintainer | each change one reviewable work package, measured by the reference benchmark and the catalogue |

## 2. How the work went

| step | what was done | output |
|---|---|---|
| 1. Libraries | surveyed Python libraries that schedule loads by price (§4.1) | none installs in HA and covers the use cases |
| 2. Algorithms | exact and near-exact methods per use case, and what solvers ship on HA OS (§4.2) | dynamic programming over a store's level for heating and the battery; the exact greedies kept |
| 3. Measurement | ran every shipped strategy closed loop against the exact optimum of its own load on 13 synthetic price days (§3) | the gaps, and the mechanism behind each |
| 4. The paused `heat-moves` branch | measured on the same bench | its goal kept, its search replaced (§5.2) |
| 5. Evidence for the numbers | standards and studies for every number sensors can't give (§6) | defaults, bounds and the owner's choices |
| 6. Design pass | HLD, D4, D5, D6, D8, D9, D10, PLAN §3.0t, D-0766 … D-0779 | eight work packages, BP.1 … BP.8 |
| 7. Implementation | BP.1 … BP.8, each measured by the catalogue, the scenarios and the smoke benchmark | v0.0.11; follow-ups D-0780 … D-0839 |

## 3. What was measured before the redesign

Closed loop, re-planned every 15 minutes, against the exact optimum of the same one-node model on HiGHS, 13 price days of 48 h each: flat, flat ± 0.1 %, Norgespris day/night, three-step time of use, winter double peak, solar dip below zero, negative night, volatile, one spike, rising, falling, a 15-minute sawtooth, and the NO3 test day. "Share" is the part of the gap between hands-off and the optimum a strategy closed. The bench became the catalogue ([D9 §5.17](../lld/D9-testing.md), D-0770).

| strategy | share before | worst day | mechanism |
|---|---|---|---|
| `deadline_fill` | 100 % | flat today, deadline in a structured night: +67 % | flat judged on today only (D-0771) |
| `cheapest_hours` | 100 % first plan | closed loop ran 16–96 quarters for 16 | forgot the hours already run (D-0810) |
| `run_once` | 98–100 % | 1.8 % | ranked by sampled segments, placed by overlap (D-0771) |
| block fill | best within its rule | - | kept |
| `hot_water` | 88–101 % | - | kept (D-0779) |
| `heat_capacitor`, 1–2 K bank | 27–55 % | 50 % dearer than the thermostat | a started stretch forgotten at replan, the bank sized twice over, an unfit fill kept, recovery at the next slot's price (D-0767) |
| `heat_capacitor`, pause only | −3 … 16 % | 33 % dearer | the same |
| `best_save` | 3–9 % | 13–15 % dearer; a cold room fell to 18 °C | recovered in the very next slot (D-0767) |
| `arbitrage` | about half | 0 % on some days | paired the K cheapest with the K dearest slots whatever the battery's size, executed in time order (D-0768) |
| `peak_shave` | - | missed 1–8 kWh of an 8 kWh deficit on 7 of 13 days | reserve unprotected, charged after the peak; in production it never saw a deficit at all (D-0768, D-0837) |
| priority walk | 36–87 % | the EV 14 kWh short at 5 kW | mostly the heating plan over-reserving (R16) |

Fixing one heating defect alone made results worse, because the other rules were compensating for it. The lesson: the losses were in the searches, not in their numbers.

## 4. Options considered

### 4.1 Libraries

| library | fit | why not adopted |
|---|---|---|
| [EMHASS](https://github.com/davidusb-geek/emhass) | nearly every load type, LP/MILP; thermal and slab models ([thermal model docs](https://emhass.readthedocs.io/en/latest/thermal_model.html)) | needs Python < 3.13 and cvxpy, which has no musl wheel ([pyproject](https://github.com/davidusb-geek/emhass/blob/HEAD/pyproject.toml)); runs as an add-on |
| [HAEO](https://github.com/hass-energy/haeo) | an HA integration on HiGHS; proves `highspy` ships on HA OS ([manifest](https://github.com/hass-energy/haeo/blob/HEAD/custom_components/haeo/manifest.json)) | batteries, grid and solar only |
| home-energy-optimizer | the same dynamic programming, numpy only | two months old, one author, when read |
| [FlexMeasures](https://pypi.org/project/flexmeasures/), [Akkudoktor EOS](https://github.com/Akkudoktor-EOS/EOS), evcc's optimizer | mature in their niche | server stacks, Python pins, or a genetic search |
| [Predbat](https://github.com/springfall2008/batpred) | battery window search | its licence forbids reuse ([LICENSE](https://github.com/springfall2008/batpred/blob/HEAD/LICENSE)) |
| [PyPSA, linopy, Calliope, oemof](https://pypi.org/project/linopy/), [do-mpc](https://pypi.org/project/do-mpc/)/casadi | modelling frameworks | far heavier than a 192-slot problem; pandas or wheel conflicts |

Borrowed, not depended on: EMHASS's outdoor-dependent COP for a slab, and HiGHS as the catalogue's oracle.

### 4.2 Algorithms and solvers

| use case | best known method | adopted |
|---|---|---|
| energy by a deadline | sort and fill is the LP optimum (fractional knapsack) | kept, `deadline_fill` |
| minimum run length | single-unit commitment DP ([Frangioni & Gentile, via Bacci et al. 2024](https://pubsonline.informs.org/doi/10.1287/opre.2023.2435); [Guan, Pan & Zhou 2018](https://arxiv.org/abs/1608.00042); [Lee, Leung & Margot 2004](https://www.math.ucdavis.edu/~deloera/MISC/LA-BIBLIO/trunk/LeeLeungMargot.pdf)) | block fill kept: exact within its rule |
| a fixed cycle | exhaustive start search | kept, `run_once` |
| a thermal store | LP over the RC model, or DP over the level; MPC literature ([Halvgaard et al. 2012](https://independent.academia.edu/RasmusHalvgaard); [CLIMA 2022](https://proceedings.open.tudelft.nl/clima2022/article/view/154): MPC 8.3 % vs rule-based 0.8 %; [Riederer et al. 2026](https://www.tugraz.at/fileadmin/user_upload/Events/Eninnov/EnInnov2026/files/kf/662_KF_Riederer.pdf): MPC wins on flexible stores) | **DP over the level**, `levels.py` (D-0767) |
| a tank with draws | lot-sizing ([Wagelmans, van Hoesel & Kolen 1992](https://pubsonline.informs.org/doi/10.1287/opre.40.1.S145); [Ahuja & Hochbaum 2008](https://pubsonline.informs.org/doi/10.1287/opre.1070.0508)) | `hot_water` kept: 88–101 % already |
| battery arbitrage | LP, exact without binaries except at negative prices ([Prat, Lusby & Pinson 2025](https://arxiv.org/abs/2508.12999)); min-cost flow ([Cruise, Gibbens & Zachary 2014](https://arxiv.org/abs/1307.0800)) | **DP over the state of charge**, the same `levels.py` (D-0768) |
| several loads under one ceiling | joint LP/MILP; dual decomposition, valley filling ([Gan, Topcu & Low 2013](https://authors.library.caltech.edu/records/6bhnt-fy360)) | not adopted: INV-33; open as R16 |

Solvers on HA OS (Python 3.14, musllinux): [highspy](https://pypi.org/project/highspy/1.15.1/#files) and [scipy](https://pypi.org/project/scipy/1.18.1/#files) have wheels; [OR-Tools](https://pypi.org/project/ortools/9.15.6755/), [cvxpy](https://pypi.org/project/cvxpy/1.9.3/), OSQP, Clarabel and casadi do not. The design used HiGHS only as the tests' referee: most of our levers are on/off or two-step, which makes a runtime MILP slow, and a compiled wheel would be owed for every Python HA moves to. The DP handles the lever's steps, the COP per hour and stable ties directly, in 3–14 ms per bare store in numpy, about 25 ms per heating load as built (D-0804).

## 5. The strategies as designed

### 5.1 The level planner

Dynamic programming over a store's level (temperature, or the battery's stored kWh), backwards from the horizon, in the actions the lever has, then forwards from the measured level. It is exact to the grid, and the grid is laid on the thermostat's own setpoint steps (D-0820). It is time-consistent: re-planned a quarter hour later from the level reached, it finds the rest of the same route, so a started move is not forgotten. Ties go to the reference action, holding the target, so a flat day holds (INV-32). Decisions: D-0767, D-0800 … D-0809, D-0815, D-0818, D-0820.

### 5.2 `heat_capacitor` and `best_save` with a store

The goal came from the paused `heat-moves` branch, agreed with the owner on a prototype (D-0766). A store's energy moves only across a price change: it banks before a rise, coasts before a fall and buys back after, and holds comfort through a flat block. A move is valued on timing alone, so the heat saved by sitting colder earns nothing. Otherwise "sit at the bottom of the band" always wins, which is the comfort loss the goal forbids. `heat-moves` measured 44–60 % on a slab but left the band in 73 of 312 runs and took up to 2.2 s per plan, so its goal and product pieces were kept and its search replaced.

The first cut of the planner used the whole 1 K coast band for hours, and with the thermostat's swing a heat pump sat 0.63 K under target on average in January. The coast default became 0.5 K (D-0819). The heat pump returned to −0.23 K, and the floors to within 0.1 K of target. Measured after: heat_capacitor median 99.7 %, never dearer than the thermostat, never out of band; best_save at least 90 %.

### 5.3 Heat pumps

Heat is priced at the COP of its hour, × 0.85 between −5 and +3 °C for defrost. The air-to-water curve is 0.35 × Carnot at the supply the heating system needs, so a floor-heating system gets a curve 17–29 % more efficient. Pre-heating stays the household's choice, decided by the hour when on. A heat pump changes setpoint at most once an hour, because it answers in 0.5–1.5 h. Decisions: D-0773, D-0825 … D-0829. Measured after: at least 93.4 % on every day the optimum saves 1 % or more.

### 5.4 The battery

One route over the state of charge. Import and export prices come from the house's forecast balance, plus a wear cost per kWh discharged and the peak duty. The battery sees the true shortfall below the ceiling, which production had always clipped to zero (D-0837). Decisions: D-0768, D-0769, D-0830 … D-0838. Measured after: arbitrage 99.9–100 %, peak shaving 96–100 % with every deficit covered.

### 5.5 Deadlines, cycles and hours

- `deadline_fill` judges a flat day over its own window (D-0771).
- An EV is paused only once it has resumed (D-0776, D-0790).
- `run_once` ranks a start by the energy it places, and keeps a started cycle's start (D-0811, D-0821).
- `cheapest_hours` counts the hours already run today (D-0810).

All equal the optimum on the catalogue.

## 6. The numbers, and their sources

Physical values the recorder measures (cooling time, loss, element power, standby, daily draw, EV efficiency) are fits, D10 §5.6. The evidence below sets what no sensor can.

| number | value | source | decision |
|---|---|---|---|
| coast below target | 0.5 K (1 K evidence-supported; 0.5 K chosen for the planner's full use of the band plus the thermostat's swing) | EN 16798-1 Category I ±1 K ([REHVA](https://www.rehva.eu/rehva-journal/chapter/european-standardisation-on-thermal-environment)); measured comfort, smoke benchmark | D-0775, D-0819 |
| setpoint rate | 1 K/h down, 2 K/h up | ASHRAE 55 drift limits (2.2 K/h); warm discomfort follows the level reached ([Favero et al., SINTEF](https://www.sintef.no/en/publications/publication/1922431)) | D-0775 |
| bedroom floor | 18 °C | WHO Housing and Health Guidelines 2018; EN 16798-1 lowest bedroom class | D-0775, D-0786 |
| floor surface maximum | tile 30 °C bathroom / 29 °C elsewhere; wood, laminate, vinyl, carpet 27 °C | EN 1264; [Boen 2025](https://boen.com/media/bo1ctauh/bo_leggeanvisning_2-lags-parkett_n-f_no_2025.pdf) | D-0775 |
| lowering comfort when away / asleep | opt-in; 3 K away, 1 K through quiet hours | unnoticed in an empty home 2–3 K, asleep 1–1.5 K ([DTU overrides](https://orbit.dtu.dk/en/publications/a-data-driven-study-of-thermostat-overrides-during-demand-respons/), [EQUINOX](https://octopus.energy/blog/EQUINOX-heat-pump-trial-24-25-FAQs/)); opt-in by the owner | D-0772, D-0785 |
| minimum saving | 3 % beyond the modelled losses; 10 % for a panel heater | storage efficiency > 90 % floor-heated, 66–85 % radiators ([Reynders et al. 2017](https://ideas.repec.org/a/eee/appene/v198y2017icp192-202.html)) | D-0774 |
| heat pump pre-heat | decided by the hour; no default outdoor cap | no source for 5 °C; no saving at Norwegian spreads ([Clauß et al. 2019](https://ideas.repec.org/a/eee/appene/v237y2019icp500-518.html)) | D-0773 |
| air-to-water COP | 0.35 × Carnot by heating system | [NIBE F2120](https://www.nibe.eu/download/18.24e4cb8217594fb2fa76acd/1761809552878/FLY_NIBE_F2120_FR_WEB.pdf); [Fraunhofer field study](https://iifiir.org/en/news/fraunhofer-ise-study-heat-pumps-achieve-high-seasonal-performance-in-older-buildings) | D-0773, D-0825 |
| switching budgets | relay 24 planned starts a day; heat pump one change an hour; EV 4 pauses a session | [Finder S40](https://cdn.findernet.com/app/uploads/S40EN.pdf) 100 000 switchings; [Bitzer](https://bitzer.de/shared_media/html/kt-420/en-GB/242642187242698891.html), [Copeland](https://media.copeland.com/0cfb235b-7155-4885-b009-b16b0107a750/AGL_AC_ST_ZRKRE_E_Rev02_FULL.pdf) starts per hour | D-0777 |
| battery wear | LFP 0.04, NMC 0.10 EUR per kWh discharged | [HTW/aquu Stromspeicher-Inspektion 2026](https://solar.htw-berlin.de/wp-content/uploads/HTW-aquu-Stromspeicher-Inspektion-2026.pdf); [Kumtepeli et al. 2024](https://arxiv.org/pdf/2403.10617v1); [Collath et al. 2023](https://opus4.kobv.de/opus4-hs-kempten/files/1891/ID1891_Increasing_the_lifetime_profitability.pdf) | D-0769, D-0830 |
| battery round trip | 0.80 until measured | HTW 2026: 63–78 % grid to load at low discharge | D-0769, D-0834 |
| NMC charge ceiling | 90 % | [Zsoldos et al. 2024](https://www.julianehehl.de/voltaik/lfp-studie.pdf); Wikner 2018 | D-0769 |
| EV pausing | only once the car has resumed | 14–17 % of models fail after a 20-minute pause ([Brinkel et al. 2024](https://www.nature.com/articles/s41467-024-48477-w)) | D-0776 |
| EV efficiency | by phase count and current | 1-phase 6 A loses 13–24 %, 3-phase 5–13 % ([Reick et al. 2021](https://arbor.bfh.ch/bitstreams/67fb6c67-ed54-4535-a0fb-f43b55a2a3f5/download)) | D-0776 |
| fit bounds | foil by sensor; room loss 0.3–5 W/K·m²; room mass 0.012 kWh/K·m³ | [ISO 13790 classes](https://simulationresearch.lbl.gov/modelica/releases/latest/help/Buildings_ThermalZones_ISO13790_Data.html); [Kensby et al. 2015](https://ideas.repec.org/a/eee/appene/v137y2015icp773-781.html) | D-0778, D-0795 |
| water heater | 45 °C floor, 75 °C ready, reserve and lead unchanged | kept by the owner against [Helsedirektoratet](https://www.helsedirektoratet.no/veiledere/legionella/vannfordelingsnett-som-forsyner-dusjer-og-andre-aerosoldannende-tappepunkter/virksomheter-og-eiendommer-med-interne-vannfordelingsnett-skal-ha-drifts-og-vedlikeholdsrutiner-som-bidrar-til-a-hindre-vekst-og-spredning-av-legionella-via-aerosol)'s 55 °C outlet guidance; 75 °C nightly pasteurises the tank (D-0742) | D-0779 |

## 7. What is still open

PLAN §3.0u, BO.1 … BO.7: a heat pump's heat and electric watts (D-0817, D-0829), a margin that follows the fit, the four scenarios that fail on `main`, the sauna's restore (D-0822), a foil read on its floor probe (D-0795), a battery's measured round trip (D-0834), and a cold week in the house. The whole house at a tight limit (R16: 81–89 % of a joint plan on the hardest day) is the owner's decision, since it needs INV-33 changed.
