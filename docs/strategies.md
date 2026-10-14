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

Keeps the appliance at comfort and pauses it in an expensive hour when a cheaper hour soon after can make up for it. A pause never lasts longer than two hours, and the appliance stays on at least 30 minutes between pauses. It never lets the room fall below its lowest temperature. When pausing would save less than 4 % of what the heating costs, PowerPlan leaves the appliance to its own thermostat; see [When a plan is not worth it](#worth_it).

> [!NOTE]
> Since v0.0.11, a new panel heater or foil floor starts on **Heat capacitor**, which also pauses, for as long as the room or floor can take. An appliance already on this plan keeps it.

| | |
|---|---|
| Good for | heating where you want short, fixed pauses rather than ones sized by the room |
| Needs | a comfort temperature and a lowest temperature |
| Key | `best_save` |

<a name="cheapest_hours"></a>
## Cheapest hours

Runs the appliance for the hours a day you set, in the cheapest of them. Nothing tells it when to be finished. An hour where your home has no room for the appliance at full power under your target is skipped, so on a full day it may run fewer hours.

> [!NOTE]
> Since v0.0.11, this plan skips hours with no room under your target. Before that it could plan the appliance into an hour above the target.

| | |
|---|---|
| Good for | a pump, a dehumidifier, anything that needs so many hours a day |
| Needs | how many hours a day it should run |
| Key | `cheapest_hours` |

<a name="deadline_fill"></a>
## Deadline fill

Charges or heats in the cheapest hours before the time you set, and stops when the appliance is full. If time runs short, it runs whatever the price. Where several hours cost the same, a car charges in the first of them and a water heater heats in the last, so the water is hot at the time you set.

| | |
|---|---|
| Good for | the car charger and the water heater |
| Needs | a ready-by time, and a way to read how full the appliance is |
| Key | `deadline_fill` |

<a name="heat_capacitor"></a>
## Heat capacitor

Keeps the appliance off through expensive hours, and through hours when your home has no room under its capacity step. Before such a run of hours, it warms the floor, the room or the tank just enough to stay above the lowest temperature you allow until the run ends. How much is worked out from how fast the appliance cools, which PowerPlan measures from its history.

When the highest temperature you allow is not enough to last the whole run, the appliance heats a little inside it, spread evenly, so no hour gets the whole top-up. A single expensive hour with no run around it is paused if cheaper hours soon after can make up for it. The make-up is spread over those hours, never above the appliance's power or your target, and a pause with no room to make up for it is not planned. Every move must save at least 10 % on the hours it skips. An appliance that only switches between comfort and an energy-saving mode, or a panel heater on a plug, cannot store heat above comfort, so it only pauses; see [When a plan is not worth it](#worth_it).

> [!NOTE]
> Since v0.0.11, this plan sizes its warm-up from how fast the appliance cools, and stays off through full hours of the capacity step. Before that it warmed by a fixed degree in the cheapest quarter of the day, and it was called **Bank heat in cheap hours**.

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
| Needs | a temperature sensor on the tank, best with a top and a bottom probe |
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

Starts the dishwasher, washer or dryer once, in the cheapest window that finishes before the time you set. You fill it and press start; PowerPlan picks the moment.

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
