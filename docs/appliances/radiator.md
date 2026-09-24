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

PowerPlan shows which entity it will control and which it only reads. Check them before you go on.

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

<!-- generated:begin questions:radiator · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Heater type | What kind of heater this is. |
| Room | Which room this heats, which sets the comfort and floor temperatures below. |
| Control | Whether PowerPlan controls a plug or a thermostat. |
| Power | The appliance's power draw, in watts. |
| Power sensor | A sensor measuring this appliance's power, if there is one. |
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

By default PowerPlan keeps the room at comfort and pauses the heater where it saves most, never below the room's lowest temperature. The other plans it offers are listed on [Appliances](README.md#radiator); each is explained on [Plans](../strategies.md).

## Everyday use

Put a panel heater in a room with a heat pump, and PowerPlan heats with the cheaper one, as [Circuits, groups and rooms](../circuits-groups-rooms.md#room) explains.

## Limits

A plug has no temperature: PowerPlan then relies on the room's temperature sensor, or on time.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
