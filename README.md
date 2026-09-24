![PowerPlan](https://raw.githubusercontent.com/jkaberg/hass-powerplan/main/docs/images/brand/preview.png)

# PowerPlan

[![CI](https://github.com/jkaberg/hass-powerplan/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jkaberg/hass-powerplan/actions/workflows/ci.yml)
[![Nightly](https://github.com/jkaberg/hass-powerplan/actions/workflows/nightly.yml/badge.svg)](https://github.com/jkaberg/hass-powerplan/actions/workflows/nightly.yml)
[![Release](https://img.shields.io/github/v/release/jkaberg/hass-powerplan?include_prereleases&sort=semver)](https://github.com/jkaberg/hass-powerplan/releases)
[![HACS: custom](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Home Assistant 2026.3 or newer](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-18BCF2.svg)](https://www.home-assistant.io/)

When is power cheapest today? Will charging the car now push this month's grid fee up a step? Is the water heater running at the wrong time? Will the oven and the car together trip the main fuse?

PowerPlan answers these every hour and acts on them. It plans your appliances from the day-ahead prices and your grid tariff, keeps usage under the capacity step you choose, and explains each decision on its own dashboard.

- **Your real price.** It fetches your grid company's tariff in many countries, and knows VAT and national taxes by date and region.
- **Ready when you need it.** The car is charged by the time you set, and rooms and water stay at the temperature you chose.
- **Priorities.** When something must pause, the least important appliance pauses first.
- **Presence.** While nobody is home, the heating saves.
- **What it saved.** Cost and savings for each appliance, compared with running at its usual times.
- **Your automations.** Entities, actions such as *Run now for a while*, events such as *Peak warning*, and planned runs in the calendar.
- **Trial mode first.** It shows what it would do and changes nothing until you switch on automatic control.

![ PowerPlan dashboard](https://raw.githubusercontent.com/jkaberg/hass-powerplan/main/docs/images/dashboard-stack.png)

## Requirements

- Home Assistant 2026.3 or newer.
- A power reading from your electricity meter, for example from a HAN or P1 reader.
- Electricity prices in Home Assistant, for example from the Nord Pool integration.
- Appliances you can already switch or set from Home Assistant.

## Installation

### Option 1: HACS

[![Open your Home Assistant instance and open PowerPlan in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jkaberg&repository=hass-powerplan&category=integration)

1. Select the button above. Or open **HACS**, open the menu at the top right, and select **Custom repositories**.
2. If HACS asks, add `https://github.com/jkaberg/hass-powerplan` with the type **Integration**.
3. Select **PowerPlan**, then **Download**.
4. Restart Home Assistant.

### Option 2: Manual

1. Download the source code (zip) of the latest [release](https://github.com/jkaberg/hass-powerplan/releases), and unpack it.
2. Copy its `custom_components/powerplan/` folder into the `custom_components/` folder of your Home Assistant configuration. Create that folder if it is missing.
3. Restart Home Assistant.

A manual install does not update itself. Repeat these steps for each new release.

## Setup

[![Open your Home Assistant instance and start setting up PowerPlan.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=powerplan)

1. Select the button above. Or go to **Settings** > **Devices & services**, select **Add integration**, and search for **PowerPlan**.
2. Answer the questions about your home. Each one links to a page that says where to find the answer.
3. On the last screen, leave **Start in trial mode** on.

The setup asks about these parts of your home. Most questions have **Don't know** or a default, and **Reconfigure** on the home changes any answer later.

| Part | What it asks |
|---|---|
| [The home](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md#the-home) | What PowerPlan should help with, the home's name, and its timezone |
| [The meter](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md#the-meter) | Your meter's sensors and the size of your main fuse |
| [Prices](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md#prices) | Your contract, your price area, and the sensor with your prices |
| [What your supplier adds](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md#what-your-supplier-adds) | A markup, a fixed price, or prices by time of day |
| [Taxes and support schemes](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md#taxes-and-support-schemes) | The support schemes that apply to you |
| [Export and other heat sources](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md#export-and-other-heat-sources) | Whether you sell power back, and whether you also heat with gas, district heating, oil or pellets |
| [The grid tariff](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md#the-grid-tariff) | Your grid company and tariff, and the capacity step you want to stay in |
| [Presence and messages](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md#presence-and-messages) | Who is home, and what PowerPlan should tell you |

## After setup

1. Open PowerPlan's page under **Settings** > **Devices & services**, and select **Add appliance** on your home. Start with those that use the most power: the car charger, the water heater, the floors, and the heat pump.
2. Add the PowerPlan dashboard: go to **Settings** > **Dashboards**, select **Add dashboard**, and choose **PowerPlan**. Home Assistant 2026.3 and 2026.4 need one more step, as [Dashboard](https://github.com/jkaberg/hass-powerplan/blob/main/docs/dashboard.md) explains.
3. Watch the plans for a day or two. In trial mode, PowerPlan shows what it would do and changes nothing.
4. When the plans look right, switch on **Automatic control** on your home.

## Documentation

- [Get started](https://github.com/jkaberg/hass-powerplan/blob/main/docs/get-started.md): from an installed PowerPlan to its first plan
- [Set up a home](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md): every setup question and where to find the answer
- [Appliances](https://github.com/jkaberg/hass-powerplan/blob/main/docs/appliances/README.md) and [Devices](https://github.com/jkaberg/hass-powerplan/blob/main/docs/devices.md): what PowerPlan can steer
- [Dashboard](https://github.com/jkaberg/hass-powerplan/blob/main/docs/dashboard.md): what each card shows
- [Troubleshooting](https://github.com/jkaberg/hass-powerplan/blob/main/docs/troubleshooting.md): repairs and problems
- [All documentation](https://github.com/jkaberg/hass-powerplan/blob/main/docs/README.md)
