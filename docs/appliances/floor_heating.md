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

Shown only when PowerPlan knows more than one way of talking to the device. Choose one; the best fit is already selected. Which entity PowerPlan reads and controls is checked on the next step.

## Data points

What PowerPlan reads and controls on this appliance, and what it does without each one. The [data points step](README.md#data-points) finds them on the device and asks for the rest.

<!-- generated:begin data_points:floor_heating · tools/docs.py writes this block; change the needs or strings.json, not this table -->
| Data point | Needed | What it is for | Without it |
|---|---|---|---|
| Setpoint | Required | The temperature the device is set to - what PowerPlan changes. | PowerPlan cannot control it. |
| Temperature | Required | The temperature the device measures. | PowerPlan cannot plan the heat. |
| Floor temperature | Recommended | The floor's own temperature, which the plan heats the floor by. | The plan uses the room temperature instead. |
| Lowest floor temperature | Recommended | The thermostat's lowest floor temperature, which it keeps by itself. | The lowest temperature holds only while PowerPlan runs. |
| Power | Recommended | What the appliance draws right now, in watts. | PowerPlan uses a typical figure per square meter and cannot measure it. |
| Energy | Optional | The appliance's energy meter, in kWh. | Energy is counted from the power instead. |
| Outdoor temperature | Optional | The temperature outside. | Your home's outdoor temperature is used. |
| Mode select | Required when PowerPlan sets its mode | The thermostat's mode: heating or energy saving. | PowerPlan cannot switch it to energy saving. |
| Eco setpoint | Recommended when PowerPlan sets its mode | The temperature the thermostat holds in energy-saving mode. | The thermostat keeps its own energy-saving temperature. |
| Hysteresis | Optional when PowerPlan sets its mode | How far the temperature may swing before the thermostat switches. | The thermostat keeps its own setting. |
<!-- generated:end data_points:floor_heating -->

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

Where the floor's temperature and power are already in Home Assistant's history, PowerPlan measures how fast the floor cools when it is off and how much it draws. Those numbers replace the typical ones, and the review says how many days they came from. You can change them under **Advanced**.

Without history, a heating foil under wood, laminate, vinyl or carpet is taken to draw 60 W per square meter, and a mat under tiles 120. A foil warms the room more than the floor, so PowerPlan never takes it to stay warm for more than two days.

> [!NOTE]
> Since v0.0.11, a foil floor's measured power is used even when it is far from the typical figure, and a foil floor set up before then at the mat figure, 120 W per square meter, is moved to 60 when you update.

Where the thermostat's setpoint is in Home Assistant's history, a temperature it held for at least half of the last 14 days is the comfort temperature, and is not asked. It is never taken from a thermostat PowerPlan has already controlled, since then the history is PowerPlan's own.

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
| Advanced › Coast time | Advanced: hours for the floor's heat to fall by two thirds when off. |
| Advanced › Thermostat swing | Advanced: the thermostat's own hysteresis. |
| Advanced › Minimum on | Advanced: seconds a switch stays on at least. |
| Advanced › Minimum off | Advanced: seconds a switch stays off at least. |
| Advanced › Command interval | Advanced: seconds between two writes to the device. |
| Advanced › Follow presence | Advanced: lower the target when nobody is home. |
| Advanced › Weekly schedule | Advanced: a schedule helper – on is comfort, off is the vacation level. |
| Advanced › Arrival calendars | Advanced: calendars whose next event is an arrival, a deadline for the comfort target. |
<!-- generated:end questions:floor_heating -->

<a name="questions_followup"></a>
## How far should preheat go?

The floor heating has no follow-up question.

## The plan

By default the floor stays off through expensive hours and through hours your home has no room for, after warming just enough beforehand to last them. It never goes under the room's lowest temperature, and never above what the covering allows. The other plans it offers are listed on [Appliances](README.md#floor_heating); each is explained on [Plans](../strategies.md).

## Everyday use

Put several floors in a group so they take turns when an hour is tight, as [Circuits, groups and rooms](../circuits-groups-rooms.md#group) explains.

## Limits

The highest floor temperature depends on the covering: wood and laminate allow less than tiles. PowerPlan sets the thermostat's own floor limit, so the floor is safe even if PowerPlan stops.

On **Heat capacitor**, the energy-saving setpoint is your comfort temperature minus 1 °C, and never under the lowest temperature you allow. A pause switches the thermostat to energy saving, so the thermostat itself keeps the floor there. When you change the comfort temperature, the energy-saving setpoint follows within 15 minutes.

> [!NOTE]
> Since v0.0.11, PowerPlan writes the thermostat's lowest floor temperature and its energy-saving setpoint while it controls the floor: at start and when the floor is added, then it checks them once a day. A setting that does not take is tried again every 15 minutes, and a [repair](../troubleshooting.md#provision_refused) says so if the thermostat keeps refusing. In trial mode nothing is written. Before v0.0.11, both stayed at the thermostat's own values.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
