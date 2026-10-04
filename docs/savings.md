<!-- kind: understand -->
[PowerPlan docs](README.md) › Understand › Savings

# Savings

How PowerPlan counts what you paid, and what it saved you. The figures are on the home's device and each appliance's device, and on the dashboard.

<a name="cost"></a>
## Cost

Each hour, PowerPlan multiplies the energy the home used by that hour's price, with the grid tariff, taxes and VAT, and adds the capacity fee. **Cost this month** is what your bills should add up to, split by who you pay: your grid company, your supplier and the state.

In the month PowerPlan starts, or after its books are reset, the energy is only counted from that day, while the capacity fee is the whole month's. The sensor then has `partial: true`, and `energy_since` says from when; the dashboard says the same under the month's cost. An appliance's `settled_cost` is the part of its cost its savings are compared with, since the latest hours aren't settled yet.

<a name="savings"></a>
## Savings

Savings compare what you paid with what the same energy would have cost without PowerPlan. For each appliance, PowerPlan takes the energy it really used and prices it as if the appliance had run when it would have without PowerPlan:

- a car from the moment it was plugged in, at full power;
- a thermostat, a floor or a water heater spread evenly over the day;
- a dishwasher from the moment you asked for it;
- a home battery as its inverter would have run it on its own: charging from the solar power your home would otherwise sell, and covering what your home would otherwise buy, never from the grid. A battery PowerPlan sets through a plain number has no mode of its own, and is compared with a battery that stands still.

The difference is the saving. A day's savings are added after midnight, and a car's when its charge is done. In trial mode PowerPlan steers nothing, so it saves nothing.

With solar panels, the solar power an appliance used is priced at what you would have been paid for selling it, and the rest at your price for buying. The same rule prices the house without PowerPlan, which has the same panels. A battery's cost is the solar power it stored, at the selling price, less what it covered in the evening, at the buying price. Your home's total cost does not change; only its split between the appliances does.

<a name="step_below"></a>
## The step below

Each month PowerPlan also works out the capacity step your home would have reached if the appliances it steers had spread their energy evenly over each day. It is the best they could have done, not a promise: a car that isn't home can't charge at noon, and a water heater still has to be hot in the morning. When that step is lower than the one you reached, **Recommendation** says so for the following month, with what it would save. You decide whether to choose it as your target.

<a name="confidence"></a>
## Confidence

Savings are only as good as the comparison. When an appliance has nothing to compare with, its saving is unknown rather than zero. PowerPlan also learns a model of each appliance; when the model has proved accurate, it shows a second figure, **model savings**, with its confidence.

If a saving looks wrong for a week, a repair says so, and the figure improves as PowerPlan learns.

**See also:** [How it works](how-it-works.md) · [Entities](entities.md)
