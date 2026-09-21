<!-- kind: understand -->
[PowerPlan docs](README.md) › Understand › How it works

# How PowerPlan works

What PowerPlan decides, in which order, and how often. Read this to understand why an appliance ran, waited or was paused.

<a name="priorities"></a>
## The order of priorities

When PowerPlan decides what each appliance may use, it asks five questions, always in this order:

1. **Limits.** The main fuse and each circuit's fuse are never exceeded. This comes before everything, whatever the price or the plan.
2. **The target.** The hour stays under the capacity step you chose, by pausing what can wait.
3. **Comfort.** No room falls below its lowest temperature, no tank below its lowest, and no car misses its departure if it can be avoided.
4. **The plan.** Within that, each appliance runs in the hours its plan chose, the cheapest ones.
5. **Your wishes.** **Run now**, priorities and presence decide the rest.

So a cheap hour never pushes the home over its fuse, and a plan never pauses a room below its lowest temperature.

<a name="plan"></a>
## The plan and the moment

PowerPlan works on two clocks.

- **The plan** is made every quarter of an hour, and when prices, presence or an appliance change. It looks up to two days ahead and picks, for each appliance, the hours it should run in. It uses tomorrow's prices as soon as they are published, and estimates them until then.
- **The moment** is checked every few seconds, from the meter. If the hour is heading over the target, PowerPlan pauses appliances now, starting with the lowest priority, and lets them run again when there is room.

The plan says what would be cheapest. The moment makes sure the home stays safe while the plan runs.

<a name="never"></a>
## What PowerPlan never does

- It never turns off a heat pump's power, or pauses one while it defrosts.
- It never lets a floor, a room or a tank go below the lowest temperature you set.
- It never writes to an appliance in trial mode, or while **Automatic control** is off.
- It never sends your data anywhere, except your postcode to your country's address register and the requests to fetch your grid tariff.

<a name="data_update"></a>
## How the data updates

| What | When |
|---|---|
| The meter's power and reading | as fast as your meter reports, usually every few seconds |
| Decisions for the moment | at most every few seconds, and at every hour change |
| The plan | every quarter of an hour, and when something changes |
| Tomorrow's prices | when the market publishes them, usually early afternoon |
| Your grid tariff | once a month, and with **Refresh the grid tariff** |
| Normal usage and each appliance's model | learned from the history every night |

If the meter stops reporting, PowerPlan waits instead of guessing, and a repair says so. If prices are missing, it plans on estimated prices and says so on the price entities.

**See also:** [Capacity tariffs](capacity-tariffs.md) · [Savings](savings.md) · [Glossary](glossary.md)
