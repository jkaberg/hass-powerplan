<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Dishwasher, washer or dryer

# Dishwasher, washer or dryer

A dishwasher, washer or dryer PowerPlan starts once, in the cheapest window that finishes before the time you set.

## What it needs

An appliance with remote start in Home Assistant, such as Home Connect or Miele.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

PowerPlan shows which entity it will control and which it only reads. Check them before you go on.

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

<!-- generated:begin questions:appliance_cycle · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Appliance | What kind of appliance this is. |
| Ready by | When the hot water or the cycle must be ready. |
| Start control | How the appliance is started. |
| Power sensor | A sensor measuring this appliance's power, if there is one. |
| Advanced › Energy per cycle | Advanced: kWh one program uses. |
| Advanced › Duration | Advanced: minutes one program runs. |
| Power | The appliance's power draw, in watts. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:appliance_cycle -->

<a name="questions_followup"></a>
## How far should preheat go?

The dishwasher, washer or dryer has no follow-up question.

## The plan

You fill it and press start as usual. PowerPlan picks when the program runs, and it is done by your ready-by time. The other plans it offers are listed on [Appliances](README.md#appliance_cycle); each is explained on [Plans](../strategies.md).

## Everyday use

Set when it must be done under **Ready by**. **Run now** starts it at once.

## Limits

PowerPlan cannot pause a program once it runs. Without remote start, it can only tell you the best time.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
