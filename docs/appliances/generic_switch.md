<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Something else on a switch

# Something else on a switch

Anything else on a switch or a smart plug: a pump, a dehumidifier, an outbuilding's heater.

## What it needs

A switch or a smart plug, and its power if you know it.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

PowerPlan shows which entity it will control and which it only reads. Check them before you go on.

## Data points

What PowerPlan reads and controls on this appliance, and what it does without each one. The [data points step](README.md#data-points) finds them on the device and asks for the rest.

<!-- generated:begin data_points:generic_switch · tools/docs.py writes this block; change the needs or strings.json, not this table -->
| Data point | Needed | What it is for | Without it |
|---|---|---|---|
| On/off switch | Required | The switch that turns the appliance on and off. | PowerPlan cannot control it. |
| Power | Recommended | What the appliance draws right now, in watts. | Its power and hours stay your answers and are not measured. |
| Energy | Optional | The appliance's energy meter, in kWh. | Energy is counted from the power instead. |
<!-- generated:end data_points:generic_switch -->

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

<!-- generated:begin questions:generic_switch · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Appliance | What kind of appliance this is. |
| Power | The appliance's power draw, in watts. |
| Hours per day | How many hours a day it should run, in the cheapest hours. |
| Advanced › Minimum on | Advanced: seconds a switch stays on at least. |
| Advanced › Minimum off | Advanced: seconds a switch stays off at least. |
| Advanced › Inverted switch | Advanced: on means off for this switch. |
| Advanced › Command interval | Advanced: seconds between two writes to the device. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:generic_switch -->

<a name="questions_followup"></a>
## How far should preheat go?

The something else on a switch has no follow-up question.

## The plan

By default it runs the hours a day you set, in the cheapest of them. The other plans it offers are listed on [Appliances](README.md#generic_switch); each is explained on [Plans](../strategies.md).

## Everyday use

Change **Hours per day** as the season changes. **Run now** turns it on for a while.

## Limits

PowerPlan only turns it on and off. For something that must never be off for long, set the minimum times under Advanced.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
