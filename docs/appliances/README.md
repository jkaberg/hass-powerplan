<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › Appliances

# Appliances

The kinds of appliance PowerPlan steers, the plan each follows by default, and the others it offers. Each kind has its own page with its questions.

<!-- generated:begin types · tools/docs.py writes this block; change the device type registry, not this table -->
| Appliance | Default plan | Other plans | Key |
|---|---|---|---|
| Dishwasher, washer or dryer | One run before ready-by | Always on, Cheapest hours before the deadline | `appliance_cycle` |
| Home battery | Shave the peaks | Always on, Buy low, sell high, Cheapest hours before the deadline | `battery` |
| Car charger | Cheapest hours before the deadline | Always on, Cheapest hours | `ev` |
| Floor heating | Bank heat in cheap hours | Always on, Save where it hurts least, Cheapest hours before the deadline, Follow a schedule | `floor_heating` |
| Something else on a switch | Cheapest hours | Always on, Save where it hurts least, Cheapest hours before the deadline, Follow a schedule | `generic_switch` |
| Heat pump | Bank heat in cheap hours | Always on, Save where it hurts least, Cheapest hours, Cheapest hours before the deadline, Follow a schedule | `heat_pump` |
| Panel heater | Save where it hurts least | Always on, Cheapest hours, Cheapest hours before the deadline, Bank heat in cheap hours, Follow a schedule | `radiator` |
| Water heater | Cheapest hours before the deadline | Always on, Save where it hurts least, Cheapest hours, Bank heat in cheap hours, Follow a schedule | `water_heater` |
<!-- generated:end types -->

<a name="ev"></a>
## Car charger

A car charger PowerPlan starts, stops and turns up or down, so the car is charged by the time you leave, in the cheapest hours. [More about the car charger](ev.md).

<a name="water_heater"></a>
## Water heater

A water heater PowerPlan heats in the cheapest hours, so the water is hot when the household needs it. [More about the water heater](water_heater.md).

<a name="floor_heating"></a>
## Floor heating

Electric floor heating PowerPlan warms in cheap hours, storing heat in the floor so the room stays at comfort through expensive ones. [More about the floor heating](floor_heating.md).

<a name="heat_pump"></a>
## Heat pump

A heat pump PowerPlan steers by its temperature: warmer when power is cheap, a little cooler when it is dear, and in summer the same for cooling. [More about the heat pump](heat_pump.md).

<a name="radiator"></a>
## Panel heater

A panel heater or another radiator PowerPlan pauses in the hours where pausing costs least comfort. [More about the panel heater](radiator.md).

<a name="appliance_cycle"></a>
## Dishwasher, washer or dryer

A dishwasher, washer or dryer PowerPlan starts once, in the cheapest window that finishes before the time you set. [More about the dishwasher, washer or dryer](appliance_cycle.md).

<a name="generic_switch"></a>
## Something else on a switch

Anything else on a switch or a smart plug: a pump, a dehumidifier, an outbuilding's heater. [More about the something else on a switch](generic_switch.md).

<a name="battery"></a>
## Home battery

A home battery PowerPlan discharges when the home would go over your target, and charges when there is room. [More about the home battery](battery.md).

## Adding an appliance

Go to **Settings** > **Devices & services** > **PowerPlan**, open your home, and select **Add appliance**. The questions are the same for every kind until the kind is chosen.

<a name="user"></a>
### What do you want to control?

Choose the kind of appliance. It decides the questions that follow and the plan PowerPlan suggests.

<a name="device"></a>
### Which device replaces it?

Pick the device that controls the appliance. Devices already added to PowerPlan are marked. When you change an appliance whose device is gone, pick the device that replaced it.

<a name="match"></a>
### Is this the right device?

PowerPlan says what the device looks like and which entity it will control. Check each entity's role; if one is wrong, pick the right one.

<a name="questions"></a>
### About {name}

The questions for this kind of appliance, as its own page lists them. Everything under **Advanced** is already filled in from your answers.

<a name="questions_followup"></a>
### How far should preheat go?

A few answers have a follow-up question, such as how far a heat pump may store heat. It appears only when you turned the first one on.

**See also:** [Plans](../strategies.md) · [Devices](../devices.md) · [Circuits, groups and rooms](../circuits-groups-rooms.md)
