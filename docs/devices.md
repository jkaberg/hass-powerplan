<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Devices

# Devices

The chargers and devices PowerPlan knows how to steer, and the ones it cannot. When you add an appliance, PowerPlan recognizes the device and says which of these it uses.

<!-- generated:begin profiles · tools/docs.py writes this block; change the profile registry, not this table -->
| Profile | For | PowerPlan |
|---|---|---|
| `anker_solix` | Home battery | charges, holds and discharges it |
| `e3dc_rscp` | Home battery | charges, holds and discharges it |
| `easee_ble` | Car charger | sets the power or current |
| `easee_cloud` | Car charger | sets the power or current |
| `ecoflow_cloud` | Home battery | charges, holds and discharges it |
| `foxess_modbus` | Home battery | charges, holds and discharges it |
| `fronius` | Home battery | charges, holds and discharges it |
| `fronius_modbus` | Home battery | charges, holds and discharges it |
| `generic_climate` | Floor heating, Heat pump, Panel heater, Water heater | sets its mode, sets its temperature |
| `generic_number` | Home battery, Car charger, Something else on a switch | sets the power or current |
| `generic_switch` | Dishwasher, washer or dryer, Something else on a switch, Panel heater | turns it on and off |
| `goecharger_api2` | Car charger | sets the power or current |
| `goodwe` | Home battery | charges, holds and discharges it |
| `growatt_server` | Home battery | charges, holds and discharges it |
| `homewizard` | Home battery | charges, holds and discharges it |
| `huawei_solar` | Home battery | charges, holds and discharges it |
| `marstek_local_api` | Home battery | charges, holds and discharges it |
| `marstek_modbus` | Home battery | charges, holds and discharges it |
| `ocpp` | Car charger | sets the power or current |
| `peblar` | Car charger | sets the power or current |
| `saj_h2_modbus` | Home battery | charges, holds and discharges it |
| `sessy` | Home battery | charges, holds and discharges it |
| `sigen` | Home battery | charges, holds and discharges it |
| `solaredge_modbus_multi` | Home battery | charges, holds and discharges it |
| `solarman` | Home battery | charges, holds and discharges it |
| `solax_modbus` | Home battery | charges, holds and discharges it |
| `solax_modbus_sofar` | Home battery | charges, holds and discharges it |
| `solis_cloud_control` | Home battery | charges, holds and discharges it |
| `solis_modbus` | Home battery | charges, holds and discharges it |
| `sonnenbatterie` | Home battery | charges, holds and discharges it |
| `sungrow_modbus` | Home battery | charges, holds and discharges it |
| `tesla_custom` | Home battery | charges, holds and discharges it |
| `tesla_fleet` | Home battery | charges, holds and discharges it |
| `teslemetry` | Home battery | charges, holds and discharges it |
| `tessie` | Home battery | charges, holds and discharges it |
| `v2c` | Car charger | sets the power or current |
| `victron` | Home battery | charges, holds and discharges it |
| `victron_gx` | Home battery | charges, holds and discharges it |
| `victron_mqtt` | Home battery | charges, holds and discharges it |
| `wallbox` | Car charger | sets the power or current |
| `zaptec` | Car charger | sets the power or current |
| `zendure_ha` | Home battery | charges, holds and discharges it |
<!-- generated:end profiles -->

<a name="anker_solix"></a>
## `anker_solix`

An Anker Solarbank through the Anker Solix integration from HACS. Its own panels charge it; PowerPlan sets only how much it gives your home, through its system output preset. Anker's cloud updates every 5 minutes, so PowerPlan changes it at most that often.

<a name="e3dc_rscp"></a>
## `e3dc_rscp`

An E3/DC home power station, through the E3/DC Remote Storage Control Protocol integration from HACS. PowerPlan sets its power mode - charging from the grid, discharging, or idle to hold the charge - with the power. The integration repeats the mode while Home Assistant runs, and the E3/DC returns to normal operation when Home Assistant stops.

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

<a name="fronius"></a>
## `fronius`

A Fronius GEN24 battery, through Home Assistant's own Fronius integration. PowerPlan can hold the battery's charge, by limiting its discharge to 0 %, and lets it run on its own otherwise; it cannot make it charge or discharge. Switch on **Inverter control via Modbus** on the inverter first; without it the integration has no settings to change. For charging and discharging on command, use the Fronius Modbus integration from HACS instead.

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

<a name="growatt_server"></a>
## `growatt_server`

A Growatt MIN, SPH or MIX battery, through Home Assistant's own Growatt integration with an OpenAPI token. PowerPlan steers it by its discharge limit and by charging from the grid up to your charge target; the inverter sets the power itself. The Growatt cloud answers every five minutes, so PowerPlan changes it no more often.

<a name="huawei_solar"></a>
## `huawei_solar`

A Huawei LUNA battery behind a SUN2000 inverter, through the Huawei Solar integration from HACS. PowerPlan charges and discharges it for up to an hour at a time. If PowerPlan stops, the battery goes back to its own mode within the hour. To hold the battery's charge for later, PowerPlan sets its maximum discharging power to 0, and puts it back when the battery may discharge again.

<a name="homewizard"></a>
## `homewizard`

HomeWizard Plug-In Batteries, through Home Assistant's HomeWizard integration. PowerPlan sets the battery group's charging strategy on your P1 meter: net zero to run on their own, net zero charging only to hold their charge, and one-time full charge to charge them. The batteries set their own power. Choose the state of charge of one of the batteries when you add them. If you used HomeWizard's own smart charging, PowerPlan puts it back when you stop steering the batteries.

<a name="marstek_local_api"></a>
## `marstek_local_api`

A Marstek Venus battery, through the Marstek Local API integration from HACS. PowerPlan charges and discharges it in passive mode for up to an hour at a time, and presses **Auto mode** to hand it back. If you used its AI mode, PowerPlan puts it back when you stop steering the battery.

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

<a name="sessy"></a>
## `sessy`

A Sessy battery, through the Sessy integration from HACS. PowerPlan sets its power strategy to API and gives it a power; Net zero is how it runs on its own, and Idle holds its charge. If you used its Dynamic strategy, PowerPlan puts it back when you stop steering the battery.

<a name="sigen"></a>
## `sigen`

A Sigenergy battery through the Sigenergy integration from HACS. PowerPlan switches its remote control mode between charging, discharging and maximum self consumption, and keeps remote control on. To hold the battery's charge for later, PowerPlan sets its maximum discharging limit to 0. The integration ships its controls turned off: switch off its read-only mode in the integration's options, then enable the **Remote EMS Control Mode** entity.

<a name="solaredge_modbus_multi"></a>
## `solaredge_modbus_multi`

A SolarEdge StorEdge battery, through the SolarEdge Modbus Multi integration from HACS. PowerPlan puts the storage into remote control and sets the command mode and its power. A command lapses after an hour to maximum self-consumption, so the battery runs on its own if PowerPlan stops. Switch on **Power Control Options** in the integration's options first. The battery's state of charge is on its own device: choose it when you add the battery.

<a name="solarman"></a>
## `solarman`

A Deye or Sunsynk hybrid inverter's battery, through the Solarman integration from HACS. PowerPlan sets the state of charge of all six of its time-of-use programs at once - your reserve to run on its own, the charge now to hold, your charge target with grid charging to charge - and turns time of use on for every day. The programs' times stay yours. The inverter sets the power, and PowerPlan changes the programs at most every five minutes.

<a name="solax_modbus"></a>
## `solax_modbus`

A SolaX hybrid inverter's battery, or a rebranded one, through the SolaX Inverter Modbus integration from HACS. PowerPlan uses its remote control: it sets the power, then presses the trigger. To hold the battery's charge for later it uses the remote control's *No Discharge* mode. If PowerPlan stops, the inverter goes back to its own mode within the hour.

<a name="solax_modbus_sofar"></a>
## `solax_modbus_sofar`

A Sofar HYD inverter's battery, through the SolaX Inverter Modbus integration from HACS. PowerPlan puts it into passive mode, sets the battery's power and presses **Passive: Update Battery Charge/Discharge**; to hold the charge for later it keeps the battery from discharging.

<a name="solis_cloud_control"></a>
## `solis_cloud_control`

A Solis inverter's battery, through the Solis Cloud Control integration from HACS. PowerPlan steers it by its reserve and by charging it from the grid up to your charge target; the inverter sets the power.

<a name="solis_modbus"></a>
## `solis_modbus`

A Solis hybrid inverter's battery with Remote Dispatch firmware, through the Solis Modbus integration from HACS. PowerPlan charges, discharges or holds it through a dispatch that runs out after an hour, so the inverter returns to its own mode if PowerPlan stops. Without Remote Dispatch firmware PowerPlan cannot steer it.

<a name="sonnenbatterie"></a>
## `sonnenbatterie`

A sonnenBatterie, through the sonnenBatterie integration from HACS. PowerPlan switches it to manual and sets its charge or discharge power; automatic is how it runs on its own. Turn on write access for the local API in the battery's own web interface first. If you used its optimizing mode, PowerPlan puts it back when you stop steering the battery.

<a name="sungrow_modbus"></a>
## `sungrow_modbus`

A Sungrow SH hybrid inverter's battery, through the Sungrow Modbus package by mkaiser, a YAML package rather than an integration. Its entities are on no device: choose **Not on a device: choose the entities** when you add the battery and pick them. PowerPlan finds the package by its **EMS mode** select and the one next to it that starts and stops a charge. It sets the battery's power there and switches the EMS mode away from self-consumption to charge, discharge or hold. Nothing it sets runs out, so a discharge also sets **Battery Min Soc** to your reserve: a battery left discharging stops there.

<a name="tesla_custom"></a>
## `tesla_custom`

A Tesla Powerwall, through the Tesla Custom Integration from HACS. PowerPlan keeps the Powerwall in self-powered mode and steers it by its backup reserve: at your reserve it runs on its own, at its charge now it holds that charge, and at your charge target, with charging from the grid allowed, it charges. The Powerwall sets the power itself, and PowerPlan cannot make it discharge; it covers your home by itself. If you used Tesla's own time-based control, PowerPlan puts it back when you stop steering the battery.

<a name="tesla_fleet"></a>
## `tesla_fleet`

A Tesla Powerwall, through Home Assistant's own Tesla Fleet integration. PowerPlan keeps the Powerwall in self-powered mode and steers it by its backup reserve: at your reserve it runs on its own, at its charge now it holds that charge, and at your charge target, with charging from the grid allowed, it charges. The Powerwall sets the power itself, and PowerPlan cannot make it discharge; it covers your home by itself. If you used Tesla's own time-based control, PowerPlan puts it back when you stop steering the battery.

<a name="teslemetry"></a>
## `teslemetry`

A Tesla Powerwall, through Home Assistant's own Teslemetry integration. PowerPlan keeps the Powerwall in self-powered mode and steers it by its backup reserve: at your reserve it runs on its own, at its charge now it holds that charge, and at your charge target, with charging from the grid allowed, it charges. The Powerwall sets the power itself, and PowerPlan cannot make it discharge; it covers your home by itself. If you used Tesla's own time-based control, PowerPlan puts it back when you stop steering the battery.

<a name="tessie"></a>
## `tessie`

A Tesla Powerwall, through Home Assistant's own Tessie integration. PowerPlan keeps the Powerwall in self-powered mode and steers it by its backup reserve: at your reserve it runs on its own, at its charge now it holds that charge, and at your charge target, with charging from the grid allowed, it charges. The Powerwall sets the power itself, and PowerPlan cannot make it discharge; it covers your home by itself. If you used Tesla's own time-based control, PowerPlan puts it back when you stop steering the battery.

<a name="v2c"></a>
## `v2c`

A V2C Trydan through Home Assistant's V2C integration, over your home network.

<a name="victron"></a>
## `victron`

A Victron system with ESS, through the Victron integration from HACS that talks Modbus to the GX device. Turn on write support in the integration's options first. PowerPlan charges by moving the ESS grid setpoint, so the system draws the charge from the grid on top of the house. It discharges by limiting the discharge power with the setpoint at 0, so the battery covers the house and never exports. To hold the charge for later it sets the discharge limit to 0. PowerPlan switches Dynamic ESS off while it steers and back on when you stop steering the battery.

<a name="victron_gx"></a>
## `victron_gx`

A Victron system with ESS, through Home Assistant's own Victron GX integration. Choose the **Hub4** device when you add it. The battery, the grid meter and Dynamic ESS are other devices, so the form asks for the state of charge, the battery's power and the grid's power. PowerPlan writes the Hub4 overrides, not the ESS settings: a charge moves the grid setpoint, a discharge limits the discharge power with the setpoint at 0, and a hold sets that limit to 0. Switch Dynamic ESS off yourself: it writes the same overrides.

<a name="victron_mqtt"></a>
## `victron_mqtt`

A Victron system with ESS, through the Victron MQTT integration from HACS. It is the same as [`victron_gx`](#victron_gx): choose the **Hub4** device, pick the state of charge, the battery's power and the grid's power, and switch Dynamic ESS off yourself.

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
