<!-- kind: understand -->
[PowerPlan docs](README.md) › Understand › Capacity tariffs

# Capacity tariffs

Many grid companies bill you by your highest use, not only by the energy you use. This page explains how that works and why one hour can cost you a month.

<a name="capacity_step"></a>
## What a capacity step is

Your grid company measures how much power your home uses each hour, or each quarter of an hour. At the end of the month, it takes your highest hours, for example the average of the three highest on different days, and puts you in a capacity step by that value. Each step has a monthly fee: 5 to 10 kW costs more than 2 to 5 kW.

So a single hour in which the car charges, the oven bakes and the water heater heats at once can put the whole month in a higher step.

<a name="hour_and_quarter"></a>
## The hour and the quarter

Most grid companies measure by the hour. Some, such as Belgium's, measure by the quarter of an hour, and some average over a rolling year. PowerPlan reads how your grid company measures from its tariff, and plans each window the same way.

<a name="target"></a>
## The target

The target is the capacity step you want to stay in. PowerPlan plans every hour to stay under it, and pauses what can wait when an hour heads over.

- **Automatic** keeps you in the step you have already reached this month. Early in the month that is the lowest step; after a cold week it may be higher, and there is no point saving hours that no longer count.
- A fixed step keeps you there, even if it means pausing more.

<a name="strictness"></a>
## How strict

With **Strict**, no hour goes over the target. With **Use paid hours**, an hour may reach the value you already paid for this month, since it costs nothing extra. **Flexible** allows more, as long as your highest hours stay in the step.

<a name="one_peak"></a>
## Why one peak costs a month

The fee is for the whole month, set by a few hours. That is why PowerPlan pauses the car for ten minutes rather than let one hour through: the ten minutes cost nothing, and the hour could cost the month's difference between two steps.

**See also:** [Grid tariffs](tariffs.md) · [How it works](how-it-works.md) · [Glossary](glossary.md)
