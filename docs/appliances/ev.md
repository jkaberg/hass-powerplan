<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Car charger

# Car charger

A car charger PowerPlan starts, stops and turns up or down, so the car is charged by the time you leave, in the cheapest hours.

## What it needs

A charger Home Assistant can set the current of, such as Easee, Zaptec or one through OCPP; [Devices](../devices.md) lists them. A sensor for the car's charge level makes the plan exact; without one, PowerPlan plans by time.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

PowerPlan shows which entity it will control and which it only reads. Check them before you go on.

## Data points

What PowerPlan reads and controls on this appliance, and what it does without each one. The [data points step](README.md#data-points) finds them on the device and asks for the rest.

<!-- generated:begin data_points:ev · tools/docs.py writes this block; change the needs or strings.json, not this table -->
| Data point | Needed | What it is for | Without it |
|---|---|---|---|
| Charging current | Required | The charging current PowerPlan sets. | PowerPlan cannot control the charging. |
| Status | Required | What the charger says it is doing. | PowerPlan cannot tell when the car is plugged in. |
| Charging allowed | Optional | The switch that lets the car charge or not. | Charging is paused by lowering the current. |
| State of charge | Recommended | The battery's charge level, in percent. | The plan charges the energy you ask for instead of to a level. |
| Power | Recommended | What the appliance draws right now, in watts. | Charging is counted from the current instead. |
| Energy this session | Optional | The energy charged this session. | Each session is counted from the power. |
| Current L1 | Optional | The current on phase 1. | The number of phases stays your answer. |
| Current L2 | Optional | The current on phase 2. | The number of phases stays your answer. |
| Current L3 | Optional | The current on phase 3. | The number of phases stays your answer. |
| Circuit maximum | Optional | The highest current the circuit allows. | The highest current stays your answer. |
| Cable rating | Optional | The highest current the cable allows. | The highest current stays your answer. |
| Maximum charging current | Optional | The charger's own current limit. | The highest current stays your answer. |
| Blocked by | Optional | Why the charger is not charging, when it says. | A pause by the car or the charger is not explained. |
| Energy | Optional | The appliance's energy meter, in kWh. | Energy is counted from the power instead. |
<!-- generated:end data_points:ev -->

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

<!-- generated:begin questions:ev · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Battery capacity | The car's battery in kWh, from its spec sheet. |
| Charger maximum | The most current the charger can deliver, in amps. |
| Phases | How many phases the charger uses. |
| Charge to | The charge level every session aims for; most cars recommend 80 % daily. |
| Never below | Under this the car charges at once whatever the price says. |
| Advanced › Charging efficiency | Advanced: the share of grid energy that ends up in the battery. |
| Advanced › Minimum current | Advanced: the lowest current the car keeps charging at before it stops. |
| Advanced › Ramp step | Advanced: how many amps the limit rises per write on the way up. |
| Advanced › Settle time | Advanced: seconds after a write before the reading is trusted. |
| Advanced › Write threshold | Advanced: a change smaller than this is not written. |
| Advanced › Rewrite after | Advanced: seconds after which the same value is written again. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
| Advanced › Departure calendar | Advanced: a calendar whose next event is a departure, earlier than the weekday table. |
<!-- generated:end questions:ev -->

<a name="questions_followup"></a>
## How far should preheat go?

The car charger has no follow-up question.

## The plan

By default the car charges in the cheapest hours before your next departure, and at once below the charge level you set as **Never below**. The other plans it offers are listed on [Appliances](README.md#ev); each is explained on [Plans](../strategies.md).

## Everyday use

Set when you leave each weekday under **Departures**, or bind a calendar whose events are departures. **Run now** charges at full power for a while, whatever the price.

## Limits

PowerPlan never sets the current below 6 A while a car is charging, because most cars stop there. A charger that is also controlled by evcc or the car's own app should stay in trial mode.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
