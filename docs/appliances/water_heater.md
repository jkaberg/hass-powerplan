<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Water heater

# Water heater

A water heater PowerPlan heats in the cheapest hours, so the water is hot when the household needs it.

## What it needs

A thermostat or a switch Home Assistant controls. A temperature sensor on the tank lets PowerPlan heat exactly what is needed.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

PowerPlan shows which entity it will control and which it only reads. Check them before you go on.

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

<!-- generated:begin questions:water_heater · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Tank size | The tank's volume, in liters. |
| Element | The heating element's power, in kW. |
| People | How many people use the hot water. |
| Control | Whether PowerPlan controls a plug or a thermostat. |
| Mechanical thermostat | The tank has its own thermostat that PowerPlan cannot set. |
| Temperature sensor | A sensor on the tank, if there is one. |
| Ready by | When the hot water or the cycle must be ready. |
| Second ready-by | A second time of day the water must be ready, if any. |
| Legionella cycle | Who runs the weekly anti-legionella cycle. |
| Advanced › Comfort minimum | Advanced: the tank temperature below which PowerPlan reheats at once. |
| Advanced › Ready temperature | Advanced: the temperature the tank is heated to by the ready-by time. |
| Never above | The highest temperature when storing heat in cheap hours; blank uses the room's usual. |
| Advanced › Standby loss | Advanced: watts the tank loses standing. |
| Advanced › Legionella temperature | Advanced: the temperature the cycle heats to. |
| Advanced › Legionella hold | Advanced: minutes the temperature is held. |
| Advanced › Legionella interval | Advanced: days between cycles. |
| Advanced › Minimum on | Advanced: seconds a switch stays on at least. |
| Advanced › Minimum off | Advanced: seconds a switch stays off at least. |
| Advanced › Command interval | Advanced: seconds between two writes to the device. |
| Advanced › Follow presence | Advanced: lower the target when nobody is home. |
| Advanced › Weekly schedule | Advanced: a schedule helper — on is comfort, off is the vacation level. |
| Advanced › Arrival calendars | Advanced: calendars whose next event is an arrival, a deadline for the comfort target. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:water_heater -->

<a name="questions_followup"></a>
## How far should preheat go?

The water heater has no follow-up question.

## The plan

By default the tank is heated in the cheapest hours before the morning's ready-by time, and a second time if you set one. It never falls below its lowest temperature. The other plans it offers are listed on [Appliances](README.md#water_heater); each is explained on [Plans](../strategies.md).

## Everyday use

Set when the water must be hot under **Ready by**. With **Legionella** on, PowerPlan heats the tank to a high temperature on the interval you choose, in a cheap hour.

## Limits

A tank on a plain switch with a mechanical thermostat is safe: its own thermostat keeps it from overheating. Without a temperature sensor, PowerPlan estimates the tank's heat from time and power.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
