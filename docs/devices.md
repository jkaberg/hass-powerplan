<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Devices

# Devices

The chargers and devices PowerPlan knows how to steer, and the ones it cannot. When you add an appliance, PowerPlan recognizes the device and says which of these it uses.

<!-- generated:begin profiles · tools/docs.py writes this block; change the profile registry, not this table -->
| Profile | For | PowerPlan |
|---|---|---|
| `anker_solix` | Home battery | charges, holds and discharges it |
| `easee_ble` | Car charger | sets the power or current |
| `easee_cloud` | Car charger | sets the power or current |
| `ecoflow_cloud` | Home battery | charges, holds and discharges it |
| `foxess_modbus` | Home battery | charges, holds and discharges it |
| `fronius_modbus` | Home battery | charges, holds and discharges it |
| `generic_climate` | Floor heating, Heat pump, Panel heater, Water heater | sets its mode, sets its temperature |
| `generic_number` | Home battery, Car charger, Something else on a switch | sets the power or current |
| `generic_switch` | Dishwasher, washer or dryer, Something else on a switch, Panel heater | turns it on and off |
| `goecharger_api2` | Car charger | sets the power or current |
| `goodwe` | Home battery | charges, holds and discharges it |
| `homewizard` | Home battery | charges, holds and discharges it |
| `huawei_solar` | Home battery | charges, holds and discharges it |
| `marstek_modbus` | Home battery | charges, holds and discharges it |
| `ocpp` | Car charger | sets the power or current |
| `peblar` | Car charger | sets the power or current |
| `saj_h2_modbus` | Home battery | charges, holds and discharges it |
| `sigen` | Home battery | charges, holds and discharges it |
| `solaredge_modbus_multi` | Home battery | charges, holds and discharges it |
| `solax_modbus` | Home battery | charges, holds and discharges it |
| `solax_modbus_sofar` | Home battery | charges, holds and discharges it |
| `v2c` | Car charger | sets the power or current |
| `wallbox` | Car charger | sets the power or current |
| `zaptec` | Car charger | sets the power or current |
| `zendure_ha` | Home battery | charges, holds and discharges it |
<!-- generated:end profiles -->

<a name="anker_solix"></a>
## `anker_solix`

An Anker Solarbank through the Anker Solix integration from HACS. Its own panels charge it; PowerPlan sets only how much it gives your home, through its system output preset. Anker's cloud updates every 5 minutes, so PowerPlan changes it at most that often.

<a name="easee_ble"></a>
## `easee_ble`

An Easee charger over Bluetooth, through the Easee BLE integration. PowerPlan sets its charging current and reads its status directly, with no cloud in between.

<a name="easee_cloud"></a>
## `easee_cloud`

An Easee charger through Easee's cloud integration. PowerPlan sets the charger's dynamic current limit, which the charger forgets on every new session, so PowerPlan sets it again when a car connects.

<a name="ecoflow_cloud"></a>
## `ecoflow_cloud`

An EcoFlow PowerStream through the EcoFlow Cloud integration from HACS. Its own panels charge it; PowerPlan sets only how much power it gives your home.

<a name="foxess_modbus"></a>
## `foxess_modbus`

A Fox ESS hybrid inverter's battery (H1, H3, KH, AIO and their rebrands), through the FoxESS - Modbus integration from HACS. PowerPlan switches its work mode to charge or discharge the battery at the power it sets; to hold the battery's charge for later it uses Back-up. The integration keeps the command alive while Home Assistant runs, and the inverter drops it when Home Assistant stops.

<a name="fronius_modbus"></a>
## `fronius_modbus`

A Fronius GEN24 or Verto battery, through the Fronius Modbus integration from HACS. PowerPlan sets the storage control mode to Charge from Grid or Discharge to Grid and then the power; to hold the battery's charge for later it uses Block Discharging. Switch on **Inverter control via Modbus** on the inverter first.

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

<a name="goodwe"></a>
## `goodwe`

A home battery behind a GoodWe hybrid inverter, through Home Assistant's GoodWe integration. PowerPlan switches the inverter's operation mode between charging, discharging and its own mode. The inverter sets the power itself, so PowerPlan counts it at full power. The battery's reserve becomes the inverter's depth of discharge. To hold the battery's charge for later, PowerPlan raises the depth of discharge to where the battery is now.

<a name="huawei_solar"></a>
## `huawei_solar`

A Huawei LUNA battery behind a SUN2000 inverter, through the Huawei Solar integration from HACS. PowerPlan charges and discharges it for up to an hour at a time. If PowerPlan stops, the battery goes back to its own mode within the hour. To hold the battery's charge for later, PowerPlan sets its maximum discharging power to 0, and puts it back when the battery may discharge again.

<a name="homewizard"></a>
## `homewizard`

HomeWizard Plug-In Batteries, through Home Assistant's HomeWizard integration. PowerPlan sets the battery group's charging strategy on your P1 meter: net zero to run on their own, net zero charging only to hold their charge, and one-time full charge to charge them. The batteries set their own power. Choose the state of charge of one of the batteries when you add them. If you used HomeWizard's own smart charging, PowerPlan puts it back when you stop steering the batteries.

<a name="marstek_modbus"></a>
## `marstek_modbus`

A Marstek Venus battery, through the Marstek Venus Modbus integration from HACS. PowerPlan turns on RS485 control and tells the battery to charge, discharge or stand by, at the power it sets; with RS485 control off, the battery runs its own work mode again. Enable the integration's control entities first: it ships them disabled.

<a name="ocpp"></a>
## `ocpp`

Any charger that speaks OCPP 1.6, through the OCPP integration from HACS: ABB, Alfen, CTEK, EVBox, Vestel, Etrel, Autel and others. PowerPlan sets the charge point's maximum current, and pauses charging by setting it to 0 A.

<a name="peblar"></a>
## `peblar`

A Peblar charger through Home Assistant's Peblar integration, over your home network.

<a name="saj_h2_modbus"></a>
## `saj_h2_modbus`

A SAJ H2 inverter's battery, through the SAJ H2 Modbus integration from HACS. PowerPlan uses its passive charge and discharge, with the power as a share of the inverter's rate: set the battery's charge and discharge power right in PowerPlan, because that is what the share is taken from.

<a name="sigen"></a>
## `sigen`

A Sigenergy battery through the Sigenergy integration from HACS. PowerPlan switches its remote control mode between charging, discharging and maximum self consumption, and keeps remote control on. To hold the battery's charge for later, PowerPlan sets its maximum discharging limit to 0. The integration ships its controls turned off: switch off its read-only mode in the integration's options, then enable the **Remote EMS Control Mode** entity.

<a name="solaredge_modbus_multi"></a>
## `solaredge_modbus_multi`

A SolarEdge StorEdge battery, through the SolarEdge Modbus Multi integration from HACS. PowerPlan puts the storage into remote control and sets the command mode and its power. A command lapses after an hour to maximum self-consumption, so the battery runs on its own if PowerPlan stops. Switch on **Power Control Options** in the integration's options first. The battery's state of charge is on its own device: choose it when you add the battery.

<a name="solax_modbus"></a>
## `solax_modbus`

A SolaX hybrid inverter's battery, or a rebranded one, through the SolaX Inverter Modbus integration from HACS. PowerPlan uses its remote control: it sets the power, then presses the trigger. To hold the battery's charge for later it uses the remote control's *No Discharge* mode. If PowerPlan stops, the inverter goes back to its own mode within the hour.

<a name="solax_modbus_sofar"></a>
## `solax_modbus_sofar`

A Sofar HYD inverter's battery, through the SolaX Inverter Modbus integration from HACS. PowerPlan puts it into passive mode, sets the battery's power and presses **Passive: Update Battery Charge/Discharge**; to hold the charge for later it keeps the battery from discharging.

<a name="v2c"></a>
## `v2c`

A V2C Trydan through Home Assistant's V2C integration, over your home network.

<a name="wallbox"></a>
## `wallbox`

A Wallbox charger through Home Assistant's Wallbox integration. The integration reads the cloud every 90 seconds, so PowerPlan waits that long before it checks a change.

<a name="zaptec"></a>
## `zaptec`

A Zaptec charger through the Zaptec integration from HACS. Zaptec asks that the current changes at most every 15 minutes, so PowerPlan raises it slowly and lowers it at once when it must.

<a name="zendure_ha"></a>
## `zendure_ha`

A Zendure battery, such as a Hyper 2000, through the Zendure integration from HACS. Its own panels charge it; PowerPlan sets only its output limit.

## Chargers PowerPlan cannot steer yet

- Chargers with no way to set the current, only a mode: myenergi zappi, Ohme.
- Charging through the car's own integration: Tesla Fleet, Teslemetry, Tessie.
- KEBA chargers: Home Assistant's KEBA integration adds no device, and PowerPlan adds appliances by device.

If you use evcc, it already controls the charger. Add the charger in PowerPlan in trial mode, so PowerPlan counts its power without steering it.

**See also:** [Appliances](appliances/README.md) · [Plans](strategies.md)
