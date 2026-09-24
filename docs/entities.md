<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Entities

# Entities

Every entity PowerPlan adds, what its state means, and where you find it. Use them in cards, automations, and the Energy dashboard.

## Where the entities live

Each home gets a device named after the home, and its entities start with the home's name. An appliance's entities sit on the appliance's own device, next to the entities its own integration made, and start with its name.

In the tables, `<home>` and `<appliance>` stand for those names as Home Assistant writes them in an entity id. **Where** says whether an entity shows on the device page, sits under **Configuration** or **Diagnostic**, and whether it starts switched off. To switch one on, select it on the device page, then **Settings** > **Enabled**.

## The home's entities

<!-- generated:begin entities:home · tools/docs.py writes this block; change the entity, not this table -->
| Entity | Name | Unit | Where |
|---|---|---|---|
| `binary_sensor.<home>_hour_change` | Hour change | – | Diagnostic, off by default |
| `binary_sensor.<home>_meter_data` | Meter data | – | Diagnostic, off by default |
| `binary_sensor.<home>_meter_reading` | Meter reading | – | Diagnostic, off by default |
| `binary_sensor.<home>_peak_warning` | Peak warning | – | shown |
| `binary_sensor.<home>_tomorrow_s_prices` | Tomorrow's prices | – | shown |
| `button.<home>_fetch_prices_again` | Fetch prices again | – | Configuration |
| `button.<home>_plan_again` | Plan again | – | Configuration |
| `button.<home>_relearn_normal_usage` | Relearn normal usage | – | Configuration, off by default |
| `button.<home>_reload_this_month_s_peaks` | Reload this month's peaks | – | Configuration, off by default |
| `calendar.<home>_planned_runs` | Planned runs | – | shown |
| `event.<home>_events` | Events | – | shown |
| `number.<home>_safety_margin` | Safety margin | kWh | Configuration, off by default |
| `select.<home>_capacity_step_target` | Capacity step target | – | Configuration |
| `select.<home>_presence` | Presence | – | shown |
| `select.<home>_strictness` | Strictness | – | Configuration |
| `sensor.<home>_calculation_time` | Calculation time | ms | Diagnostic, off by default |
| `sensor.<home>_capacity_metric_this_period` | Capacity metric this period | kW | shown |
| `sensor.<home>_capacity_step_this_month` | Capacity step this month | – | shown |
| `sensor.<home>_control_level` | Control level | – | Diagnostic |
| `sensor.<home>_cost_this_month` | Cost this month | ‹currency› | shown |
| `sensor.<home>_deviations_this_month` | Deviations this month | – | shown |
| `sensor.<home>_electricity_price_now` | Electricity price now | ‹currency›/kWh | shown |
| `sensor.<home>_estimated_savings_this_month` | Estimated savings this month | ‹currency› | shown |
| `sensor.<home>_expected_capacity_step` | Expected capacity step | – | shown |
| `sensor.<home>_expected_usage_this_hour` | Expected usage this hour | kWh | shown |
| `sensor.<home>_last_decision` | Last decision | – | Diagnostic, off by default |
| `sensor.<home>_learning` | Learning | % | Diagnostic, off by default |
| `sensor.<home>_meter_status` | Meter status | – | shown |
| `sensor.<home>_next_risky_hour` | Next risky hour | – | Diagnostic |
| `sensor.<home>_planned_usage` | Planned usage | kWh | Diagnostic |
| `sensor.<home>_power_available_now` | Power available now | kW | shown |
| `sensor.<home>_price_source` | Price source | – | Diagnostic, off by default |
| `sensor.<home>_prices_known_until` | Prices known until | – | Diagnostic |
| `sensor.<home>_production_now` | Production now | kW | off by default |
| `sensor.<home>_recommendation` | Recommendation | – | shown |
| `sensor.<home>_surplus_now` | Surplus now | kW | off by default |
| `sensor.<home>_target_this_hour` | Target this hour | kWh | shown |
| `sensor.<home>_usage_this_hour` | Usage this hour | kWh | shown |
| `switch.<home>_automatic_control` | Automatic control | – | shown |
<!-- generated:end entities:home -->

Where your grid company counts half-hours or quarter-hours instead of hours, the entities for this hour count that period instead, and their names say so.

Where your grid company bills no capacity step, **Target this hour** and **Capacity metric this period** read *unknown*: there is no target and no metric to report.

## Each appliance's entities

<!-- generated:begin entities:appliance · tools/docs.py writes this block; change the entity, not this table -->
| Entity | Name | Unit | Where | Appliances |
|---|---|---|---|---|
| `button.<appliance>_run_now` | Run now | – | shown | Dishwasher, washer or dryer |
| `number.<appliance>_always_charge_to_at_least` | Always charge to at least | % | Configuration | Car charger |
| `number.<appliance>_charge_to` | Charge to | % | shown | Car charger |
| `number.<appliance>_never_colder_than` | Never colder than | °C | Configuration, off by default | Floor heating, Heat pump, Panel heater, Water heater |
| `number.<appliance>_never_warmer_than` | Never warmer than | °C | Configuration, off by default | Floor heating, Heat pump, Panel heater, Water heater |
| `number.<appliance>_run_now_for_at_most` | Run now for at most | h | Configuration | Car charger, Something else on a switch, Water heater |
| `select.<appliance>_control` | Control | – | shown | all |
| `select.<appliance>_priority` | Priority | – | Configuration | all |
| `select.<appliance>_strategy` | Strategy | – | Configuration | Car charger, Floor heating, Heat pump, Panel heater, Water heater |
| `sensor.<appliance>_cost_this_month` | Cost this month | ‹currency› | shown | all |
| `sensor.<appliance>_energy_in_total` | Energy in total | kWh | Diagnostic | all |
| `sensor.<appliance>_energy_this_month` | Energy this month | kWh | Diagnostic, off by default | all |
| `sensor.<appliance>_granted_power` | Granted power | W | Diagnostic, off by default | all |
| `sensor.<appliance>_health` | Health | – | Diagnostic | all |
| `sensor.<appliance>_learned_charging_efficiency` | Learned charging efficiency | – | Diagnostic, off by default | Car charger |
| `sensor.<appliance>_learned_heat_loss` | Learned heat loss | W/K | Diagnostic, off by default | Floor heating, Heat pump, Panel heater |
| `sensor.<appliance>_learned_heat_up_rate` | Learned heat-up rate | K/h | Diagnostic, off by default | Floor heating, Heat pump, Panel heater |
| `sensor.<appliance>_learned_rated_power` | Learned rated power | W | Diagnostic, off by default | Floor heating, Panel heater, Water heater |
| `sensor.<appliance>_learned_standby_loss` | Learned standby loss | W | Diagnostic, off by default | Water heater |
| `sensor.<appliance>_next_legionella_cycle` | Next legionella cycle | – | shown | Water heater |
| `sensor.<appliance>_plan_status` | Plan status | – | shown | all |
| `sensor.<appliance>_planned_energy` | Planned energy | kWh | Diagnostic, off by default | all |
| `sensor.<appliance>_reserved_power` | Reserved power | W | Diagnostic, off by default | all |
| `sensor.<appliance>_savings_this_month` | Savings this month | ‹currency› | shown | Car charger, Dishwasher, washer or dryer, Floor heating, Heat pump, Panel heater, Water heater |
| `switch.<appliance>_save_when_nobody_is_home` | Save when nobody is home | – | Configuration | Floor heating, Heat pump, Panel heater |
| `switch.<appliance>_save_when_nobody_is_home` | Save when nobody is home | – | Configuration, off by default | Water heater |
| `time.<appliance>_ready_by` | Ready by | – | shown | Car charger, Dishwasher, washer or dryer, Water heater |
<!-- generated:end entities:appliance -->

**Appliances** says which kinds of appliance get the entity; "all" means every kind. A home battery gets the entities that fit it when you add one.

## The states that need words

<a name="active"></a>
### Automatic control

The home's main switch. **On**: PowerPlan steers the appliances it may control. **Off (trial mode)**: PowerPlan hands every appliance back and only shows what it would do. Plans, costs, and savings keep updating either way.

| | |
|---|---|
| Entity | `switch.<home>_automatic_control` |
| Key | `active` |

<a name="presence"></a>
### Presence

Whether anyone is home, which lets heating save while nobody is there.

| State | Meaning |
|---|---|
| **Automatic** | From the people you chose during setup. |
| **Home**, **Away**, **Vacation** | Set by you, until you choose **Automatic** again, or until the time you gave the **Set presence** action. |

Key: `presence`.

<a name="target"></a>
### Capacity step target

The capacity step PowerPlan plans to stay in. **Automatic** holds the step this month's usage has already reached, and never aims above it. **Step 1**, **Step 2**, and so on name your grid company's steps from the lowest, and **Configured kW** keeps the limit you typed in during setup. Key: `target`.

<a name="risk"></a>
### Strictness

How hard PowerPlan works to keep every hour under the target.

| State | Meaning |
|---|---|
| **Strict (recommended)** | Every hour counts. PowerPlan pauses what can wait before any hour goes over. |
| **Use paid hours** | If your bill counts only your highest hours, an hour below the ones already counted is free to use. |
| **Flexible** | Aims at the month's average, so a single high hour is allowed when the rest stay low. |

Key: `risk`.

<a name="level"></a>
### Capacity step this month

The capacity step your usage so far this month puts you in, by your grid company's name for it. Its attributes carry the numbers behind it.

| Attribute | Meaning |
|---|---|
| `metric_kw` | The kW your grid company bills by, so far this month. |
| `fee` | The step's monthly fee. |
| `confidence` | `exact`; `partial` while the month has fewer days on record than the step counts; `coarse` when part of the history is estimated. |
| `top_entries` | The days, or hours, that count toward the step: `day`, `kw`, and whether it is `estimated`. |

**Expected capacity step** is where the month is heading if the rest of it goes as planned. Key: `level`.

<a name="stage"></a>
### Control level

How much PowerPlan is holding back right now, from 0 to 4. It starts diagnostic, so switch it on to use it.

| State | What PowerPlan does |
|---|---|
| 0 | Nothing held back. |
| 1 | Turns down appliances that can run slower, such as a car charger. |
| 2 | Also pauses water heaters and heating, down to their comfort minimum. |
| 3 | Also turns heat pumps down by one degree. |
| 4 | Everything off except what is below its comfort minimum. Only to protect the main fuse or a circuit's fuse, never for a capacity step. |

The `reason` attribute says why, and `since` says since when. Key: `stage`.

<a name="advice"></a>
### Recommendation

What PowerPlan would tell you about your capacity step this month, one state at a time. The `items` attribute lists every recommendation that applies, with its numbers.

| State | Meaning |
|---|---|
| **All good** | Nothing to do. |
| **Room below the next capacity step** | You can use more without moving up a step. |
| **More high days would raise the step** | The hours that count are close to the next step. |
| **Today's peak is already paid for** | Today can go as high as a day already counted, at no extra cost. |
| **An old month is leaving the average** | Your grid company averages several months, and a high one drops out soon. |
| **Some history is estimated** | Part of the month is missing, so the numbers are estimates. |
| **Close to your contracted power** | Your usage is near the power your contract allows. |

Key: `advice`.

<a name="reasons"></a>
### Last decision

What PowerPlan did last, in a word. It starts diagnostic.

| State | Meaning |
|---|---|
| **All as planned** | Appliances run as their plans say. |
| **Holding back to stay on target** | PowerPlan turned something down to keep the hour under the target. |
| **Pausing appliances** | At least one appliance is paused. |
| **Waiting for the meter** | The meter's reading is missing, so PowerPlan waits before it changes anything. |
| **Trial mode: controlling nothing** | **Automatic control** is off. |
| **Fallback mode: controlling nothing** | PowerPlan hit an error. A repair says what to do. |

Key: `reasons`.

<a name="meter_health"></a>
### Meter status

Whether PowerPlan gets readings from your electricity meter: **OK**, **Slow** when readings arrive late, or **No data**. With no data, PowerPlan waits and raises a repair. Key: `meter_health`.

<a name="price_source_health"></a>
### Price source

Whether PowerPlan gets electricity prices: **OK**, **Missing prices** when the next prices are late, or **Error** when the source fails. With an error, PowerPlan raises a repair. Key: `price_source_health`.

<a name="price_forecast"></a>
### Prices known until

The time up to which PowerPlan knows your electricity prices. The `slots` attribute lists each hour or quarter-hour with your price, `total`, and whether it is known or estimated. With a spot price, each one also has `spot`, the market price without VAT. With a fixed price such as Norgespris, `fixed_price` is that price with VAT, and `spot` is what the market would have charged. The dashboard's price card reads these attributes. To fetch the prices again now, press **Fetch prices again**. Key: `price_forecast`.

<a name="plan_status"></a>
### Plan status

What one appliance is doing, and why, in one state. The first state in this list that is true is the one shown.

| State | Meaning |
|---|---|
| **Device not answering** | The appliance's device does not respond. |
| **Changed by hand** | Someone changed the device since PowerPlan last did. The plan waits. |
| **Run now is on** | You started it with **Run now**. |
| **Not controlled by PowerPlan** | Its **Control** is **Don't control**, or **Controlled by something else**. |
| **Trial mode** | Its **Control** is **Trial mode**, or the home's **Automatic control** is off. |
| **No car connected**, **Charging**, **Done** | A car charger's session. |
| **Paused to hold the capacity step** | Paused, to keep the hour under the target. |
| **Needs no power now** | Full, warm, or finished. |
| **Running as planned** | Drawing power in a planned hour. |
| **Waiting for cheap power** | It needs power, and the plan has it waiting for a cheaper hour. |

The `reason_key` attribute says what PowerPlan last did to the device, for example **Sent, waiting for the device to confirm**. While the appliance waits for a planned run, it says **Run planned** instead, and `reason_params` gives the start time and the energy planned. `next_start` is when the plan starts it next. While it waits, `why_party` says whose price makes the wait worth it: `grid` for your grid company, `supplier` for your electricity supplier, `state` for taxes. `why_difference` says how much lower that part of the price is then, per kWh. Key: `plan_status`.

<a name="control"></a>
### Control

How PowerPlan treats one appliance.

| State | Meaning |
|---|---|
| **Automatic** | PowerPlan runs it by its plan. |
| **Run now** | It runs now, whatever the price, for at most **Run now for at most** hours. |
| **Don't control** | PowerPlan leaves it alone, but still counts its power. |
| **Trial mode** | PowerPlan shows what it would do with it, and changes nothing. |
| **Controlled by something else** | Another system steers it, and PowerPlan plans around it. |

Key: `control`.

<a name="strategy"></a>
### Strategy

The plan the appliance follows, from the plans its kind offers. For example, **Cheapest hours before the deadline** for a car charger. Key: `strategy`.

<a name="priority"></a>
### Priority

Which appliance keeps its power longest when PowerPlan must pause something: **Low** pauses first, **High** last. Key: `priority`.

<a name="health"></a>
### Health

Whether the appliance's device answers: **OK**, **Transient** after a missed answer, or **Unhealthy** when it keeps failing. It starts diagnostic. Key: `health`.

<a name="savings_month"></a>
### Savings this month

What the appliance saved this month by using its energy at cheaper times than it would have without PowerPlan. The same energy is priced as it would have run: spread evenly over the day for a heater or water heater, at full power from plug-in for a car, from your request for a dishwasher. It can be negative: then PowerPlan used it at dearer times.

A day's savings are added after midnight, and a charge's when the car is done. Until then the attribute `pending` is `true`. In trial mode the savings are zero, because PowerPlan changed nothing.

The attribute `model_savings` appears once PowerPlan's model of the appliance has proved accurate in trial mode. It also counts energy the plan saved or used, for example while nobody is home. **Unknown**, with `reason: no_reference`, means there is nothing to compare with yet. The cards then show " - ".

The attributes `price_paid` and `price_reference` say the same thing per kWh: what the appliance's counted energy cost, and what it would have cost at the times it would have run. Both are empty until at least 0.1 kWh has been counted.

<a name="savings"></a>
### Estimated savings this month

What the whole home saved this month: every appliance's savings, plus the capacity fee PowerPlan kept you from paying. Its attributes say where the money came from.

| Attribute | Meaning |
|---|---|
| `capacity_savings`, `energy_savings` | The part from a lower capacity step, and the part from cheaper hours. |
| `capacity_step`, `capacity_step_without` | Your capacity step, and the step you would be on without PowerPlan. |
| `metric_kw`, `metric_kw_without` | The kW your step is counted from, with and without PowerPlan. |
| `price_paid`, `price_reference`, `kwh_counted` | What the appliances' counted energy cost per kWh, what it would have cost, and how many kWh that is. |

Days are counted once they are over, so these figures lag today by up to a day. Key: `savings`.

<a name="deviations"></a>
### Deviations this month

What PowerPlan's plans cost you this month, counted as they happen. The number is the sum of three things: deadlines missed, times an appliance fell below its comfort temperature, and hours that ended over the capacity target. **0** means none of them happened.

| Attribute | Meaning |
|---|---|
| `deadlines_met`, `deadlines_missed` | For each appliance with a ready-by time, how often it was ready and how often it was not. |
| `comfort_min`, `comfort_episodes` | For each appliance, the minutes below its comfort temperature and how many times it went below. |
| `over_windows`, `windows` | Hours that ended over the target, out of the hours counted. |

The counts start from zero on the first of each month. Key: `deviations`.

<a name="events"></a>
### Events

Shows PowerPlan's last event, such as **Peak warning** or **New plan adopted**, and puts each one in the logbook. [Events](events.md) lists them all. Key: `events`.

**See also:** [Actions](actions.md) · [Events](events.md) · [Glossary](glossary.md)
