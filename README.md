![PowerPlan: the icon and the wordmark on light and dark themes](https://raw.githubusercontent.com/jkaberg/hass-powerplan/main/docs/images/brand/preview.png)

# PowerPlan

PowerPlan is a Home Assistant integration that runs your home's big appliances when power is cheap, and keeps your home inside the capacity step you choose on your grid bill. It plans each appliance a day ahead from the electricity prices, and steers it through the integrations you already have.

- **Cheaper hours.** The car charger, the water heater, and the heating run in the cheapest hours, and are still ready when you need them.
- **A lower grid fee.** Where your grid company bills by capacity step, PowerPlan keeps every hour under the step you choose.
- **A safe main fuse.** PowerPlan turns appliances down before the home draws more than the main fuse can carry.

PowerPlan starts in trial mode: it shows what it would do, and changes nothing until you switch on automatic control.

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
