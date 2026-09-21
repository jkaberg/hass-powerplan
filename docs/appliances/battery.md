<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Home battery

# Home battery

A home battery PowerPlan discharges when the home would go over your target, and charges when there is room.

## What it needs

A battery Home Assistant can set the charge and discharge power of, and its charge level.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

PowerPlan shows which entity it will control and which it only reads. Check them before you go on.

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

<!-- generated:begin questions:battery · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Battery capacity | The car's battery in kWh, from its spec sheet. |
| Maximum charge | The battery's maximum charging power, in kW. |
| Maximum discharge | The battery's maximum discharging power, in kW. |
| Reserve | The share kept back for an outage. |
| Charge from the grid | Whether the battery may charge from the grid in cheap hours. |
| Chemistry | The cell chemistry. |
| State of charge sensor | The car's own charge level sensor, if any; without it, the plan is by time. |
| Power sensor | A sensor measuring this appliance's power, if there is one. |
| Advanced › Maximum charge | Advanced: the state of charge the battery is charged to at most. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:battery -->

<a name="questions_followup"></a>
## How far should preheat go?

The home battery has no follow-up question.

## The plan

By default the battery shaves your peaks on a capacity tariff. With a spot price, it can instead buy low and sell high. The other plans it offers are listed on [Appliances](README.md#battery); each is explained on [Plans](../strategies.md).

## Everyday use

Set the reserve it always keeps for a power cut under **Reserve**.

## Limits

PowerPlan charges from the grid only if you allow it. Solar and the battery together are planned in a later version.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
