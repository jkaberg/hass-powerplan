<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › Appliances

# Appliances

The kinds of appliance PowerPlan steers, the plan each follows by default, and the others it offers. Each kind has its own page with its questions.

<!-- generated:begin types · tools/docs.py writes this block; change the device type registry, not this table -->
| Appliance | Default plan | Other plans | Key |
|---|---|---|---|
| Dishwasher, washer or dryer | Run once | Always, Deadline fill | `appliance_cycle` |
| Home battery | Peak shave | Always, Arbitrage, Deadline fill | `battery` |
| Car charger | Deadline fill | Always, Cheapest hours, Surplus | `ev` |
| Floor heating | Heat capacitor | Always, Best save, Deadline fill, Schedule | `floor_heating` |
| Something else on a switch | Cheapest hours | Always, Best save, Deadline fill, Schedule, Surplus | `generic_switch` |
| Heat pump | Heat capacitor | Always, Best save, Cheapest hours, Deadline fill, Schedule | `heat_pump` |
| Panel heater | Heat capacitor | Always, Best save, Cheapest hours, Deadline fill, Schedule | `radiator` |
| Water heater | Deadline fill | Always, Best save, Cheapest hours, Heat capacitor, Hot water, Schedule, Surplus | `water_heater` |
<!-- generated:end types -->

<a name="ev"></a>
## Car charger

A car charger PowerPlan starts, stops and turns up or down, so the car is charged by the time you leave, in the cheapest hours. [More about the car charger](ev.md).

<a name="water_heater"></a>
## Water heater

A water heater PowerPlan heats in the cheapest hours, so the water is hot when the household needs it. [More about the water heater](water_heater.md).

<a name="floor_heating"></a>
## Floor heating

Electric floor heating PowerPlan warms in cheap hours, storing heat in the floor so the room stays within half a degree of comfort through expensive ones. [More about the floor heating](floor_heating.md).

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

If the appliance's entities are on no device, as with a YAML package, choose **Not on a device: choose the entities** at the end of the list and pick them all. PowerPlan finds what each one is by its shape, asks for any role it cannot find, and puts the appliance's own entities on a PowerPlan device named after it.

<a name="match"></a>
### Is this the right device?

Shown only when PowerPlan knows more than one way of talking to the device, as with a car charger it can reach over Bluetooth or the cloud. Choose one; the best fit is already selected.

<a name="data-points"></a>
### What does PowerPlan read and control?

Each kind of appliance needs some *data points*: the entities PowerPlan reads, such as a temperature or a power meter, and the ones it controls, such as a setpoint or a switch. What it needs depends on the kind, how the device is controlled and the plan. PowerPlan looks for each one on the device you picked.

- **Found**: listed with its current reading. They are under **Found**, already filled in; open it only to change one.
- **Asked**: when the device has several entities that could be it, you choose one; each choice shows its current reading, and the most likely one is selected. When the device has none, PowerPlan may suggest a sensor fed from the device or the only one in the same room. Each question says what the data point is for and what PowerPlan does without it.
- **Optional**: a data point PowerPlan can do without and did not find waits under **Advanced**, empty.
- **Required**: PowerPlan cannot control the appliance without it, so the step does not continue until it has one.
- **Leave it empty**: a data point that is not required can be left empty. PowerPlan then uses the fallback the question names, and does not ask again. Change it later by reconfiguring the appliance.
- **Disabled**: an entity on the device that could be a data point, but is disabled in Home Assistant, is named so you can enable it.

PowerPlan never picks between two candidates on its own, and never replaces a data point you chose. A sensor that has not changed for a day while another of its kind on the device has is left out, as it measures nothing.

Choose **Another entity** to pick one from anywhere in your home. **Which entity instead?** then asks for it.

<a name="questions"></a>
### About {name}

The questions for this kind of appliance, as its own page lists them. Everything under **Advanced** is already filled in from your answers.

<a name="questions_followup"></a>
### How far should preheat go?

A few answers have a follow-up question, such as how far a heat pump may store heat. It appears only when you turned the first one on.

**See also:** [Plans](../strategies.md) · [Devices](../devices.md) · [Circuits, groups and rooms](../circuits-groups-rooms.md)
