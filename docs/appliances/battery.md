<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Home battery

# Home battery

A home battery PowerPlan discharges when the home would go over your target, and charges when there is room.

## What it needs

A battery Home Assistant can set the charge and discharge power of, and its charge level.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

A battery steered through a YAML package, such as Sungrow's Modbus package, is on no device: choose **Not on a device: choose the entities** and pick the package's entities.

<a name="match"></a>
## Is this the right device?

Shown only when PowerPlan knows more than one way of talking to the device. Choose one; the best fit is already selected. Which entity PowerPlan reads and controls is checked on the next step.

## Data points

What PowerPlan reads and controls on this appliance, and what it does without each one. The [data points step](README.md#data-points) finds them on the device and asks for the rest.

<!-- generated:begin data_points:battery · tools/docs.py writes this block; change the needs or strings.json, not this table -->
| Data point | Needed | What it is for | Without it |
|---|---|---|---|
| Battery power (setting) | Required when PowerPlan sets the power or current | The power PowerPlan asks the battery to charge or discharge at. | PowerPlan cannot control the battery. |
| State of charge | Required | The battery's charge level, in percent. | PowerPlan cannot plan the battery. |
| Power | Recommended | What the appliance draws right now, in watts. | The battery's own reading of a command is not checked. |
| Energy | Optional | The appliance's energy meter, in kWh. | Energy is counted from the power instead. |
<!-- generated:end data_points:battery -->

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

<!-- generated:begin questions:battery · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Battery capacity | The battery's capacity in kWh, from its spec sheet. |
| Maximum charge | The battery's maximum charging power, in kW. |
| Maximum discharge | The battery's maximum discharging power, in kW. |
| Reserve | The share kept back for an outage. |
| Charge from the grid | Whether the battery may charge from the grid in cheap hours. |
| Chemistry | The cell chemistry. |
| Advanced › Highest charge | Advanced: the most the battery is charged to; blank uses the chemistry's. |
| Advanced › Wear cost | Advanced: what each kWh delivered wears the battery, in your currency; blank uses the chemistry's. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:battery -->

<a name="questions_followup"></a>
## How far should preheat go?

The home battery has no follow-up question.

## The plan

By default the battery shaves your peaks on a capacity tariff. With a spot price, it can instead buy low and sell high. The other plans it offers are listed on [Appliances](README.md#battery); each is explained on [Plans](../strategies.md).

## Everyday use

Set the reserve it always keeps for a power cut under **Reserve**.

Under **Advanced** you can change two numbers that PowerPlan works out from the chemistry you chose. The **Wear cost** is what each kWh the battery delivers costs you in wear. It is about 0.04 EUR for LFP and 0.10 EUR for NMC, converted to your currency once, when you add the battery. The **Highest charge** is how full the battery is charged: 100 % for LFP and 90 % for NMC, whose cells age faster at a high charge. A number you type stays.

> [!NOTE]
> Since v0.0.14, the **Wear cost** replaces the old minimum gain per kWh. A battery you added before keeps a number you typed there, and gets the chemistry's number otherwise.

## Limits

Between the hours its plan charges or discharges it, the battery runs on its own, as its inverter does without PowerPlan. When the plan needs the charge later, for the evening or for a peak, PowerPlan holds it: the battery then stops discharging, while solar power may still fill it.

PowerPlan charges the battery from the grid only in the hours its plan chooses, and only if you allow grid charging. At any other time it charges only from solar power your home would otherwise send to the grid. With solar panels, the plan stores the midday surplus for the evening when your evening price is worth more than selling the surplus now. It sells when that is not the case. When the battery covers the evening, it covers only what your home would buy, never more.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
