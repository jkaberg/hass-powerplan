![PowerPlan: the icon and the wordmark on light and dark themes](https://raw.githubusercontent.com/jkaberg/hass-powerplan/main/docs/images/brand/preview.png)

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

## Requirements

- Home Assistant 2026.3 or newer, and HACS.
- A power reading from your electricity meter, and electricity prices in Home Assistant, for example from the Nord Pool integration.

## Install

[![Open your Home Assistant instance and open PowerPlan in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jkaberg&repository=hass-powerplan&category=integration)

[![Open your Home Assistant instance and start setting up PowerPlan.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=powerplan)

[Read the documentation](https://github.com/jkaberg/hass-powerplan/blob/main/docs/README.md), starting with [Install](https://github.com/jkaberg/hass-powerplan/blob/main/docs/install.md).

## Known limitations

- HACS shows a blank icon for PowerPlan in its store. HACS reads icons from a central brands repository, and PowerPlan ships its icon inside the integration, which Home Assistant shows on the integration page, the device pages, and the pickers ([hacs/integration#5171](https://github.com/hacs/integration/issues/5171)).

## Contributing

The design documents are under [`design/`](https://github.com/jkaberg/hass-powerplan/tree/main/design).
