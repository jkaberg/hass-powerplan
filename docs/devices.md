<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Devices

# Devices

The chargers and devices PowerPlan knows how to steer, and the ones it cannot. When you add an appliance, PowerPlan recognizes the device and says which of these it uses.

<!-- generated:begin profiles · tools/docs.py writes this block; change the profile registry, not this table -->
| Profile | For | PowerPlan |
|---|---|---|
| `easee_ble` | Car charger | sets the power or current |
| `easee_cloud` | Car charger | sets the power or current |
| `generic_climate` | Floor heating, Heat pump, Panel heater, Water heater | sets its mode, sets its temperature |
| `generic_number` | Home battery, Car charger, Something else on a switch | sets the power or current |
| `generic_switch` | Dishwasher, washer or dryer, Something else on a switch, Panel heater | turns it on and off |
| `goecharger_api2` | Car charger | sets the power or current |
| `ocpp` | Car charger | sets the power or current |
| `peblar` | Car charger | sets the power or current |
| `v2c` | Car charger | sets the power or current |
| `wallbox` | Car charger | sets the power or current |
| `zaptec` | Car charger | sets the power or current |
<!-- generated:end profiles -->

<a name="easee_ble"></a>
## `easee_ble`

An Easee charger over Bluetooth, through the Easee BLE integration. PowerPlan sets its charging current and reads its status directly, with no cloud in between.

<a name="easee_cloud"></a>
## `easee_cloud`

An Easee charger through Easee's cloud integration. PowerPlan sets the charger's dynamic current limit, which the charger forgets on every new session, so PowerPlan sets it again when a car connects.

<a name="generic_climate"></a>
## `generic_climate`

Any thermostat Home Assistant shows as a climate entity: a floor thermostat, a heat pump, a panel heater or a water heater with a thermostat. PowerPlan detects what it can set, such as its temperature or an eco mode, from its entities.

<a name="generic_number"></a>
## `generic_number`

Anything with a number PowerPlan can set in amps or watts, with or without a switch: a charger no other profile knows, or a battery's charge power.

<a name="generic_switch"></a>
## `generic_switch`

Anything on a switch or a smart plug, with a power sensor if it has one. PowerPlan turns it on and off.

<a name="goecharger_api2"></a>
## `goecharger_api2`

A go-e charger through the go-e Charger API v2 integration from HACS. Enable its **Car state [CODE]** sensor: PowerPlan reads whether a car is connected from it.

<a name="ocpp"></a>
## `ocpp`

Any charger that speaks OCPP 1.6, through the OCPP integration from HACS: ABB, Alfen, CTEK, EVBox, Vestel, Etrel, Autel and others. PowerPlan sets the charge point's maximum current, and pauses charging by setting it to 0 A.

<a name="peblar"></a>
## `peblar`

A Peblar charger through Home Assistant's Peblar integration, over your home network.

<a name="v2c"></a>
## `v2c`

A V2C Trydan through Home Assistant's V2C integration, over your home network.

<a name="wallbox"></a>
## `wallbox`

A Wallbox charger through Home Assistant's Wallbox integration. The integration reads the cloud every 90 seconds, so PowerPlan waits that long before it checks a change.

<a name="zaptec"></a>
## `zaptec`

A Zaptec charger through the Zaptec integration from HACS. Zaptec asks that the current changes at most every 15 minutes, so PowerPlan raises it slowly and lowers it at once when it must.

## Chargers PowerPlan cannot steer yet

- Chargers with no way to set the current, only a mode: myenergi zappi, Ohme.
- Charging through the car's own integration: Tesla Fleet, Teslemetry, Tessie.
- KEBA chargers: Home Assistant's KEBA integration adds no device, and PowerPlan adds appliances by device.

If you use evcc, it already controls the charger. Add the charger in PowerPlan in trial mode, so PowerPlan counts its power without steering it.

**See also:** [Appliances](appliances/README.md) · [Plans](strategies.md)
