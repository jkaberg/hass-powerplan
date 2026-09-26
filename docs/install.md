<!-- kind: start -->
[PowerPlan docs](README.md) › Start › Install

# Install PowerPlan

Install PowerPlan through HACS, then add it to Home Assistant. This page also covers updating and removing it.

<a name="requirements"></a>
## Requirements

<!-- generated:begin requirements · tools/docs.py writes this block; change hacs.json or manifest.json, not this table -->
| Requirement | Value |
|---|---|
| Home Assistant | 2026.3.0 or newer |
| Uses, when set up | `energy`, `frontend`, `http`, `lovelace`, `recorder`, `nordpool`, `weather`, `zwave_js` |
| Installs | `holidays>=0.84` |
| Version | 0.0.2 |
<!-- generated:end requirements -->

PowerPlan uses these integrations when they are set up: the grid meter's power reading, the Nord Pool integration for prices, a weather entity, and the integrations your appliances already use.

<a name="hacs"></a>
## Install through HACS

[![Open your Home Assistant instance and open PowerPlan in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jkaberg&repository=hass-powerplan&category=integration)

1. Select the button above. Or go to **HACS**, open the menu at the top right, and select **Custom repositories**.
2. If HACS asks, add `https://github.com/jkaberg/hass-powerplan` with the type **Integration**.
3. Select **PowerPlan**, then **Download**.
4. Restart Home Assistant: go to **Settings**, open the menu at the top right, and select **Restart Home Assistant**.

## Install by hand

1. Download the source code (zip) of the latest [release](https://github.com/jkaberg/hass-powerplan/releases), and unpack it.
2. Copy its `custom_components/powerplan/` folder into the `custom_components/` folder of your Home Assistant configuration. Create that folder if it is missing.
3. Restart Home Assistant.

A manual install does not update itself. Repeat these steps for each new release.

## Add PowerPlan to Home Assistant

[![Open your Home Assistant instance and start setting up PowerPlan.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=powerplan)

1. Select the button above. Or go to **Settings** > **Devices & services**, select **Add integration**, and search for **PowerPlan**.
2. Answer the questions about your home. Each one says where to find the answer.
3. Add your appliances from PowerPlan's page with **Add appliance**.

> [!IMPORTANT]
> PowerPlan starts in trial mode. It shows what it would do, and controls nothing until you switch on **Automatic control**.

<a name="update"></a>
## Update

HACS shows an update for PowerPlan when a new version is out.

1. Read what changed on the [releases page](https://github.com/jkaberg/hass-powerplan/releases).
2. Select the update under **Settings** > **Updates**, or in HACS, and install it.
3. Restart Home Assistant.

Your homes, appliances, and learned data stay as they are.

<a name="remove"></a>
## Remove

1. Go to **Settings** > **Devices & services** > **PowerPlan** ([open](https://my.home-assistant.io/redirect/integration/?domain=powerplan)).
2. For each home, open the menu next to it and select **Delete**. PowerPlan first hands every appliance back, so nothing stays paused.
3. If you added PowerPlan's dashboard, delete it under **Settings** > **Dashboards** ([open](https://my.home-assistant.io/redirect/lovelace_dashboards/)).
4. In HACS, open **PowerPlan**, open the menu, and select **Remove**.
5. Restart Home Assistant.

The history of PowerPlan's sensors stays in Home Assistant's recorder until it ages out.

**Next:** [the glossary](glossary.md), for the words the setup uses.
