<!-- kind: index -->

# PowerPlan documentation

PowerPlan runs your home's big appliances when power is cheap, and keeps your home inside the capacity step you choose on your grid bill. These pages explain how to set it up, what each screen means, and what to do when something looks wrong.

## What PowerPlan does for a home

- **Cheaper hours.** The car charger, the water heater, floor heating and other big appliances run in the cheapest hours of the day, and are still ready when you need them.
- **A lower grid fee.** Where your grid company bills by capacity step, PowerPlan keeps the hour's usage under the step you choose, and pauses what can wait.
- **A safe main fuse.** PowerPlan turns appliances down before the whole home draws more than the main fuse can carry.

PowerPlan starts in trial mode. It shows what it would do, and changes nothing until you switch on **Automatic control**.

## What it needs

- Home Assistant and HACS, as [Install](install.md) lists them.
- A power reading from your electricity meter, for example a HAN or P1 reader, to protect the fuse and the capacity step.
- Electricity prices in Home Assistant, for example from the Nord Pool integration, to find the cheap hours.
- Appliances you can already switch or set from Home Assistant.

## I want to…

| I want to… | Read |
|---|---|
| install, update, or remove PowerPlan | [Install](install.md) |
| set up PowerPlan for the first time | [Get started](get-started.md) |
| understand a question in the setup | [Set up a home](setup.md) |
| add a circuit, a group of appliances, or a room | [Circuits, groups and rooms](circuits-groups-rooms.md) |
| see which appliances PowerPlan steers, and how | [Appliances](appliances/README.md) |
| choose a plan for an appliance | [Plans](strategies.md) |
| check whether my charger or thermostat works | [Devices](devices.md) |
| use a price sensor I already have | [Prices](prices.md) |
| use PowerPlan day to day | [Daily use](daily-use.md) |
| understand why an appliance ran or waited | [How it works](how-it-works.md) |
| understand capacity steps and the target | [Capacity tariffs](capacity-tariffs.md) |
| understand the cost and savings figures | [Savings](savings.md) |
| fix a repair or a problem | [Troubleshooting](troubleshooting.md) |
| know what PowerPlan does not do yet | [Limitations](limitations.md) |
| copy an automation | [Examples](examples.md) |
| know what a word on a screen means | [Glossary](glossary.md) |
| check whether PowerPlan fetches my grid company's tariff | [Grid tariffs](tariffs.md) |
| use PowerPlan's entities in a card or an automation | [Entities](entities.md) |
| start or pause an appliance from an automation | [Actions](actions.md) |
| react when PowerPlan changes something | [Events](events.md) |
| add PowerPlan's dashboard | [Dashboard](dashboard.md) |

## The version these pages describe

These pages describe the newest code on `main`. What changed in each version is on the [releases page](https://github.com/jkaberg/hass-powerplan/releases), and a section whose behavior changed says since which version.

**See also:** the [design documents](../design/README.md), for contributors.
