<!-- kind: help -->
[PowerPlan docs](README.md) › Help › Glossary

# Glossary

The words PowerPlan's screens and these pages use, one word for each idea. The last column is the word in entity ids, action fields, and event data, for automations.

| Word | Meaning | In automations |
|---|---|---|
| <a name="home"></a>Home | Everything behind one electricity meter. PowerPlan plans each home on its own. | `site` |
| <a name="appliance"></a>Appliance | One thing PowerPlan may steer: a car charger, a water heater, a floor, a heat pump. | `load` |
| <a name="circuit"></a>Circuit | Appliances behind one fuse in the fuse box, such as the garage's. PowerPlan keeps them under that fuse. | `circuit` |
| <a name="group"></a>Group | Appliances that share power and take turns, such as the floors of one bathroom. | `group` |
| <a name="room"></a>Room | A room with more than one heat source, such as a heat pump and a floor. PowerPlan heats it with the cheaper one. | `zone` |
| <a name="capacity_step"></a>Capacity step | The band your grid company bills you by, set by your highest hours in the month. | `level` |
| <a name="target"></a>Target | The capacity step you want to stay in. PowerPlan plans to keep every hour under it. | `target` |
| <a name="limit"></a>Limit | What must never be exceeded: the main fuse, or a circuit's fuse. | `limit` |
| <a name="paused"></a>Paused | PowerPlan has turned an appliance off or down for now, to stay under the target or a limit. | `shed` |
| <a name="run_now"></a>Run now | You start an appliance yourself, for a while, whatever the price. | `force`, `boost` |
| <a name="trial_mode"></a>Trial mode | PowerPlan shows what it would do and changes nothing. A new home and a new appliance start in it. | `observe` |
| <a name="fallback_mode"></a>Fallback mode | PowerPlan hit an error and controls nothing until you look. A repair says what to do. | `safe_mode` |
| <a name="normal_usage"></a>Normal usage | What your home draws hour by hour besides the appliances PowerPlan steers, learned from its history. | `baseline` |
| <a name="heat_source"></a>Heat source | What a room's heat comes from: electricity, gas, district heating, oil, or pellets. | `carrier` |
| <a name="price_add_on"></a>Price add-on | A part of your price besides the spot price: VAT, taxes, the supplier's markup, the grid's day and night rates. | `modifier` |
| <a name="efficiency"></a>Efficiency (COP) | How much heat a heat pump gives for each kWh of power. It falls when it is cold outside. | `cop` |

**See also:** [Entities](entities.md) · [Actions](actions.md) · [Events](events.md)
