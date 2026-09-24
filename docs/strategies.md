<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Plans

# Plans

Each appliance follows a plan: the way PowerPlan chooses its hours. The setup picks a plan for each kind of appliance, and you can change it on the appliance.

<!-- generated:begin strategies · tools/docs.py writes this block; change the strategy registry, not this table -->
| Plan | Offered for | Default for | Key |
|---|---|---|---|
| Always on | Dishwasher, washer or dryer, Home battery, Car charger, Floor heating, Something else on a switch, Heat pump, Panel heater, Water heater | – | `always` |
| Buy low, sell high | Home battery | – | `arbitrage` |
| Save where it hurts least | Floor heating, Something else on a switch, Heat pump, Panel heater, Water heater | Panel heater | `best_save` |
| Cheapest hours | Car charger, Something else on a switch, Heat pump, Panel heater, Water heater | Something else on a switch | `cheapest_hours` |
| Cheapest hours before the deadline | Dishwasher, washer or dryer, Home battery, Car charger, Floor heating, Something else on a switch, Heat pump, Panel heater, Water heater | Car charger, Water heater | `deadline_fill` |
| Bank heat in cheap hours | Floor heating, Heat pump, Panel heater, Water heater | Floor heating, Heat pump | `heat_capacitor` |
| Shave the peaks | Home battery | Home battery | `peak_shave` |
| One run before ready-by | Dishwasher, washer or dryer | Dishwasher, washer or dryer | `run_once` |
| Follow a schedule | Floor heating, Something else on a switch, Heat pump, Panel heater, Water heater | – | `schedule` |
| Solar surplus | Car charger, Something else on a switch, Water heater | – | `surplus` |
<!-- generated:end strategies -->

<a name="always"></a>
## Always on

Runs the appliance whenever it wants to, as if PowerPlan were not there. PowerPlan still keeps it under your fuse and your target.

| | |
|---|---|
| Good for | an appliance you do not want planned, or while you try PowerPlan out on another one |
| Needs | nothing |
| Key | `always` |

<a name="arbitrage"></a>
## Buy low, sell high

Charges the battery in the cheapest hours and discharges it in the dearest, when the difference pays for the losses. In between, the battery runs on its own; where that would spend the charge a dearer hour needs, PowerPlan holds it. It keeps the reserve you set.

| | |
|---|---|
| Good for | a home battery with a spot price |
| Needs | a home battery and prices for the next day |
| Key | `arbitrage` |

<a name="best_save"></a>
## Save where it hurts least

Keeps the appliance at comfort and pauses it in the hours where pausing costs least comfort and saves most money. It never lets the room fall below its lowest temperature.

| | |
|---|---|
| Good for | panel heaters and other heating that holds little heat |
| Needs | a comfort temperature and a lowest temperature |
| Key | `best_save` |

<a name="cheapest_hours"></a>
## Cheapest hours

Runs the appliance for the hours a day you set, in the cheapest of them. Nothing tells it when to be finished.

| | |
|---|---|
| Good for | a pump, a dehumidifier, anything that needs so many hours a day |
| Needs | how many hours a day it should run |
| Key | `cheapest_hours` |

<a name="deadline_fill"></a>
## Cheapest hours before the deadline

Charges or heats in the cheapest hours before the time you set, and stops when the appliance is full. If time runs short, it runs whatever the price.

| | |
|---|---|
| Good for | the car charger and the water heater |
| Needs | a ready-by time, and a way to read how full the appliance is |
| Key | `deadline_fill` |

<a name="heat_capacitor"></a>
## Bank heat in cheap hours

Stores heat in the floor, the room or the tank when power is cheap, a little above comfort, and lets it coast through expensive hours. It never goes above the highest temperature you allow.

| | |
|---|---|
| Good for | floor heating and heat pumps, which hold heat for hours |
| Needs | a comfort temperature and a highest temperature |
| Key | `heat_capacitor` |

<a name="peak_shave"></a>
## Shave the peaks

Discharges the battery when the home would otherwise go over your target, and recharges it when there is room. It holds the charge until the hours that need it. It keeps the reserve you set.

| | |
|---|---|
| Good for | a home battery on a capacity tariff |
| Needs | a home battery and a target |
| Key | `peak_shave` |

<a name="run_once"></a>
## One run before ready-by

Starts the dishwasher, washer or dryer once, in the cheapest window that finishes before the time you set. You fill it and press start; PowerPlan picks the moment.

| | |
|---|---|
| Good for | a dishwasher, washer or dryer with remote start |
| Needs | a ready-by time and remote start |
| Key | `run_once` |

<a name="schedule"></a>
## Follow a schedule

Follows a Home Assistant schedule helper: on in its windows, off outside them. PowerPlan still keeps it under your fuse and your target.

| | |
|---|---|
| Good for | an appliance you want run by the clock |
| Needs | a schedule helper |
| Key | `schedule` |

<a name="surplus"></a>
## Solar surplus

Runs the appliance on the solar power your home would otherwise send to the grid. If the forecast sun cannot fill it before the time you set, it takes the rest from the grid in the cheapest hours. Without a ready-by time it runs on the sun alone.

| | |
|---|---|
| Good for | the car charger, the water heater or a switch, in a home with solar panels |
| Needs | a production sensor and a solar forecast on Home Assistant's energy dashboard |
| Key | `surplus` |

With solar panels, every plan counts the forecast surplus as cheap power: it costs what you would have been paid for it, or nothing above your export limit. A water heater on **Cheapest hours before the deadline** then heats at noon when the sun is cheaper than the night.

To change an appliance's plan, open its device and choose another under **Strategy**. The plans an appliance offers are in the table above.

**See also:** [Appliances](appliances/README.md) · [Glossary](glossary.md)
