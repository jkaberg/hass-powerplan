<!-- kind: start -->
[PowerPlan docs](README.md) › Start › Get started

# Get started

From an installed PowerPlan to a first planned run, then to handing PowerPlan control. It takes about ten minutes, and nothing is switched until the last step.

## Before you begin

- PowerPlan is installed, as [Install](install.md) describes.
- Your electricity meter's power reading is in Home Assistant, for example from a HAN or P1 reader.
- Your electricity prices are in Home Assistant, for example through the Nord Pool integration.
- You know the size of your main fuse, or you are happy to answer **Don't know**.

## 1. Add your home

1. Go to **Settings** > **Devices & services**, select **Add integration**, and search for **PowerPlan**.
2. Choose what PowerPlan helps with. **Save on energy and grid fee** is the right choice for most homes.
3. Answer the questions. Each one links to its section of [Set up a home](setup.md), which says where to find the answer.
4. On the last screen, leave **Start in trial mode** on.

PowerPlan adds a device for your home, with the price, the hour's usage and your capacity step as entities.

## 2. Add your appliances

1. Open **Settings** > **Devices & services** > **PowerPlan**, and select **Add appliance** on your home.
2. Pick the device that controls the appliance, such as the car charger or the floor thermostat. PowerPlan recognizes what it is and fills in what it can.
3. Answer the questions about the appliance, such as when the car must be ready or the room's comfort temperature.

Add the appliances that use the most power first: the car charger, the water heater, the floors, the heat pump. If some sit behind their own fuse or should take turns, add a circuit or a group afterwards, as [Circuits, groups and rooms](circuits-groups-rooms.md) explains.

## 3. Watch the first plan

Each appliance's **Plan status** says what PowerPlan plans for it and why, and its **Planned runs** calendar shows when. In trial mode, PowerPlan plans and shows what it would do, but changes nothing. Watch it for a day or two: check that the car is planned to be ready in time and that the rooms keep warm.

> [!TIP]
> Add PowerPlan's dashboard to see the plan against the prices and your capacity step on one screen: [Dashboard](dashboard.md).

## 4. Switch on control

When the plans look right, switch on **Automatic control** on your home. PowerPlan then starts and pauses the appliances as planned. You can switch an appliance back to trial mode on its own device, or switch the whole home off, at any time; PowerPlan then hands every appliance back as it found it.

**Next:** [Set up a home](setup.md) · [Glossary](glossary.md)
