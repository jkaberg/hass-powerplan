<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Circuits, groups and rooms

# Circuits, groups and rooms

Three ways to tell PowerPlan how your appliances belong together: behind one fuse, sharing power in turns, or heating the same room. Add each one from your home's page, after the appliances it names.

| You have | Add a | PowerPlan then |
|---|---|---|
| Appliances behind their own fuse, such as the garage's | [Circuit](#circuit) | keeps them together under that fuse, and under the main fuse |
| Appliances that can take turns, such as the floors of one bathroom | [Group](#group) | shares a set amount of power between them when an hour is tight, coldest first |
| A room with two heat sources, such as a heat pump and a heated floor | [Room](#room) | heats it with the cheaper one |

To add one, go to **Settings** > **Devices & services** > **PowerPlan**, open your home, and select **Add circuit**, **Add group**, or **Add room**. To change one later, select **Change setup** next to it.

<a name="circuit"></a>
## Which appliances share a circuit?

A circuit is a fuse in your fuse box and the appliances behind it, such as the garage's 32 A with the car charger and the sauna. PowerPlan keeps the appliances on it under that fuse together, before it considers the main fuse. The fuse and the number of phases are on the fuse itself; count a three-phase fuse as three phases.

If the circuit has its own power sensor, pick it as **Sub-meter**: PowerPlan then sees everything on the circuit, including what it does not steer. Without one, the circuit's use is the sum of its appliances. Under **Advanced**, keep some power clear for things on the circuit PowerPlan neither steers nor measures, such as a freezer.

<!-- generated:begin fields:circuit.user · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Name | What the circuit is called: the garage, the outbuilding, the charger feed. |
| Fuse | The circuit's own fuse, in amps. |
| Phases | How many phases the circuit is wired on; the fuse is per phase. |
| Appliances on this circuit | The appliances PowerPlan steers that sit behind this fuse. |
| Sub-meter | Optional: a power sensor for the whole circuit; without one, its appliances are summed. |
| Advanced › Unmeasured use | kW kept clear for what PowerPlan neither steers nor measures on the circuit, usually 0. |
<!-- generated:end fields:circuit.user -->

> [!WARNING]
> The circuit's fuse is a limit PowerPlan never plans past. Enter the fuse that is really there: a value too high lets the appliances trip it.

<a name="group"></a>
## Which appliances should take turns?

A group is appliances that can wait for each other, such as six floor loops. When an hour gets tight, PowerPlan shares a set amount of power between them instead of turning them all down. It serves the coldest first and rotates, so none is left behind for long.

Set how much power the group may use at once. By default, sharing starts when the hour's expected use nears its target; under **Advanced**, choose when sharing starts and how many may run together.

<!-- generated:begin fields:group.user · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Name | What the group is called: the floor loops, the bedroom radiators. |
| Appliances in this group | The appliances that share power and take turns for it. |
| Advanced › From control level | The control level at which the group starts sharing. |
| Advanced › Share of this hour's target | The hour's expected use, in percent of its target, at which the group shares anyway. |
| Advanced › Starvation timeout | How long an appliance may wait before it goes to the front of the queue. |
<!-- generated:end fields:group.user -->

<a name="room"></a>
## Which heat sources share a room?

A room is two or more appliances that heat the same space, such as a heat pump and a heated floor, or a heat pump and a boiler. PowerPlan heats the room with whichever gives heat cheapest this hour, counting each heat source's price and the heat pump's efficiency. When the hour's target is at risk, it prefers the heat pump, because it gives more heat per kWh.

The **Advanced** settings keep the choice steady: the heat pump is never chosen below the lowest efficiency you set, and PowerPlan switches only when the other heat source is cheaper by the margin you set.

<!-- generated:begin fields:zone.user · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Name | What the room is called: the living room, the bathroom. |
| Appliances in this room | The room's heating appliances; each brings its heat source and efficiency from its setup. |
| Advanced › Lowest efficiency (COP) | Never engage a heat pump below this COP. |
| Advanced › Switch margin | How much cheaper, in percent, the other source must be before switching. |
| Advanced › Minimum dwell | How long the running heater keeps the room before another may take over. |
| Advanced › Switch confirmation | How long a cheaper source must stay cheaper before the switch is taken. |
| Advanced › Capacity penalty | Extra cost per kWh of electric heat while the hour's target is at risk. |
<!-- generated:end fields:zone.user -->

<a name="never_substitute"></a>
## Should any of these always keep their own source?

Pick an appliance here if it must keep heating whatever the comparison says, such as a bathroom floor that should never wait for the heat pump. PowerPlan still steers it by price; it just never swaps it for another heat source.

<!-- generated:begin fields:zone.never_substitute · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Never substitute | Appliances that always keep their own heat source. |
<!-- generated:end fields:zone.never_substitute -->

**See also:** [Set up a home](setup.md) · [Glossary](glossary.md)
