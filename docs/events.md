<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Events

# Events

PowerPlan fires an event on Home Assistant's event bus whenever something changes that an automation may want to act on. The same events show on the home's **Events** entity and in the logbook.

## Every event

<!-- generated:begin events · tools/docs.py writes this block; change the event's schema, not this table -->
| Event | Shown as | Payload, besides `at`, `kind`, `schema`, `site_id` |
|---|---|---|
| `powerplan_stage_changed` | Control level changed | `blunt`, `ceiling_kwh`, `new`, `old`, `projected_kwh`, `reason` |
| `powerplan_peak_warning` | Peak warning | `advice`, `ceiling_kwh`, `cleared`, `drivers`, `expected_kwh`, `uncontrolled_share`, `window_start` |
| `powerplan_breach` | Limit exceeded | `breach`, `excess_w`, `scope`, `table` |
| `powerplan_ev_connected` | Car connected or disconnected | `connected`, `load`, `soc` |
| `powerplan_comfort_violation` | Comfort missed | `current`, `floor`, `load`, `over_allowance`, `served` |
| `powerplan_deadline_at_risk` | Deadline at risk | `deadline`, `load`, `reason`, `shortfall_kwh` |
| `powerplan_plan_adopted` | New plan adopted | `cost`, `load`, `mode`, `next_start`, `planned_kwh`, `reason` |
| `powerplan_prices_received` | Prices received | `avg`, `carrier`, `cheapest_slots`, `coverage_h`, `day`, `max`, `min`, `source` |
| `powerplan_device_unhealthy` | Appliance not responding | `failures`, `last_error`, `load`, `recovered` |
| `powerplan_level_changed` | Capacity step changed | `fee`, `metric_kw`, `new`, `old`, `projected` |
| `powerplan_period_closed` | Billing period closed | `capacity_savings`, `counterfactual_fee`, `fee`, `level`, `metric_kw`, `period` |
| `powerplan_month_closed` | Month closed | `by_load`, `capacity_savings`, `confidence`, `cost`, `energy_savings`, `month`, `savings` |
| `powerplan_legionella` | Legionella cycle | `load`, `state` |
| `powerplan_cycle` | Appliance program | `load`, `start_at`, `state` |
| `powerplan_force` | Run now | `load`, `reason`, `state` |
| `powerplan_presence_changed` | Presence changed | `new`, `old`, `source` |
| `powerplan_safe_mode` | Fallback mode | `entered`, `reason` |
| `powerplan_baseline_ready` | Normal usage learned | `confidence` |
| `powerplan_tariff_updated` | Grid tariff updated | `added`, `changed`, `fetched`, `kept`, `next_renewal`, `source` |
<!-- generated:end events -->

Every event also carries `site`, the home's name, and `entity_id`, the appliance's **Plan status** entity or the home's **Events** entity. The logbook uses `entity_id` to file the event under its appliance.

## Trigger an automation on an event

Use an event trigger with the event's name. Add `event_data` to match only some events, for example one appliance by its `load` id.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_deadline_at_risk
actions:
  - action: notify.notify
    data:
      message: "{{ trigger.event.data.load }} may not be ready: {{ trigger.event.data.reason }}"
```

To see an event's data, go to **Developer tools** > **Events**, and listen to `powerplan_plan_adopted`, for example.

## The events

<a name="stage_changed"></a>
### Control level changed

The control level moved up or down. `new` is the level now, from 0 to 4, and `blunt` is `true` when a fuse is at stake.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_stage_changed
    event_data:
      new: 4
```

<a name="peak_warning"></a>
### Peak warning

PowerPlan expects this hour to go over the target because of something it cannot hold back: the house's other usage, or an appliance that must run whatever the price (below its lowest temperature, a legionella cycle, a car below its minimum charge). What PowerPlan plans for its own appliances never raises it, because PowerPlan keeps those under the target. `drivers` lists what draws most, and `cleared` is `true` when the warning ends.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_peak_warning
    event_data:
      cleared: false
```

<a name="breach"></a>
### Limit exceeded

Usage went over a limit. `breach` is `fuse` or `trip` for the main fuse, `circuit` for a circuit's fuse, or `window` for the hour's target, and `excess_w` says by how much.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_breach
    event_data:
      breach: fuse
```

<a name="ev_connected"></a>
### Car connected or disconnected

A car was plugged in or unplugged. `connected` says which, and `soc` is the car's charge in percent when the car reports it.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_ev_connected
    event_data:
      connected: true
```

<a name="comfort_violation"></a>
### Comfort missed

An appliance is below its comfort minimum. `served` is `true` when PowerPlan is already bringing it back.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_comfort_violation
```

<a name="deadline_at_risk"></a>
### Deadline at risk

An appliance may not be ready by its time. `shortfall_kwh` is what is missing, and `reason` says why.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_deadline_at_risk
```

<a name="plan_adopted"></a>
### New plan adopted

An appliance got a new plan. `planned_kwh`, `cost`, and `next_start` describe it.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_plan_adopted
```

<a name="prices_received"></a>
### Prices received

New prices arrived for a day. `day`, `min`, `max`, and `avg` describe them, and `cheapest_slots` lists the cheapest starts.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_prices_received
```

<a name="device_unhealthy"></a>
### Appliance not responding

An appliance's device stopped answering. `recovered` is `true` when it answers again.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_device_unhealthy
    event_data:
      recovered: false
```

<a name="level_changed"></a>
### Capacity step changed

The capacity step changed. `projected` is `true` when the month is heading there, and `false` when it is reached.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_level_changed
    event_data:
      projected: false
```

<a name="period_closed"></a>
### Billing period closed

A billing period ended. `fee` is the capacity fee, and `counterfactual_fee` what it would have been without PowerPlan.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_period_closed
```

<a name="month_closed"></a>
### Month closed

A month ended. `cost`, `savings`, and `by_load` give the month's totals, per appliance too.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_month_closed
```

<a name="legionella"></a>
### Legionella cycle

A water heater's legionella cycle is `due`, `started`, `completed`, or `at_risk`.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_legionella
    event_data:
      state: at_risk
```

<a name="cycle"></a>
### Appliance program

A dishwasher, washer, or dryer program is `planned`, `started`, `finished`, or `aborted`. `start_at` is the planned start.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_cycle
    event_data:
      state: finished
```

<a name="force"></a>
### Run now

**Run now** was switched `on`, ran out (`expired`), or was `ignored`, with the `reason`.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_force
    event_data:
      state: expired
```

<a name="presence_changed"></a>
### Presence changed

Presence changed from `old` to `new`. `source` says what changed it.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_presence_changed
```

<a name="safe_mode"></a>
### Fallback mode

PowerPlan entered fallback mode, or left it. `entered` says which, and a repair explains what to do.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_safe_mode
    event_data:
      entered: true
```

<a name="baseline_ready"></a>
### Normal usage learned

PowerPlan has learned your home's normal usage well enough to plan with, for the first time. `confidence` says how well.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_baseline_ready
```

<a name="tariff_updated"></a>
### Grid tariff updated

PowerPlan fetched your grid company's tariff and it changed: `added` lists the new price periods by their start date, `changed` the corrected ones. It fires on the monthly refresh and on **Refresh the grid tariff**, only when something changed.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_tariff_updated
```

<a name="event_entity"></a>
## The Events entity

`event.<home>_events` shows the last event, with its data as attributes. Its state changes with every event, so it also works as a state trigger for any PowerPlan event. See [Entities](entities.md#events).

**See also:** [Actions](actions.md) · [Entities](entities.md)
