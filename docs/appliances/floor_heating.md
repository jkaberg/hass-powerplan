<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Floor heating

# Floor heating

Electric floor heating PowerPlan warms in cheap hours, storing heat in the floor so the room stays at comfort through expensive ones.

## What it needs

A floor thermostat Home Assistant shows as a climate entity, such as a Heatit Z-TRM. PowerPlan finds its floor sensor and its eco mode by itself.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

PowerPlan shows which entity it will control and which it only reads. Check them before you go on.

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

<!-- generated:begin questions:floor_heating · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Room | Which room this heats, which sets the comfort and floor temperatures below. |
| Floor covering | What is on the floor, which sets how it stores and gives off heat. |
| Heating type | What is under the floor. |
| Area | The heated area, in square meters. |
| Sensor | Which sensor the thermostat regulates on. |
| Comfort temperature | The temperature you want when home; blank uses the room's usual. |
| Never below | The floor PowerPlan never goes under, whatever the price; blank uses the room's usual. |
| Never above | The highest temperature when storing heat in cheap hours; blank uses the room's usual. |
| Advanced › Screed depth | Advanced: millimeters of screed over the cable. |
| Advanced › Heat loss | Advanced: watts lost per kelvin to the outside. |
| Advanced › Thermostat swing | Advanced: the thermostat's own hysteresis. |
| Advanced › Minimum on | Advanced: seconds a switch stays on at least. |
| Advanced › Minimum off | Advanced: seconds a switch stays off at least. |
| Advanced › Command interval | Advanced: seconds between two writes to the device. |
| Advanced › Follow presence | Advanced: lower the target when nobody is home. |
| Advanced › Weekly schedule | Advanced: a schedule helper — on is comfort, off is the vacation level. |
| Advanced › Arrival calendars | Advanced: calendars whose next event is an arrival, a deadline for the comfort target. |
<!-- generated:end questions:floor_heating -->

<a name="questions_followup"></a>
## How far should preheat go?

The floor heating has no follow-up question.

## The plan

By default the floor stores heat a little above comfort when power is cheap, and coasts when it is dear. It never goes under the room's lowest temperature, and never above what the covering allows. The other plans it offers are listed on [Appliances](README.md#floor_heating); each is explained on [Plans](../strategies.md).

## Everyday use

Put several floors in a group so they take turns when an hour is tight, as [Circuits, groups and rooms](../circuits-groups-rooms.md#group) explains.

## Limits

The highest floor temperature depends on the covering: wood and laminate allow less than tiles. PowerPlan sets the thermostat's own floor limit, so the floor is safe even if PowerPlan stops.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
