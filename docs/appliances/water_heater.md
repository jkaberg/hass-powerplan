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

Shown only when PowerPlan knows more than one way of talking to the device. Choose one; the best fit is already selected. Which entity PowerPlan reads and controls is checked on the next step.

## Data points

What PowerPlan reads and controls on this appliance, and what it does without each one. The [data points step](README.md#data-points) finds them on the device and asks for the rest.

<!-- generated:begin data_points:water_heater · tools/docs.py writes this block; change the needs or strings.json, not this table -->
| Data point | Needed | What it is for | Without it |
|---|---|---|---|
| Temperature | Recommended | The temperature the device measures. | PowerPlan estimates the water temperature from the power. |
| Power | Recommended | What the appliance draws right now, in watts. | The element, heat loss and daily use stay typical figures. |
| Top temperature | Optional | The temperature at the top of the tank. | The tank's heat is read from one sensor. |
| Bottom temperature | Optional | The temperature at the bottom of the tank. | The tank's heat is read from one sensor. |
| On/off switch | Optional when PowerPlan sets its temperature; Required when PowerPlan turns it on and off | The switch that turns the appliance on and off. | PowerPlan cannot control it. |
| Energy | Optional | The appliance's energy meter, in kWh. | Energy is counted from the power instead. |
| Setpoint | Required when PowerPlan sets its temperature | The temperature the device is set to - what PowerPlan changes. | PowerPlan cannot control it. |
<!-- generated:end data_points:water_heater -->

If the tank has more than one temperature sensor, the middle one is **Temperature**, and the others are **Top temperature** and **Bottom temperature**, under **Advanced** on that step when they are not on the tank's device. Where they sit on the same device and their names say top or bottom, PowerPlan fills them in for you. With all three, PowerPlan sees how much hot water is left after a shower, not only the temperature in the middle.

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

Where the tank's power and temperature are already in Home Assistant's history, PowerPlan measures the element's power and how much heat the tank loses standing, and how much hot water the household uses in a day, over the last three weeks. Days with no hot water used, such as a holiday, are left out, so the tank is not planned short when you come back. Those numbers replace the typical ones, and the review says how many days they came from. You can change them under **Advanced**.

<!-- generated:begin questions:water_heater · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Tank size | The tank's volume, in liters. |
| Element | The heating element's power, in kW. |
| People | How many people use the hot water. |
| Control | Whether PowerPlan controls a plug or a thermostat. |
| Mechanical thermostat | The tank has its own thermostat that PowerPlan cannot set. |
| Ready by | When the hot water or the cycle must be ready. |
| Second ready-by | A second time of day the water must be ready, if any. |
| Legionella cycle | Who runs the weekly anti-legionella cycle. |
| Advanced › Comfort minimum | Advanced: the tank temperature below which PowerPlan reheats at once. |
| Advanced › Ready temperature | Advanced: the temperature the tank is heated to by the ready-by time. |
| Never above | The highest temperature when storing heat in cheap hours; blank uses the room's usual. |
| Advanced › Standby loss | Advanced: watts the tank loses standing. |
| Advanced › Hot water a day | Advanced: the heat the household draws from the tank in a day, in kWh. |
| Advanced › Legionella temperature | Advanced: the temperature the cycle heats to. |
| Advanced › Legionella hold | Advanced: minutes the temperature is held. |
| Advanced › Legionella interval | Advanced: days between cycles. |
| Advanced › Minimum on | Advanced: seconds a switch stays on at least. |
| Advanced › Minimum off | Advanced: seconds a switch stays off at least. |
| Advanced › Command interval | Advanced: seconds between two writes to the device. |
| Advanced › Follow presence | Advanced: lower the target when nobody is home. |
| Advanced › Weekly schedule | Advanced: a schedule helper – on is comfort, off is the vacation level. |
| Advanced › Arrival calendars | Advanced: calendars whose next event is an arrival, a deadline for the comfort target. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:water_heater -->

PowerPlan never lowers the temperature unless you ask. **Save when nobody is home** lowers the target 3 °C while everyone is away. It is off for a new water heater. You turn it on under **Advanced** as **Follow presence**, or later with the switch on the water heater, and it never goes under the lowest temperature.

> [!NOTE]
> Since v0.0.11, **Save when nobody is home** is off for a new water heater. One set up before keeps it as it was.

<a name="questions_followup"></a>
## How far should preheat go?

The water heater has no follow-up question.

## The plan

By default the tank is heated in the cheapest hours before the morning's ready-by time, and a second time if you set one. It never falls below its lowest temperature. When several hours cost the same, as at night on a fixed price, the tank heats in the last of them, so it is hot at the ready-by time and loses less heat waiting.

If the hot water runs low in the evening, choose **Hot water** on the appliance. It keeps a reserve of hot water for the showers ahead and tops the tank up in the cheapest hours before them, at a somewhat higher cost; see [Plans](../strategies.md#hot_water).

> [!NOTE]
> Since v0.0.8, a water heater heats as late as it can among hours that cost the same. A water heater set up before that keeps heating early until you open **Change setup** on it and save. The other plans it offers are listed on [Appliances](README.md#water_heater); each is explained on [Plans](../strategies.md).

## Everyday use

Set when the water must be hot under **Ready by**. With **Legionella** on, PowerPlan heats the tank to a high temperature on the interval you choose, in a cheap hour. Each time the coldest water in the tank stays at the legionella temperature for the hold time anyway, after a hot charge, that counts, and the next cycle moves on. A tank with a **Bottom temperature** is judged by it, because the bottom is the coldest water. The legionella temperature is 70 °C unless you change it under **Advanced**, as the Norwegian health authorities recommend; a tank set up before v0.0.11 at 65 °C is moved to 70 °C when you update, unless you set 65 yourself.

## Limits

A tank on a plain switch with a mechanical thermostat is safe: its own thermostat keeps it from overheating. Without a temperature sensor, PowerPlan estimates the tank's heat from time and power.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
