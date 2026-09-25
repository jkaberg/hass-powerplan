![PowerPlan](https://raw.githubusercontent.com/jkaberg/hass-powerplan/main/docs/images/brand/logo.png)

[![CI](https://github.com/jkaberg/hass-powerplan/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jkaberg/hass-powerplan/actions/workflows/ci.yml)
[![Nightly](https://github.com/jkaberg/hass-powerplan/actions/workflows/nightly.yml/badge.svg)](https://github.com/jkaberg/hass-powerplan/actions/workflows/nightly.yml)
[![Release](https://img.shields.io/github/v/release/jkaberg/hass-powerplan?include_prereleases&sort=semver)](https://github.com/jkaberg/hass-powerplan/releases)
[![HACS: custom](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Home Assistant 2026.3 or newer](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-18BCF2.svg)](https://www.home-assistant.io/)
[![Discussions](https://img.shields.io/github/discussions/jkaberg/hass-powerplan)](https://github.com/jkaberg/hass-powerplan/discussions)
[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/jkaberg)

PowerPlan decides when your appliances run: the car charger, water heater, floor heating, heat pump and home battery. It plans from the day-ahead prices and your grid tariff, and keeps each hour under the capacity step you pick.

I built it for my own house in Norway, where the grid fee follows the highest hours each month. It runs there every day, and the dashboard shows what it plans and why.

What it does:

- fetches your grid company's tariff (many countries), with VAT and taxes by date and region
- charges the car by the time you set, and keeps rooms and hot water at your temperature
- pauses the least important appliance first when something has to give
- saves on heating while nobody is home
- shows cost and savings per appliance
- exposes entities, actions, events and planned runs in the calendar for your own automations

It starts in trial mode, where it only shows what it would do. Nothing is switched until you turn on **Automatic control**.

![ PowerPlan dashboard](https://raw.githubusercontent.com/jkaberg/hass-powerplan/main/docs/images/dashboard-stack.png)

## Requirements

- Home Assistant 2026.3 or newer.
- A power reading from your electricity meter, for example a HAN or P1 reader.
- Electricity prices in Home Assistant, for example the Nord Pool integration.
- Appliances Home Assistant can already switch or set.

## Installation

### HACS

[![Open your Home Assistant instance and open PowerPlan in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jkaberg&repository=hass-powerplan&category=integration)

1. Select the button above. Or open **HACS**, open the menu at the top right, and select **Custom repositories**.
2. If HACS asks, add `https://github.com/jkaberg/hass-powerplan` as type **Integration**.
3. Select **PowerPlan**, then **Download**.
4. Restart Home Assistant.

### Manual

1. Download the source code (zip) from the latest [release](https://github.com/jkaberg/hass-powerplan/releases) and unpack it.
2. Copy `custom_components/powerplan/` into the `custom_components/` folder of your Home Assistant configuration. Create the folder if it's missing.
3. Restart Home Assistant.

A manual install doesn't update itself, repeat this for every release.

## Setup

[![Open your Home Assistant instance and start setting up PowerPlan.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=powerplan)

1. Select the button above. Or go to **Settings** > **Devices & services**, select **Add integration**, and search for **PowerPlan**.
2. Answer the questions about your home. Each one links to where you find the answer.
3. Leave **Start in trial mode** on at the last screen.

Most questions have **Don't know** or a default, and **Reconfigure** on the home changes any answer later. [Set up a home](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md) explains every question.

## After setup

1. Open PowerPlan under **Settings** > **Devices & services**, and select **Add appliance** on your home. Start with what draws the most: the car charger, the water heater, the floors and the heat pump.
2. Add the dashboard: go to **Settings** > **Dashboards**, select **Add dashboard**, and choose **PowerPlan**. Home Assistant 2026.3 and 2026.4 need one extra step, see [Dashboard](https://github.com/jkaberg/hass-powerplan/blob/main/docs/dashboard.md).
3. Watch the plans for a day or two, trial mode changes nothing.
4. When the plans look right, switch on **Automatic control** on your home.

## Documentation

- [Get started](https://github.com/jkaberg/hass-powerplan/blob/main/docs/get-started.md): from installed to the first plan
- [Set up a home](https://github.com/jkaberg/hass-powerplan/blob/main/docs/setup.md): every setup question and where to find the answer
- [Appliances](https://github.com/jkaberg/hass-powerplan/blob/main/docs/appliances/README.md) and [Devices](https://github.com/jkaberg/hass-powerplan/blob/main/docs/devices.md): what PowerPlan can steer
- [Dashboard](https://github.com/jkaberg/hass-powerplan/blob/main/docs/dashboard.md): what each card shows
- [Troubleshooting](https://github.com/jkaberg/hass-powerplan/blob/main/docs/troubleshooting.md): repairs and problems
- [All documentation](https://github.com/jkaberg/hass-powerplan/blob/main/docs/README.md)

## Help and feedback

- **Questions, setup help and ideas:** [Discussions](https://github.com/jkaberg/hass-powerplan/discussions). Check [Troubleshooting](https://github.com/jkaberg/hass-powerplan/blob/main/docs/troubleshooting.md) first.
- **Bugs and feature requests:** [Issues](https://github.com/jkaberg/hass-powerplan/issues). A bug report without a debug log and the diagnostics gets closed, see [Diagnostics and reporting a problem](https://github.com/jkaberg/hass-powerplan/blob/main/docs/troubleshooting.md#diagnostics) for how to get them.

PowerPlan is a spare time project. PRs are the best way to help, see [CONTRIBUTING.md](https://github.com/jkaberg/hass-powerplan/blob/main/CONTRIBUTING.md). If you want to [buy me a coffee](https://buymeacoffee.com/jkaberg) that's appreciated aswell :-)
