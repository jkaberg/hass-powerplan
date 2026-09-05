<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Actions

# Actions

The actions PowerPlan adds to Home Assistant, for scripts, automations, and **Developer tools** > **Actions**. Each one acts on one home, or on every home when you leave `site` out.

## Every action

<!-- generated:begin actions · tools/docs.py writes this block; change services.yaml or strings.json, not this table -->
| Action | Name | What it does | Fields |
|---|---|---|---|
| `powerplan.replan` | Replan | Fetch what is missing and run a planning cycle now. | `site` |
| `powerplan.rebuild_baseline` | Relearn normal usage | Forget the learned normal usage per hour and learn it again from the history now. | `site` |
| `powerplan.release` | Resume | Let an appliance PowerPlan paused run again, and leave its mode as it is. | `load` (required), `site` |
| `powerplan.boost` | Run now for a while | Turn on Run now for an appliance for a while. | `load` (required), `hours`, `site` |
| `powerplan.run_now` | Run now | Start an appliance's cycle now, capacity permitting. | `load` (required), `site` |
| `powerplan.set_presence` | Set presence | Say who is home, optionally until a time. | `mode` (required), `until`, `site` |
| `powerplan.reset_window_anchor` | Restart this hour's count (emergency) | Start counting this hour again from the meter's current reading. For emergencies only. | `site` |
| `powerplan.set_peak` | Correct a peak | Correct one day's or one month's peak in the grid-fee history. | `date`, `month`, `kw` (required), `note`, `site` |
| `powerplan.dump_state` | Make a bug report | Return the last snapshot and the assembled inputs for a bug report. | `site` |
<!-- generated:end actions -->

`site` takes the home's name as you gave it, for example `YOUR_HOME`. `load` takes the appliance's id: the `load` value in its events, which [Events](events.md) shows how to see.

<a name="replan"></a>
## Replan

Fetches what is missing, such as prices, and plans every appliance again now. PowerPlan also plans on its own when prices arrive and when something changes.

```yaml
action: powerplan.replan
data:
  site: YOUR_HOME
```

<a name="rebuild_baseline"></a>
## Relearn normal usage

Forgets the normal usage PowerPlan learned hour by hour, and learns it again from the recorder's history now. Use it after a big change in the home, such as a new heat pump or a new tenant.

```yaml
action: powerplan.rebuild_baseline
data:
  site: YOUR_HOME
```

<a name="release"></a>
## Resume

Lets an appliance that PowerPlan paused run again now. Its **Control** stays as it is, so the plan may pause it again later.

```yaml
action: powerplan.release
data:
  load: YOUR_APPLIANCE_ID
```

<a name="boost"></a>
## Run now for a while

Turns on **Run now** for an appliance, whatever the price, for `hours`. Without `hours`, it runs for the appliance's **Run now for at most**. PowerPlan still keeps the home under the main fuse.

```yaml
action: powerplan.boost
data:
  load: YOUR_APPLIANCE_ID
  hours: 2
```

<a name="run_now"></a>
## Run now

Starts a dishwasher, washer, or dryer program now instead of at the planned time, if there is capacity for it.

```yaml
action: powerplan.run_now
data:
  load: YOUR_APPLIANCE_ID
```

<a name="set_presence"></a>
## Set presence

Sets **Presence** to `auto`, `home`, `away`, or `vacation`. With `until`, it goes back to `auto` at that time.

```yaml
action: powerplan.set_presence
data:
  mode: vacation
  until: "2026-10-12 16:00:00"
```

<a name="reset_window_anchor"></a>
## Restart this hour's count (emergency)

Starts counting this hour's usage again from the meter's current reading. Use it only when the count is plainly wrong, for example after the meter was replaced mid-hour.

```yaml
action: powerplan.reset_window_anchor
data:
  site: YOUR_HOME
```

<a name="set_peak"></a>
## Correct a peak

Replaces one day's or one month's peak in the history your capacity step is counted from. Give `date` or `month`, not both, and a `note` that says why.

```yaml
action: powerplan.set_peak
data:
  date: "2026-09-14"
  kw: 4.2
  note: The meter reported a spike the grid company did not bill
```

<a name="dump_state"></a>
## Make a bug report

Returns what PowerPlan knows right now: the last result, the inputs it used, and its saved data. Attach the response to an issue, together with the diagnostics you can download from PowerPlan's page.

```yaml
action: powerplan.dump_state
data:
  site: YOUR_HOME
response_variable: report
```

**See also:** [Events](events.md) · [Entities](entities.md)
