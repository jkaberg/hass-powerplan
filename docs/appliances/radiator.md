<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Panel heater

# Panel heater

A panel heater or another radiator PowerPlan pauses in the hours where pausing costs least comfort.

## What it needs

A thermostat, or a smart plug with a power sensor.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

Shown only when PowerPlan knows more than one way of talking to the device. Choose one; the best fit is already selected. Which entity PowerPlan reads and controls is checked on the next step.

## Data points

What PowerPlan reads and controls on this appliance, and what it does without each one. The [data points step](README.md#data-points) finds them on the device and asks for the rest.

<!-- generated:begin data_points:radiator · tools/docs.py writes this block; change the needs or strings.json, not this table -->
| Data point | Needed | What it is for | Without it |
|---|---|---|---|
| On/off switch | Required when PowerPlan turns it on and off | The switch that turns the appliance on and off. | PowerPlan cannot control it. |
| Temperature | Recommended | The temperature the device measures. | PowerPlan can only pause it in expensive hours, not heat ahead. |
| Power | Recommended | What the appliance draws right now, in watts. | The rated power stays your answer and is not measured. |
| Energy | Optional | The appliance's energy meter, in kWh. | Energy is counted from the power instead. |
| Setpoint | Required when PowerPlan sets its temperature | The temperature the device is set to - what PowerPlan changes. | PowerPlan cannot control it. |
<!-- generated:end data_points:radiator -->

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

Where the room's temperature and the heater's power are already in Home Assistant's history, PowerPlan measures how much the heater draws and how fast the room cools when it is off. Those numbers replace the typical ones, and the review says how many days they came from. You can change them under **Advanced**.

Where the thermostat's setpoint is in Home Assistant's history, a temperature it held for at least half of the last 14 days is the comfort temperature, and is not asked. It is never taken from a thermostat PowerPlan has already controlled, since then the history is PowerPlan's own.

<!-- generated:begin questions:radiator · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Heater type | What kind of heater this is. |
| Room | Which room this heats, which sets the comfort and floor temperatures below. |
| Control | Whether PowerPlan controls a plug or a thermostat. |
| Power | The appliance's power draw, in watts. |
| Area | The heated area, in square meters. |
| Comfort temperature | The temperature you want when home; blank uses the room's usual. |
| Never below | The floor PowerPlan never goes under, whatever the price; blank uses the room's usual. |
| Advanced › Minimum on | Advanced: seconds a switch stays on at least. |
| Advanced › Minimum off | Advanced: seconds a switch stays off at least. |
| Advanced › Command interval | Advanced: seconds between two writes to the device. |
| Advanced › Follow presence | Advanced: lower the target when nobody is home. |
| Advanced › Weekly schedule | Advanced: a schedule helper – on is comfort, off is the vacation level. |
| Advanced › Arrival calendars | Advanced: calendars whose next event is an arrival, a deadline for the comfort target. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:radiator -->

<a name="questions_followup"></a>
## How far should preheat go?

The panel heater has no follow-up question.

## The plan

By default PowerPlan keeps the room at comfort and pauses the heater in expensive hours, for as long as the room stays above its lowest temperature. A heater with its own thermostat may also be warmed up to 2 °C above comfort before a long expensive stretch; one on a plug only pauses. The other plans it offers are listed on [Appliances](README.md#radiator); each is explained on [Plans](../strategies.md).

## Everyday use

Put a panel heater in a room with a heat pump, and PowerPlan heats with the cheaper one, as [Circuits, groups and rooms](../circuits-groups-rooms.md#room) explains.

## Limits

A plug has no temperature: PowerPlan then relies on the room's temperature sensor, or on time.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
