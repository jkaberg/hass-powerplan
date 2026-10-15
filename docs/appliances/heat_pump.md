<!-- kind: reference -->
[PowerPlan docs](../README.md) › Reference › [Appliances](README.md) › Heat pump

# Heat pump

A heat pump PowerPlan steers by its temperature: warmer when power is cheap, a little cooler when it is dear, and in summer the same for cooling.

## What it needs

A heat pump Home Assistant shows as a climate entity. Its rated power and the house's size let PowerPlan estimate how much it draws.

<a name="device"></a>
## Which device is it?

Pick the device that controls it. If you do not see it, check that Home Assistant can switch or set it.

<a name="match"></a>
## Is this the right device?

Shown only when PowerPlan knows more than one way of talking to the device. Choose one; the best fit is already selected. Which entity PowerPlan reads and controls is checked on the next step.

## Data points

What PowerPlan reads and controls on this appliance, and what it does without each one. The [data points step](README.md#data-points) finds them on the device and asks for the rest.

<!-- generated:begin data_points:heat_pump · tools/docs.py writes this block; change the needs or strings.json, not this table -->
| Data point | Needed | What it is for | Without it |
|---|---|---|---|
| Temperature | Required | The temperature the device measures. | PowerPlan cannot plan the heat. |
| Power | Recommended | What the appliance draws right now, in watts. | The rated power stays your answer and is not measured. |
| Outdoor temperature | Recommended | The temperature outside. | Your home's outdoor temperature is used. |
| Outlet temperature | Optional | The temperature of the air or water the heat pump delivers. | Nothing changes in the plan. |
| Energy | Optional | The appliance's energy meter, in kWh. | Energy is counted from the power instead. |
| Setpoint | Required | The temperature the device is set to - what PowerPlan changes. | PowerPlan cannot control it. |
<!-- generated:end data_points:heat_pump -->

The **Outdoor temperature** data point is the heat pump's own sensor, if its device has one. Without one, it uses your [home's outdoor temperature](../setup.md#outdoor). In a [room](../circuits-groups-rooms.md#room) with a heat pump and a floor, PowerPlan compares them at today's temperature.

<a name="questions"></a>
## About {name}

Answer what you know. Every other setting is worked out from these answers and shown under **Advanced**.

Where the room's temperature is already in Home Assistant's history, PowerPlan measures how fast the room cools when the heat pump is off. That number replaces the one worked out from the building's age, and the review says how many days it came from. You can change it under **Advanced**. Until it is measured, the room is taken to hold 0.012 kWh of heat per degree for every cubic meter, the light timber building of a typical Nordic home, so a 250 cubic meter room starts at 3 kWh per degree.

> [!NOTE]
> Since v0.0.12, that starting figure is 0.012 where it was 0.03. A heat pump set up before it keeps the cooling time it was given, until the next measurement replaces it.

Where the heat pump's setpoint is in Home Assistant's history, a temperature it held for at least half of the last 14 days is the comfort temperature, and is not asked. It is never taken from a heat pump PowerPlan has already controlled, since then the history is PowerPlan's own.

<!-- generated:begin questions:heat_pump · tools/docs.py writes this block; change the questionnaire or strings.json, not this table -->
| Question | What it asks |
|---|---|
| Heat pump type | Air-to-air, air-to-water or ground source. |
| Heating system | Advanced: radiators or floor heating, for an air-to-water pump's COP curve. |
| Area | The heated area, in square meters. |
| Building age | When the house was built, which sets its heat loss. |
| Rated power | The heat pump's rated electrical power, in kW. |
| Comfort temperature | The temperature you want when home; blank uses the room's usual. |
| Advanced › COP curve | Advanced: outdoor temperature to COP, as `-10:2.1, 7:3.8`. |
| Advanced › Band | Advanced: the kelvin the room may swing around the target. |
| Advanced › Command interval | Advanced: seconds between two writes to the device. |
| Advanced › Dwell | Advanced: seconds a setpoint is held before the next change. |
| Plan for cooling | Plan for summer: cool the room to the comfort temperature instead of heating it. |
| Advanced › Preheat | Advanced: bank heat before an expensive hour. |
| Advanced › Never switch when | Advanced: entities that, when on, stop PowerPlan changing this device. |
| Advanced › Follow presence | Advanced: lower the target when nobody is home. |
| Advanced › Save while asleep | Advanced: lower the target 1 K through the quiet hours. |
| Advanced › Weekly schedule | Advanced: a schedule helper – on is comfort, off is the vacation level. |
| Advanced › Arrival calendars | Advanced: calendars whose next event is an arrival, a deadline for the comfort target. |
| Advanced › Run now lasts at most | Advanced: how many hours Run now lasts before it turns itself off. |
<!-- generated:end questions:heat_pump -->

<a name="cop_curve"></a>
## The COP curve

The curve says how many units of heat the pump delivers for each unit of electricity at an outdoor temperature. PowerPlan uses it to price each hour's heat, so it is shown under **Advanced** and you can edit it. An air-to-air pump starts from the curve measured in a Nordic home, and a ground-source pump from a flat one.

An air-to-water pump's curve follows what it heats, which is the **Heating system** answer under **Advanced**. Floor heating needs warmer water than the room only slightly, and radiators need much warmer water, so the same pump does better under a floor. Radiators are the answer when you say nothing, since they give the lower, cautious curve.

| Outdoor | Floor heating | Radiators |
|---|---|---|
| -7 °C | 2.6 | 2.0 |
| 2 °C | 3.5 | 2.6 |
| 7 °C | 4.4 | 3.2 |

The curve is a third of the best a heat pump could do at the water temperature, 28 °C plus a quarter of the cold under a floor, and 35 °C plus half of it on radiators, at most 55 °C. It is written when you set the heat pump up. A heat pump set up before keeps the curve it has; to use the new one, answer the questions again.

> [!NOTE]
> Since v0.x, an air-to-water pump's curve follows the **Heating system**. Before, it was one table that was close to the radiator case and too low for a floor.

PowerPlan never lowers the temperature unless you ask. **Save when nobody is home** lowers the target 3 °C while everyone is away, and **Save while asleep** lowers it 1 °C through the quiet hours you set for notifications, 22:00 to 07:00 unless you changed them. Both are switches on the appliance, off for a new one, and neither goes under the lowest temperature. The temperature is back at comfort when the quiet hours end.

> [!NOTE]
> Since v0.0.12, **Save when nobody is home** is off for a new appliance, and **Save while asleep** is new. An appliance set up before keeps its **Save when nobody is home** as it was.

<a name="questions_followup"></a>
## How far should preheat go?

This follow-up comes after **Preheat** is turned on. You can leave **Preheat up to** empty, which is the default, and let each hour decide. Set a temperature to stop preheating above it.

## The plan

By default the room is warmed within its band before the price rises, and the heat pump coasts through the expensive hours as long as the room stays in the band. If you turn **Preheat** off, the room is never warmed above comfort; the pump still eases off before a price rise. Each hour's heat is priced at what the pump delivers at that hour's outdoor temperature, and 15 percent dearer between -5 and +3 °C, where the pump spends heat defrosting. A cold night is not the place to warm ahead unless the price gap is wide enough to pay for it, and a mild night before a colder day is. **Preheat** is off for a new heat pump. A unit that can cool asks **Plan for cooling**: turned on, PowerPlan cools the room ahead of expensive hours instead. The other plans it offers are listed on [Appliances](README.md#heat_pump); each is explained on [Plans](../strategies.md).

## Everyday use

Switch **Plan for cooling** on and off when you switch your unit between heating and cooling. **Run now** holds comfort whatever the price.

## Limits

PowerPlan never turns the heat pump's power off and never pauses it while it defrosts. The band is at most 2 degrees each way. It changes the setpoint at most once an hour, since a heat pump takes up to an hour and a half to respond, so a price that moves every 15 minutes is planned by its hourly average.

Since v0.x, **Preheat up to** has no default. A heat pump set up before keeps the 5 °C it was given.

**See also:** [Appliances](README.md) · [Plans](../strategies.md) · [Devices](../devices.md)
