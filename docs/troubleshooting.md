<!-- kind: help -->
[PowerPlan docs](README.md) › Help › Troubleshooting

# Troubleshooting

What each repair PowerPlan raises means and what to do about it, and the common symptoms. A repair appears under **Settings** > **Repairs**, and its **Learn more** link opens its section here.

<!-- generated:begin repairs · tools/docs.py writes this block; change repairs.py or strings.json, not this table -->
| Repair | Offers a fix | Key |
|---|---|---|
| The grid meter stopped reporting | no | `meter_stale` |
| No register report for a day | no | `register_missing` |
| Power and register disagree | no | `scaling_mismatch` |
| Electricity prices are missing | no | `price_source_dead` |
| The tariff preset has changed | no | `preset_outdated` |
| Confirm a tariff value you set | yes | `tariff_review` |
| The grid tariff has run out | no | `tariff_stale` |
| A schedule, person or calendar is gone | yes | `bound_helper_missing` |
| A required entity is gone | yes | `role_missing` |
| A device refuses its provisioning | no | `provision_refused` |
| Legionella cycle at risk | no | `legionella_at_risk` |
| PowerPlan is in fallback mode | yes | `engine_failing` |
| PowerPlan's saved data was reset | no | `store_reset` |
| An appliance is not answering | no | `delegated_idle` |
| Savings figure is uncertain | no | `savings_low_confidence` |
| An appliance is on hold | no | `load_error` |
| A battery is not steered | no | `battery_control_off` |
| Notification service missing | no | `notify_service_missing` |
| Electricity prices are not up to date | no | `prices_stale` |
| An appliance's device is gone | no | `device_missing` |
| A PV forecast cannot be read | no | `pv_forecast_unavailable` |
<!-- generated:end repairs -->

## Repairs

<a name="battery_control_off"></a>
### A battery is not steered

PowerPlan cannot read or change the battery's controls in its inverter's integration. Some integrations ship their controls switched off: SolarEdge Modbus Multi until **Power Control Options** is on, Fronius until **Inverter control via Modbus** is on in the inverter, Sigenergy until its read-only mode is off, and Marstek until its control entities are enabled. The repair names the setting. Until then the battery runs on its own, as it did before PowerPlan.

**What to do:** Switch on the setting the repair names, then reload the integration. The repair clears by itself once PowerPlan reads the battery's controls again.

<a name="engine_failing"></a>
### PowerPlan is in fallback mode

PowerPlan hit the same error several times in a row. It stopped steering and left every appliance as it was: this is fallback mode.

**What to do:** Restart Home Assistant. If fallback mode comes back, turn on debug logging for PowerPlan, download the diagnostics from the home's device, and report it with the log.

<a name="load_error"></a>
### An appliance is on hold

One appliance ran into an error, so PowerPlan holds it as it is while the others carry on.

**What to do:** Check that the appliance's device answers in Home Assistant: open it and try to change it by hand.

<a name="meter_stale"></a>
### The grid meter stopped reporting

The power reading has not changed for more than ten minutes. PowerPlan cannot see the home, so it waits instead of guessing.

**What to do:** Check that the meter's integration is running and its device is online. PowerPlan carries on by itself when the reading returns.

<a name="register_missing"></a>
### No register report for a day

The meter reading, the kWh counter, has not reported for a day. PowerPlan counts from the power reading meanwhile.

**What to do:** Check the meter's integration. Some readers report the meter reading only once an hour; one missed report is normal.

<a name="scaling_mismatch"></a>
### Power and register disagree

For six hours, the power reading and the meter reading have disagreed by more than 5 %. Usually one sensor reports in the wrong unit, such as W where kW is meant.

**What to do:** Open the home's **Reconfigure** and check the unit of each meter sensor.

<a name="price_source_dead"></a>
### Electricity prices are missing

Your price source has delivered nothing for a day. PowerPlan plans on estimated prices meanwhile.

**What to do:** Check that the price integration is running. For Nord Pool, check that its integration is loaded and has your area.

<a name="prices_stale"></a>
### Electricity prices are not up to date

PowerPlan has had no known price for a while and plans on estimates. It retries by itself.

**What to do:** If it lasts, check that the price integration is loaded. **Fetch prices again** on the home tries at once.

<a name="pv_forecast_unavailable"></a>
### A PV forecast cannot be read

Home Assistant's energy dashboard names a solar forecast, but PowerPlan could not read it. PowerPlan plans without a solar forecast until it can.

**What to do:** Check that your solar forecast integration, such as Forecast.Solar, is loaded and shows a forecast on the energy dashboard.

<a name="preset_outdated"></a>
### The tariff preset has changed

The grid tariff your home was set up with has changed. Your home keeps the old one until you choose.

**What to do:** Open **Reconfigure** on the home and choose your grid company again to take the new tariff.

<a name="tariff_stale"></a>
### The grid tariff has run out

Your grid tariff has no prices after its last period, and fetching a new one keeps failing. PowerPlan keeps using the last prices.

**What to do:** Check the internet connection. **Refresh the grid tariff** on the home tries again; or open **Reconfigure** and choose the tariff again.

<a name="tariff_review"></a>
### Confirm a tariff value you set

A tariff value you set yourself differs from what PowerPlan would use for your country or your grid company. It is kept as yours.

**What to do:** Select **Submit** in the repair to keep it, or open **Reconfigure** to change it.

<a name="bound_helper_missing"></a>
### A schedule, person or calendar is gone

A schedule, a person or a calendar your home or an appliance uses no longer exists.

**What to do:** Open **Reconfigure**, or the appliance's **Change setup**, and pick a new one.

<a name="role_missing"></a>
### A required entity is gone

An entity an appliance needs, such as its power sensor or its switch, no longer exists.

**What to do:** Open the appliance's **Change setup** and pick the entity again.

<a name="device_missing"></a>
### An appliance's device is gone

The device an appliance was set up with is gone from Home Assistant. PowerPlan does not control the appliance until you pick a new one.

**What to do:** Open PowerPlan, find the appliance and choose **Change setup**, then pick the device that replaced it.

<a name="provision_refused"></a>
### A device refuses its provisioning

A device keeps refusing a setting PowerPlan needs, such as a thermostat's floor limit.

**What to do:** Check that the device is online and accepts changes from Home Assistant.

<a name="legionella_at_risk"></a>
### Legionella cycle at risk

The water heater cannot finish its legionella heating in time.

**What to do:** Check that the water heater is on and reachable. PowerPlan tries again in the next cheap hours.

<a name="delegated_idle"></a>
### An appliance is not answering

An appliance that something else controls has not changed for a day.

**What to do:** Check the automation or integration that controls it, or set its **Control** back to **Automatic**.

<a name="savings_low_confidence"></a>
### Savings figure is uncertain

For a week, PowerPlan's estimate of what the appliance would have used without it has missed, so its savings are uncertain.

**What to do:** Nothing to do: the estimate improves as PowerPlan learns.

<a name="notify_service_missing"></a>
### Notification service missing

The notify service you picked does not exist, so notices appear in Home Assistant instead.

**What to do:** Open **Reconfigure** on the home and pick another notify service.

<a name="store_reset"></a>
### PowerPlan's saved data was reset

PowerPlan could not read its saved data, moved it aside and rebuilt the current hour from the meter.

**What to do:** Nothing to do. If you report it, attach the file the repair names.

## When setup stops

<a name="no_loads"></a>
### Add an appliance first

A circuit or a group is made of appliances, so it needs at least one. Add the appliances first with **Add appliance**, then the circuit or group.

<a name="not_enough_loads"></a>
### Add at least two appliances first

A room compares two or more appliances that heat it, so it needs at least two. Add them first, then the room.

## Common symptoms

<a name="dashboard"></a>
### The dashboard stays blank

Reload the page. If it stays blank, check that PowerPlan is loaded under **Settings** > **Devices & services**, and that **Settings** > **Dashboards** > **Resources** lists PowerPlan's card.

<a name="not_running"></a>
### An appliance does not run when I expect

Look at the appliance's **Plan status**: it says what PowerPlan plans and why, for example waiting for cheap power or paused to hold the capacity step. In trial mode, PowerPlan steers nothing.

### An appliance's health says Not following

The device accepted PowerPlan's last three commands but didn't apply them, for example a heat pump that keeps its old temperature. PowerPlan keeps controlling it and tries again when it may, and the notification for devices that stop answering tells you. Check the device's own integration and logs: often a cloud connection or a Bluetooth link drops the commands. It clears by itself the first time a command takes.

<a name="over_target"></a>
### An hour went over the target

Something PowerPlan does not steer used more than it could make room for, such as an oven. **Recommendation** on the home says what it saw; the capacity step may still hold, since only your highest hours count.

<a name="diagnostics"></a>
## Diagnostics and reporting a problem

1. Turn on debug logging: **Settings** > **Devices & services** > **PowerPlan**, then **Enable debug logging**.
2. Reproduce the problem, then turn debug logging off to download the log.
3. Download diagnostics from the home's device page: they hold the last decision and the plan, with nothing private.
4. Open an issue on GitHub with both files and what you expected.

**See also:** [How it works](how-it-works.md) · [Limitations](limitations.md)
