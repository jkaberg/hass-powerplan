<!-- kind: guide -->
[PowerPlan docs](README.md) › Guides › Examples

# Examples

Automations and scripts people ask for. Copy one into **Settings** > **Automations & scenes**, then change the entity ids to yours.

<a name="charge_now_button"></a>
## Charge the car now from a button

Starts **Run now** on the car charger for two hours.

```yaml
action: powerplan.boost
data:
  load: YOUR_APPLIANCE_ID  # the car charger's id, as in Actions
  hours: 2
```

<a name="away_when_leaving"></a>
## Go away when the house is locked

Sets presence to away until you come back.

```yaml
triggers:
  - trigger: state
    entity_id: lock.front_door
    to: locked
actions:
  - action: powerplan.set_presence
    data:
      mode: away
```

<a name="peak_warning_light"></a>
## Turn a light red on a peak warning

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.home_peak_warning
    to: "on"
actions:
  - action: light.turn_on
    target:
      entity_id: light.kitchen
    data:
      color_name: red
```

<a name="plan_again_on_prices"></a>
## Plan again when tomorrow's prices arrive

PowerPlan does this by itself; this shows how to react to the same moment.

```yaml
triggers:
  - trigger: event
    event_type: powerplan_prices_received
actions:
  - action: notify.mobile_app_phone
    data:
      message: Tomorrow's prices are in.
```

**See also:** [Actions](actions.md) · [Events](events.md) · [Entities](entities.md)
