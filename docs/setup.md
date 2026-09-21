<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Setup

# Set up a home

Every question PowerPlan asks when you add a home, what it means, and where to find the answer. Each question in the setup links to its section here.

> [!TIP]
> Not sure of an answer? Most questions have **Don't know** or a default, and you can change any answer later with **Reconfigure** on the home.

## The home

<a name="user"></a>
### What should PowerPlan help you with?

Choose what PowerPlan helps with. **Save on energy and grid fee** plans your appliances by price and keeps every hour under your target. **Cheapest hours only** plans by price and skips the meter and the grid tariff. **Protect the main fuse only** keeps the home under its main fuse and plans nothing by price. You can change the choice later with **Reconfigure**.

<a name="name"></a>
### What do you want to call this home?

The name you give here goes on the home's device and on every entity PowerPlan adds for it. Change it later by renaming the device.

<!-- generated:begin fields:config.name · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Name | Home Assistant's own name for your home, unless you choose another. |
<!-- generated:end fields:config.name -->

<a name="timezone"></a>
### Which timezone is the house in?

PowerPlan normally reads the timezone from **Settings** > **System** > **General**. This question appears only when Home Assistant has none. Every time PowerPlan works with depends on it: when an hour begins, when the day turns, and when tomorrow's prices are due.

<!-- generated:begin fields:config.timezone · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Timezone | The IANA time zone the house is in. |
<!-- generated:end fields:config.timezone -->

## The meter

<a name="meter"></a>
### Where do you measure your electricity use?

Pick the device that reads your electricity meter, such as an AMS/HAN reader or a P1 dongle. PowerPlan then finds the power and the meter reading on it by itself. Leave the field empty if your sensors are not on one device; the next question asks for each sensor.

<!-- generated:begin fields:config.meter · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Meter device | Only devices that measure power are listed. |
<!-- generated:end fields:config.meter -->

<a name="meter_confirm"></a>
### Is this your meter?

PowerPlan shows the sensors it found on the meter device and what each reads now. Check that the power reading moves when you turn something on. If a sensor is wrong, answer no and pick the sensors yourself.

<!-- generated:begin fields:config.meter_confirm · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Is this right? | Choose change to pick the sensors yourself. |
<!-- generated:end fields:config.meter_confirm -->

<a name="meter_roles"></a>
### Which sensor measures what?

Two sensors matter: **power now**, in watts or kilowatts, and the **meter reading**, the kWh counter that only goes up. PowerPlan works without the others. A power sensor must be in W or kW, and the meter reading in kWh; PowerPlan refuses a sensor with the wrong unit.

<!-- generated:begin fields:config.meter_roles · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Power now | What the whole home draws from the grid right now. |
| Meter reading | The meter's total reading in kWh, bought from the grid. |
| Export reading | The total kWh sold back, if you have solar panels. |
| Solar power now | What your solar panels produce right now. |
| The meter's own hour value | Some meters publish this hour's use themselves. |
| Per phase › Current L1 | Amps on phase 1; only useful with all three. |
| Per phase › Current L2 | Amps on phase 2. |
| Per phase › Current L3 | Amps on phase 3. |
<!-- generated:end fields:config.meter_roles -->

<a name="electrical"></a>
### How big is your main fuse?

The voltage decides how PowerPlan turns amps into watts: most older Norwegian houses are 230 V IT without a neutral, newer ones and most of Europe 400 V TN. The main fuse is the largest current your home may draw, per phase. It says on the main fuse in the fuse box, or on your grid bill. If you choose **Don't know**, PowerPlan assumes the most common size for your country and says so on the home's device, so you can correct it later.

<!-- generated:begin fields:config.electrical · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Country | Home Assistant has no country set, so it is asked here. |
| Voltage | It says on the fuse box or your grid bill. |
| Main fuse | It is printed on the main fuse in the fuse box. |
| Advanced › Per-phase limit | Only if your grid company set a lower limit per phase than the fuse. |
| Advanced › Phases | How many phases the home is connected with. |
<!-- generated:end fields:config.electrical -->

## Prices

<a name="prices"></a>
### What contract do you have with your electricity supplier?

Your supplier sells you the energy. Choose how you pay for it: the spot price from Nord Pool, a spot price from a sensor you already have, a fixed price, or a state scheme such as Norgespris. Your grid company's tariff comes on top and is asked later.

<!-- generated:begin fields:config.prices · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Contract | Spot price follows the market hour by hour; Norgespris is the state's fixed price. |
<!-- generated:end fields:config.prices -->

<a name="prices_nordpool"></a>
### Which price area are you in?

PowerPlan reads prices through the Nord Pool integration, so that integration must be set up first. When you have only one Nord Pool area, PowerPlan picks it without asking. Tomorrow's prices usually arrive early in the afternoon.

<!-- generated:begin fields:config.prices_nordpool · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Nord Pool integration | Which Nord Pool entry to read prices through. |
| Price area | Where your home is; it is on your electricity bill. |
| Advanced › Publication time zone | Advanced: the market's publication time zone; leave empty to use the price area's. |
| Advanced › Publication time | Advanced: when tomorrow's prices usually appear; leave empty to use the price area's. |
<!-- generated:end fields:config.prices_nordpool -->

<a name="prices_entity"></a>
### Which sensor has your prices?

Pick the sensor that already carries a price curve, such as one from Tibber, Octopus Energy or ENTSO-e. PowerPlan recognizes the common formats from the integration behind the sensor. Leave **Format** empty to use the one it detects; change it only if the prices come out wrong.

<!-- generated:begin fields:config.prices_entity · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Price sensor | The sensor whose attributes hold the hours and their prices. |
| Format | Detected from the sensor's integration; change it only if the prices come out wrong. |
<!-- generated:end fields:config.prices_entity -->

<a name="prices_format"></a>
### How are these prices written?

PowerPlan asks this when it cannot tell everything from the sensor. For a sensor it does not recognize, say which attribute holds the list of prices and which keys hold the start, the end and the price. For Octopus Energy, check that **Tomorrow's prices** points at the next day's rates.

<!-- generated:begin fields:config.prices_format · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Attribute with the prices | The attribute that holds the list of prices. |
| Attribute with tomorrow's prices | Leave empty when tomorrow is in the same list. |
| Key for the start time | The key in each row that holds when the price starts. |
| Key for the end time | Leave empty when the rows have only a start. |
| Key for the price | The price's key; use dots for a nested one, such as price.amount. |
| Currency | The three-letter code, for example NOK or EUR. |
| Priced per | Whether a price is for one kWh or one MWh. |
| Written in | Whole units, such as kroner and euros, or hundredths, such as øre and cents. |
| Multiply every price by | 1 unless the prices are written in a fraction of a unit. |
| Name of today's hour attributes | The part before the hour, for example price_ for price_07h. |
| Name of tomorrow's hour attributes | Leave empty when the sensor has no hours for tomorrow. |
| Tibber home | The home to plan for, when your account has more than one. |
| Prices include VAT | Leave on unless you pay no VAT. |
| Tomorrow's prices | The sensor with tomorrow's prices, usually found for you. |
| Advanced › Publication time zone | Advanced: the market's publication time zone; leave empty to use the price area's. |
| Advanced › Publication time | Advanced: when tomorrow's prices usually appear; leave empty to use the price area's. |
| Advanced › Price interval | Advanced: how long each price lasts. |
| Advanced › Price interval | Advanced: how long each price lasts. |
| Advanced › Minutes per price | Advanced: how long each price lasts. |
| Advanced › Priced per | Advanced: whether a price is for one kWh or one MWh. |
<!-- generated:end fields:config.prices_format -->

<a name="prices_fixed"></a>
### What do you pay per kWh?

Enter the price per kWh on your bill, in øre, cents or the smallest unit of your currency. With a fixed price there is no cheaper hour to move to, so PowerPlan keeps you under your target and counts your cost, and nothing else.

<!-- generated:begin fields:config.prices_fixed · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Price per kWh | In øre (or cents) per kWh, as on your bill. |
<!-- generated:end fields:config.prices_fixed -->

## What your supplier adds

<a name="modifiers"></a>
### What does your supplier add, on top of the grid tariff?

Your grid company's tariff and the taxes are already part of the price. Tick only what your contract with the supplier adds on top. Each price add-on you tick gets its own question next.

<!-- generated:begin fields:config.modifiers · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Added by the supplier | Leave one out and it is simply not part of your price. |
<!-- generated:end fields:config.modifiers -->

<a name="modifier_spot_scale"></a>
### Does your supplier add a markup?

A markup is what your supplier adds per kWh on top of the spot price. It is on your electricity bill or in your contract. The factor multiplies the spot price, for contracts that charge a percentage.

<!-- generated:begin fields:config.modifier_spot_scale · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Multiplier | Your supplier's factor on the spot price; 1 means no change. |
| Markup per kWh | Added to every kWh, in your currency. |
<!-- generated:end fields:config.modifier_spot_scale -->

<a name="modifier_fixed_price"></a>
### What is your fixed price?

A state scheme such as Norgespris replaces the spot price with a fixed price, up to a monthly amount. Enter the price and the monthly cap from your agreement. Norgespris and strømstøtte cannot be combined.

<!-- generated:begin fields:config.modifier_fixed_price · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Price per kWh | In øre (or cents) per kWh, without VAT; VAT is added on top. |
| Cap per month | How many kWh a month the fixed price covers; spot applies above it. |
<!-- generated:end fields:config.modifier_fixed_price -->

<a name="modifier_supplier_tou"></a>
### What does your supplier charge by time of day?

Only your supplier's own day and night prices, if your contract has them. Your grid company's day and night charge is part of the grid tariff and is not asked here.

<!-- generated:begin fields:config.modifier_supplier_tou · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Periods | The periods and their prices per kWh. |
| Price for other hours | Used for an hour no period covers. |
<!-- generated:end fields:config.modifier_supplier_tou -->

<a name="modifier_day_type"></a>
### What do the special days cost?

Some tariffs price announced days differently, such as Tempo's red, white and blue days or a critical peak day. Pick the sensor that says what type the day is, and give each day type the name that sensor writes, with its price. Set **Days ahead the sensor announces** to 1 if the sensor shows tomorrow's type today.

<!-- generated:begin fields:config.modifier_day_type · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Day types | The announced day types and what each costs. |
| Normal day type | The day type used when nothing is announced. |
| Sensor that announces the day | The sensor whose state is the day's type, such as tomorrow's Tempo color. |
| Advanced › Days ahead the sensor announces | Advanced: 1 when the sensor shows tomorrow's type today, 0 when it shows today's. |
<!-- generated:end fields:config.modifier_day_type -->

<a name="modifier_cumulative_tier"></a>
### How does the price rise with use?

Some contracts charge more per kWh once your use in the month passes a band. Enter each band's upper limit in kWh and its price. The bands are in your agreement.

<!-- generated:begin fields:config.modifier_cumulative_tier · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Tiers | The use bands and their prices. |
| Counted over | Whether the bands count a month's use or a year's. |
<!-- generated:end fields:config.modifier_cumulative_tier -->

## Taxes and support schemes

<a name="state"></a>
### Which support schemes apply to you?

PowerPlan knows the VAT and the electricity taxes for your country and shows them here with their source. It asks only which support schemes apply to you, such as strømstøtte. If PowerPlan does not know your country, it asks for the VAT rate.

<!-- generated:begin fields:config.state · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Schemes | Strømstøtte is paid automatically on your grid bill; it does not apply with Norgespris. |
| VAT | PowerPlan has no VAT rate for your country: enter the one on your bill. |
<!-- generated:end fields:config.state -->

## Export and other heat sources

<a name="export"></a>
### Do you sell electricity back?

If you sell electricity back to the grid, choose how you are paid for it. PowerPlan uses the export price to decide whether charging a battery beats selling. Choose **I don't export** if you have no solar panels or battery.

<!-- generated:begin fields:config.export · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Export price | How your supplier pays for what you send out. |
<!-- generated:end fields:config.export -->

<a name="export_amounts"></a>
### What do they pay you per kWh?

Enter what you are paid per kWh, as your contract states it. A negative export price is possible, and PowerPlan plans with it.

<!-- generated:begin fields:config.export_amounts · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Amount per kWh | The fixed price, or what is subtracted from spot. |
| Share of spot | As a percent: 90 is 90 % of spot. |
<!-- generated:end fields:config.export_amounts -->

<a name="carriers"></a>
### Do you also heat with gas, district heating, oil or pellets?

If you also heat with gas, district heating, oil or pellets, PowerPlan can compare that heat source with a heat pump. Leave it empty if you only buy electricity.

<!-- generated:begin fields:config.carriers · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Other heat sources | Each one asks for its price next. |
<!-- generated:end fields:config.carriers -->

<a name="carrier_gas"></a>
### What does gas cost?

The price per kWh of heat delivered, from your bill or agreement. PowerPlan compares it with the price of heat from a heat pump.

<!-- generated:begin fields:config.carrier_gas · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Price source | A number you type, or a sensor read once a day. |
| Price per kWh | In your currency, per kWh of delivered energy. |
| Price sensor | The sensor with today's price, if you chose a sensor. |
<!-- generated:end fields:config.carrier_gas -->

<a name="carrier_district_heat"></a>
### What does district heating cost?

The price per kWh of heat delivered, from your bill or agreement.

<!-- generated:begin fields:config.carrier_district_heat · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Price source | A number you type, or a sensor read once a day. |
| Price per kWh | In your currency, per kWh of delivered energy. |
| Price sensor | The sensor with today's price, if you chose a sensor. |
<!-- generated:end fields:config.carrier_district_heat -->

<a name="carrier_oil"></a>
### What does heating oil cost?

The price per kWh of heat delivered, from your bill or agreement. Convert from liters with the heat your supplier states per liter.

<!-- generated:begin fields:config.carrier_oil · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Price source | A number you type, or a sensor read once a day. |
| Price per kWh | In your currency, per kWh of delivered energy. |
| Price sensor | The sensor with today's price, if you chose a sensor. |
<!-- generated:end fields:config.carrier_oil -->

<a name="carrier_pellets"></a>
### What do pellets cost?

The price per kWh of heat delivered, from your bill or agreement. Convert from kilograms with the heat your supplier states per kilogram.

<!-- generated:begin fields:config.carrier_pellets · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Price source | A number you type, or a sensor read once a day. |
| Price per kWh | In your currency, per kWh of delivered energy. |
| Price sensor | The sensor with today's price, if you chose a sensor. |
<!-- generated:end fields:config.carrier_pellets -->

## The grid tariff

<a name="postcode"></a>
### What is your postcode?

Your postcode tells PowerPlan which taxes apply where you live and helps it find your grid company. It is sent only to your country's official address register. Leave it empty to skip.

<!-- generated:begin fields:config.postcode · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Postcode | Leave it empty to skip; you then pick your county if it matters. |
<!-- generated:end fields:config.postcode -->

<a name="tariff"></a>
### Which grid company do you have?

Your grid company owns the power lines where you live; you do not choose it. PowerPlan fetches its tariff for you where an open source has it, and refreshes it every month. If your company is not in the list, choose to enter the tariff yourself. [Tariffs](tariffs.md) lists what PowerPlan fetches in each country.

<!-- generated:begin fields:config.tariff · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Country | Home Assistant has no country set, so it is asked here. |
| Grid company | Pick yours; if it is missing, pick Grid company not listed or Enter it myself. |
<!-- generated:end fields:config.tariff -->

<a name="tariff_product"></a>
### Which grid tariff do you have with {operator}?

Your grid company has more than one tariff for homes. Your grid bill names the one you have.

<!-- generated:begin fields:config.tariff_product · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Tariff |  |
<!-- generated:end fields:config.tariff_product -->

<a name="tariff_zone"></a>
### Which county do you live in?

Your grid company covers areas with different taxes, so PowerPlan needs to know which county you live in.

<!-- generated:begin fields:config.tariff_zone · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| County |  |
<!-- generated:end fields:config.tariff_zone -->

<a name="tariff_confirm"></a>
### Is this right for {operator}?

The source does not say everything about your tariff. Check each value against your grid bill. The suggested value is the usual one.

<!-- generated:begin fields:config.tariff_confirm · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| How your power step is worked out |  |
| A peak exactly on a step's limit belongs to the step above |  |
| These prices match my latest grid bill |  |
| Main fuse (A) |  |
| Demand measured over |  |
| My bill has no power fee |  |
| Hours of the first rate (from-to) |  |
| Months of the first rate (from-to) |  |
| How the demand charge is measured |  |
| Price per kW per day |  |
| Power factor |  |
| Connection power (kVA) |  |
<!-- generated:end fields:config.tariff_confirm -->

<a name="tariff_preset"></a>
### Does this match your grid bill?

PowerPlan shows the tariff it found, as a table of capacity steps and prices. Check it against your grid bill. If it does not match, go back and choose another tariff or enter it yourself.

<!-- generated:begin fields:config.tariff_preset · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Does it match? | No shows the grid companies again, with Enter it myself last. |
<!-- generated:end fields:config.tariff_preset -->

<a name="tariff_bills"></a>
### What were your peaks the last twelve months?

This tariff averages your highest use over a rolling year, so PowerPlan needs what you already used. Fill in the peaks your invoices show for the last twelve months. Every field is optional, and PowerPlan says when it knows too little.

<!-- generated:begin fields:config.tariff_bills · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Month 1 | The highest measured value that month, in kW. |
| Month 2 | The highest measured value that month, in kW. |
| Month 3 | The highest measured value that month, in kW. |
| Month 4 | The highest measured value that month, in kW. |
| Month 5 | The highest measured value that month, in kW. |
| Month 6 | The highest measured value that month, in kW. |
| Month 7 | The highest measured value that month, in kW. |
| Month 8 | The highest measured value that month, in kW. |
| Month 9 | The highest measured value that month, in kW. |
| Month 10 | The highest measured value that month, in kW. |
| Month 11 | The highest measured value that month, in kW. |
| Month 12 | The highest measured value that month, in kW. |
<!-- generated:end fields:config.tariff_bills -->

<a name="tariff_steps"></a>
### What steps does your grid company have?

Your grid company is not in the list, so enter its capacity steps from your grid bill. For each step, enter the power it goes up to and what it costs per month, with VAT. Leave the rest empty; the last step you fill in has no upper limit.

<!-- generated:begin fields:config.tariff_steps · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Step 1: up to | The power this step goes up to, as on your bill. |
| Step 1: per month | What this step costs per month, as on your bill. |
| Step 2: up to | The power this step goes up to, as on your bill. |
| Step 2: per month | What this step costs per month, as on your bill. |
| Step 3: up to | The power this step goes up to, as on your bill. |
| Step 3: per month | What this step costs per month, as on your bill. |
| Step 4: up to | The power this step goes up to, as on your bill. |
| Step 4: per month | What this step costs per month, as on your bill. |
| Step 5: up to | The power this step goes up to, as on your bill. |
| Step 5: per month | What this step costs per month, as on your bill. |
| Step 6: up to | The power this step goes up to, as on your bill. |
| Step 6: per month | What this step costs per month, as on your bill. |
| Step 7: up to | The power this step goes up to, as on your bill. |
| Step 7: per month | What this step costs per month, as on your bill. |
| Step 8: up to | The power this step goes up to, as on your bill. |
| Step 8: per month | What this step costs per month, as on your bill. |
| Step 9: up to | The power this step goes up to, as on your bill. |
| Step 9: per month | What this step costs per month, as on your bill. |
| Step 10: up to | The power this step goes up to, as on your bill. |
| Step 10: per month | What this step costs per month, as on your bill. |
| Step 11: up to | The power this step goes up to, as on your bill. |
| Step 11: per month | What this step costs per month, as on your bill. |
| Step 12: up to | The power this step goes up to, as on your bill. |
| Step 12: per month | What this step costs per month, as on your bill. |
| VAT on these prices | PowerPlan has no VAT rate for your country, so enter the one your prices include. |
<!-- generated:end fields:config.tariff_steps -->

<a name="tariff_limits"></a>
### How much power have you contracted?

Your tariff sets a contracted power limit per period, and the meter cuts the power if you go over it. The values shown are a common starting point; change them to what your contract says.

<!-- generated:begin fields:config.tariff_limits · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Limit 1 | The contracted power for this period. |
| Limit 2 | The contracted power for this period. |
| Limit 3 | The contracted power for this period. |
| Limit 4 | The contracted power for this period. |
<!-- generated:end fields:config.tariff_limits -->

<a name="tariff_target"></a>
### Which capacity step do you want to stay in?

Your target is the capacity step you want to stay in. **Automatic** keeps you in the step you have already reached this month, so a month's first peak does not push every later hour up. **How strict** decides whether PowerPlan may use hours you have already paid for: **Strict** never goes above the target.

<!-- generated:begin fields:config.tariff_target · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Capacity step | Automatic, or a specific step with its monthly fee. |
| How strict | Strict never goes above the target; the others may, within your ‹hours› highest hours. |
| Target | The kW to defend, for a tariff with no steps. |
| Advanced › Guard band | Advanced: kWh of each window held back for meter delay and reaction time. |
| Advanced › Free-ride margin | Advanced: how far above the target the free ride may reach. |
<!-- generated:end fields:config.tariff_target -->

## Presence and messages

<a name="presence"></a>
### Is anyone home?

Comfort matters less in an empty home. With **Automatic**, PowerPlan follows the people you pick next and lowers comfort while everyone is away. With the manual choice, you or your automations set presence with the **Presence** entity or an action.

<!-- generated:begin fields:config.presence · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Presence | Automatic follows the people you pick next; otherwise an action or the presence entity decides. |
| Advanced › Away after | Advanced: time before an empty house counts as away. |
<!-- generated:end fields:config.presence -->

<a name="presence_persons"></a>
### Whose being away counts?

Pick everyone whose being away makes the home away. The home counts as away when all of them have left, after the delay you set.

<!-- generated:begin fields:config.presence_persons · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| People | Everyone whose being away makes the house away. |
<!-- generated:end fields:config.presence_persons -->

<a name="notifications"></a>
### What should PowerPlan tell you?

PowerPlan tells you about three things by default: a coming peak, comfort that could not be kept, and a device that stopped responding. Everything else is an entity you can put on a dashboard. A persistent notification always works; pick a notify service to get messages on your phone, and set quiet hours if you like.

<!-- generated:begin fields:config.notifications · tools/docs.py writes this block; change strings.json, not this table -->
| Question | What it asks |
|---|---|
| Peak warning | This hour is heading over your capacity target. |
| Comfort missed | A room or a tank went below the floor you set. |
| Device not responding | A controlled device stopped answering. |
| Notify service | Which notify service the categories set to Send it to my phone use. |
| Quiet from | Nothing but the urgent gets through after this. |
| Quiet until | Normal notifications resume at this time. |
| Advanced › Peak from appliances PowerPlan does not control | The peak is coming from something PowerPlan does not steer. |
| Advanced › Deadline at risk | A charge or a cycle will not finish in time. |
| Advanced › Capacity step about to rise | The month is about to move into a more expensive step. |
| Advanced › Price source silent | No prices have arrived for a day. |
| Advanced › Legionella cycle at risk | A water heater's protection cycle cannot complete. |
| Advanced › Fallback mode | PowerPlan stopped steering and left every appliance as it was. |
| Advanced › Run now ended | A run now you started ran out and normal control resumed. |
| Advanced › Daily price summary | Tomorrow's prices, once a day. |
<!-- generated:end fields:config.notifications -->

## Before you finish

The last screen explains in words what PowerPlan derived from your answers. **Start in trial mode** is on by default: PowerPlan then shows what it would do and changes nothing until you switch on **Automatic control**.

**See also:** [Get started](get-started.md) · [Circuits, groups and rooms](circuits-groups-rooms.md) · [Tariffs](tariffs.md) · [Glossary](glossary.md)
