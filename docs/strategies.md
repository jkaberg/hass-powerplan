<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Plans

# Plans

Each appliance follows a plan: the way PowerPlan chooses its hours. The setup picks a plan for each kind of appliance, and you can change it on the appliance.

<!-- generated:begin strategies · tools/docs.py writes this block; change the strategy registry, not this table -->
| Plan | Offered for | Default for | Key |
|---|---|---|---|
| Always | Dishwasher, washer or dryer, Home battery, Car charger, Floor heating, Something else on a switch, Heat pump, Panel heater, Water heater | – | `always` |
| Arbitrage | Home battery | – | `arbitrage` |
| Best save | Floor heating, Something else on a switch, Heat pump, Panel heater, Water heater | – | `best_save` |
| Cheapest hours | Car charger, Something else on a switch, Heat pump, Panel heater, Water heater | Something else on a switch | `cheapest_hours` |
| Deadline fill | Dishwasher, washer or dryer, Home battery, Car charger, Floor heating, Something else on a switch, Heat pump, Panel heater, Water heater | Car charger, Water heater | `deadline_fill` |
| Heat capacitor | Floor heating, Heat pump, Panel heater, Water heater | Floor heating, Heat pump, Panel heater | `heat_capacitor` |
| Hot water | Water heater | – | `hot_water` |
| Peak shave | Home battery | Home battery | `peak_shave` |
| Run once | Dishwasher, washer or dryer | Dishwasher, washer or dryer | `run_once` |
| Schedule | Floor heating, Something else on a switch, Heat pump, Panel heater, Water heater | – | `schedule` |
| Surplus | Car charger, Something else on a switch, Water heater | – | `surplus` |
<!-- generated:end strategies -->

<a name="always"></a>
## Always

Runs the appliance whenever it wants to, as if PowerPlan were not there. PowerPlan still keeps it under your fuse and your target.

| | |
|---|---|
| Good for | an appliance you do not want planned, or while you try PowerPlan out on another one |
| Needs | nothing |
| Key | `always` |

<a name="arbitrage"></a>
## Arbitrage

Charges the battery in the cheapest hours and discharges it in the dearest, when the difference pays for the losses. In between, the battery runs on its own; where that would spend the charge a dearer hour needs, PowerPlan holds it. It keeps the reserve you set.

| | |
|---|---|
| Good for | a home battery with a spot price |
| Needs | a home battery and prices for the next day |
| Key | `arbitrage` |

<a name="best_save"></a>
## Best save

Keeps the appliance at comfort and lets it fall toward its lowest temperature in an expensive hour, then brings it back in a cheaper one. Where PowerPlan knows how fast the appliance cools, it finds the best such route through the next two days and never warms the room above comfort. A pause never lasts longer than two hours, and the appliance stays on at least 30 minutes between pauses. Without that, a floor or room that cools slowly would coast all afternoon on price alone. The appliance never falls below its lowest temperature. A move must save at least 10 % beyond what it already costs in lost heat, so small gains are left alone. When moving the heating would save less than 4 % of what it costs, PowerPlan leaves the appliance to its own thermostat; see [When a plan is not worth it](#worth_it).

> [!NOTE]
> Since v0.0.12, **Best save** plans the whole route for floors and rooms that PowerPlan can measure, instead of pausing hour by hour. Without a cooling measurement it keeps pausing hour by hour, as before. A floor or room coasts at most 1 °C under comfort on this plan.

| | |
|---|---|
| Good for | heating where you want short, fixed pauses rather than ones sized by the room |
| Needs | a comfort temperature and a lowest temperature |
| Key | `best_save` |

<a name="cheapest_hours"></a>
## Cheapest hours

Runs the appliance for the hours a day you set, in the cheapest of them. Nothing tells it when to be finished. An hour where your home has no room for the appliance at full power under your target is skipped, so on a full day it may run fewer hours. The hours it has already run today count, so a day never gets more than you set. The hours run in one stretch stay one stretch.

> [!NOTE]
> Since v0.0.11, this plan skips hours with no room under your target. Before that it could plan the appliance into an hour above the target. Since v0.0.12, the hours already run today count; before, every new plan bought the hours again.

| | |
|---|---|
| Good for | a pump, a dehumidifier, anything that needs so many hours a day |
| Needs | how many hours a day it should run |
| Key | `cheapest_hours` |

<a name="deadline_fill"></a>
## Deadline fill

Charges or heats in the cheapest hours before the time you set, and stops when the appliance is full. If time runs short, it runs whatever the price. Where several hours cost the same, a car charges in the first of them and a water heater heats in the last, so the water is hot at the time you set. Whether the price is flat is judged between now and the deadline, not over the whole day, so a flat evening before a cheaper night is spent waiting for the night. A car that has not shown it resumes after a pause is charged in one run, without gaps; see [Pauses and resumes](appliances/ev.md#pauses).

| | |
|---|---|
| Good for | the car charger and the water heater |
| Needs | a ready-by time, and a way to read how full the appliance is |
| Key | `deadline_fill` |

<a name="heat_capacitor"></a>
## Heat capacitor

Moves the appliance's heat to where it costs least. It warms the floor, the room or the tank before the price rises, lets it coast on that heat while the price is high, and heats again after the price falls. Through a stretch where the price does not change, it holds the comfort temperature and moves nothing. How far it can warm and coast comes from how fast the appliance cools, which PowerPlan measures from its history, and from the highest and lowest temperatures you allow. Comfort is judged against the temperature you set: by default the appliance coasts at most 0.5 degrees under it and warms at most 1 degree over it.

PowerPlan works out the cheapest route through the next two days from the temperature measured now, every quarter of an hour. It is the same route one quarter later, unless the prices or the weather change, so a warm-up that has started is not forgotten. A move counts only for the timing: letting a room sit colder than comfort saves nothing by itself, because the heat it did not use has to be bought back. A heat pump's heat is priced at what the pump delivers per kWh at that hour's outdoor temperature, so a cold night's warm-up costs more than the meter price says.

Hours when your home has no room under its capacity step are stayed off in. The appliance warms before them, just enough to coast through, and heats only what it needs to stay above its lowest temperature. A floor with a step-up in its schedule is warm by then, bought in the cheapest hours the allowed range lets it.

Every move must save at least 3 % of the heat it moves, and a run of moves must also save at least 3 % of what one hour of the appliance at full power costs, so a floor that barely draws is not moved for next to nothing. A setpoint falls at most 1 degree an hour and rises at most 2 degrees an hour, in the steps the thermostat takes (0.5 degrees if it does not say). A floor is told at most every 20 minutes. A relay or element is switched on at most 24 times a day. An appliance that only switches between comfort and an energy-saving mode, or a panel heater on a plug, cannot store heat above comfort, so it only coasts; see [When a plan is not worth it](#worth_it). In energy-saving mode such a thermostat holds 0.5 °C under comfort by default, never under the lowest temperature you allow.

A heat pump warms ahead only if its **Preheat** answer is on, and then only where the cheaper hour still pays for the heat the pump loses by working harder and colder (and below an outdoor limit, if you set one). It is planned in whole hours, one setpoint per hour, since a heat pump takes most of an hour to answer. A room that is still warmer than comfort at that hour is not warmed further.

> [!NOTE]
> Since v0.0.12, this plan finds the route by searching it instead of by rules, and the smallest saving it acts on is 3 %. An appliance set up before keeps the value it has, 10 % for most; open **Re-derive** on it to get 3 %. A panel heater still starts at 10 %. The appliance also coasts at most 0.5 °C under comfort instead of 1 °C, and an energy-saving thermostat holds 0.5 °C under it instead of 2 °C. A heat pump set up before keeps its coast of 1 °C until you open **Re-derive**. A floor or a room is planned a quarter of an hour at a time and a heat pump an hour at a time, where they used to be planned as pauses and stretches.

| | |
|---|---|
| Good for | every heating appliance: floor heating, heat pumps and panel heaters |
| Needs | a comfort temperature, a lowest and a highest temperature, and an outdoor temperature or a weather forecast |
| Key | `heat_capacitor` |

<a name="worth_it"></a>
## When a plan is not worth it

Floor heating, heat pumps and panel heaters on **Best save** or **Heat capacitor** only follow their plan when moving their heating in time is worth at least 4 % of what the heating costs. Otherwise PowerPlan leaves the appliance at comfort on its own thermostat, and the [New plan adopted](events.md#plan_adopted) event says "left to its thermostat" with what moving it would have earned. With a fixed price, such as Norgespris, only the grid's night rate is left to gain, and that is often less. When the prices spread out again, the plan comes back once it is worth 6 %.

A plan that stays off because your home is full is always followed: what it saves is in the capacity step, which the 4 % does not count. Capacity control does not change: PowerPlan still turns heating down to keep the hour under your capacity step, and it still protects the lowest temperature.

<a name="hot_water"></a>
## Hot water

Keeps enough hot water in the tank for the showers and taps ahead, heated in the cheapest hours that still reach them. PowerPlan expects most hot water to be used in the morning and the evening, and measures how much your household uses in a day from the tank's history. At every hour of the next day or two, the tank must hold at least the reserve, two showers by default, after the hot water expected by then. A light day is carried by the night's heating; on a heavy one the tank is topped up in the cheapest hours before the evening, not at the lowest temperature in the middle of it.

The tank is heated no hotter than the coming use needs. Heat bought many hours early counts as a little dearer, for the heat it loses while it waits. Hours when your home has no room under its capacity step are left out, and so are the expensive hours of a tariff with peak windows, unless there is no other way. A legionella cycle that has started, and **Run now**, are planned as before. Before a ready-by time, the next three hours' hot water is in the tank.

Keeping a reserve costs more than **Deadline fill**, which lets the tank run down to its lowest temperature before the night: in PowerPlan's test house it cost the tank 10 to 35 % more in a week. Choose this plan on the appliance if your hot water runs low in the evening; it is available since v0.0.11.

| | |
|---|---|
| Good for | a water heater whose hot water runs low in the evening |
| Needs | a temperature sensor on the tank, best with **Top temperature** and **Bottom temperature** data points too |
| Key | `hot_water` |

<a name="peak_shave"></a>
## Peak shave

Discharges the battery when the home would otherwise go over your target, and recharges it when there is room. It holds the charge until the hours that need it. It keeps the reserve you set.

| | |
|---|---|
| Good for | a home battery on a capacity tariff |
| Needs | a home battery and a target |
| Key | `peak_shave` |

<a name="run_once"></a>
## Run once

Starts the dishwasher, washer or dryer once, in the cheapest window that finishes before the time you set. You fill it and press start; PowerPlan picks the moment. It compares the windows by the power the program draws in each hour, and a cycle that has started keeps its start, however the prices move.

| | |
|---|---|
| Good for | a dishwasher, washer or dryer with remote start |
| Needs | a ready-by time and remote start |
| Key | `run_once` |

<a name="schedule"></a>
## Schedule

Follows a Home Assistant schedule helper: on in its windows, off outside them. PowerPlan still keeps it under your fuse and your target.

| | |
|---|---|
| Good for | an appliance you want run by the clock |
| Needs | a schedule helper |
| Key | `schedule` |

<a name="surplus"></a>
## Surplus

Runs the appliance on the solar power your home would otherwise send to the grid. If the forecast sun cannot fill it before the time you set, it takes the rest from the grid in the cheapest hours. Without a ready-by time it runs on the sun alone.

| | |
|---|---|
| Good for | the car charger, the water heater or a switch, in a home with solar panels |
| Needs | a production sensor and a solar forecast on Home Assistant's energy dashboard |
| Key | `surplus` |

With solar panels, every plan counts the forecast surplus as cheap power: it costs what you would have been paid for it, or nothing above your export limit. A water heater then heats at noon when the sun is cheaper than the night.

To change an appliance's plan, open its device and choose another under **Strategy**. The plans an appliance offers are in the table above.

**See also:** [Appliances](appliances/README.md) · [Glossary](glossary.md)
