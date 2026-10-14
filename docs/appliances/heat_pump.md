<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Heat pump

# Heat pump

A heat pump PowerPlan steers by its temperature: warmer when power is cheap, a little cooler when it is dear, and in summer the same for cooling.

## What it needs

A heat pump Home Assistant shows as a climate entity. Its rated power and the house's size let PowerPlan estimate how much it draws.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

PowerPlan shows which entity it will control and which it only reads. Check them before you go on.

## Data points

What PowerPlan reads and controls on this appliance, and what it does without each one. The [data points step](README.md#data-points) finds them on the device and asks for the rest.

<!-- generated:begin data_points:heat_pump · tools/docs.py writes this block; change the needs or strings.json, not this table -->
| Data point | Needed | What it is for | Without it |
|---|---|---|---|
| Temperature | Required | The temperature the device measures. | PowerPlan cannot plan the heat. |
| Power | Recommended | What the appliance draws right now, in watts. | The rated power stays your answer and is not measured. |
| Outdoor temperature | Recommended | The temperature outside. | The weather forecast's temperature is used. |
| Outlet temperature | Optional | The temperature of the air or water the heat pump delivers. | Nothing changes in the plan. |
| Energy | Optional | The appliance's energy meter, in kWh. | Energy is counted from the power instead. |
| Setpoint | Required | The temperature the device is set to - what PowerPlan changes. | PowerPlan cannot control it. |
<!-- generated:end data_points:heat_pump -->

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

Where the room's temperature is already in Home Assistant's history, PowerPlan measures how fast the room cools when the heat pump is off. That number replaces the one worked out from the building's age, and the review says how many days it came from. You can change it under **Advanced**.

<!-- generated:begin questions:heat_pump · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Heat pump type | Air-to-air, air-to-water or ground source. |
| Area | The heated area, in square meters. |
| Building age | When the house was built, which sets its heat loss. |
| Rated power | The heat pump's rated electrical power, in kW. |
| Comfort temperature | The temperature you want when home; blank uses the room's usual. |
| Advanced › COP curve | Advanced: outdoor temperature to COP, as `-10:2.1, 7:3.8`. |
| Advanced › Band | Advanced: the kelvin the room may swing around the target. |
| Advanced › Command interval | Advanced: seconds between two writes to the device. |
| Advanced › Dwell | Advanced: seconds a setpoint is held before the next change. |
| Plan for cooling | Plan for summer: cool the room to the comfort temperature instead of heating it. |
| Advanced › Preheat | Advanced: bank heat before an expensive hour. |
| Advanced › Never switch when | Advanced: entities that, when on, stop PowerPlan changing this device. |
| Advanced › Follow presence | Advanced: lower the target when nobody is home. |
| Advanced › Weekly schedule | Advanced: a schedule helper – on is comfort, off is the vacation level. |
| Advanced › Arrival calendars | Advanced: calendars whose next event is an arrival, a deadline for the comfort target. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:heat_pump -->

The **Outdoor sensor** is also the home's outdoor temperature: in a [room](../circuits-groups-rooms.md#room) with a heat pump and a floor, PowerPlan compares them at today's temperature. Without a sensor it uses the weather entity's forecast for the current hour.

<a name="questions_followup"></a>
## How far should preheat go?

This follow-up comes after **Preheat** is turned on: above the outdoor temperature you set, storing heat stops being worth it.

## The plan

By default the room is warmed within its band before expensive hours, and the heat pump stays off through them as long as the room stays in the band. A unit that can cool asks **Plan for cooling**: turned on, PowerPlan cools the room ahead of expensive hours instead. The other plans it offers are listed on [Appliances](README.md#heat_pump); each is explained on [Plans](../strategies.md).

## Everyday use

Switch **Plan for cooling** on and off when you switch your unit between heating and cooling. **Run now** holds comfort whatever the price.

## Limits

PowerPlan never turns the heat pump's power off and never pauses it while it defrosts. The band is at most 2 degrees each way.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
