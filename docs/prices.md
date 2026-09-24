<!-- kind: reference -->
[PowerPlan docs](README.md) › Reference › Prices

# Prices

Where PowerPlan reads your electricity price, and the parts it adds to it. Setup asks for both: the price source first, then what your supplier adds.

## Price sources

Most homes read the spot price from Nord Pool. If you already have a price sensor from another integration, PowerPlan reads that instead and detects its format.

<!-- generated:begin formats · tools/docs.py writes this block; change the format registry, not this table -->
| Price source | Integration | Read from | Key |
|---|---|---|---|
| Amber Electric | `amber_electric` | a sensor | `amber` |
| ComEd | `comed_hourly_pricing` | a sensor | `comed` |
| Czech Energy Spot Prices | `cz_energy_spot_prices` | a sensor | `cz_energy_spot_prices` |
| easyEnergy | `easyenergy` | an action's response | `easyenergy_action` |
| Energi Data Service | `energidataservice` | a sensor | `energidataservice` |
| EnergyZero | `energyzero` | an action's response | `energyzero_action` |
| ENTSO-e | `entsoe` | a sensor | `entsoe` |
| EPEX Spot | `epex_spot` | a sensor | `epex_spot` |
| Frank Energie | `frank_energie` | a sensor | `frank_energie` |
| Price list in an attribute | any | a sensor | `generic_list` |
| One attribute per hour | any | a sensor | `hourly_attributes` |
| Nord Pool | `nordpool` | a sensor | `nordpool_core` |
| Nord Pool (HACS) | `nordpool` | a sensor | `nordpool_hacs` |
| Octopus Energy | `octopus_energy` | a sensor | `octopus_energy` |
| PVPC | `pvpc_hourly_pricing` | a sensor | `pvpc` |
| Strømligning | `stromligning` | a sensor | `stromligning` |
| TGE | `tge` | a sensor | `tge` |
| Tibber | `tibber` | an action's response | `tibber_action` |
| Tibber Prices (HACS) | `tibber_prices` | an action's response | `tibber_prices` |
| Zonneplan | `zonneplan_one` | a sensor | `zonneplan_one` |
<!-- generated:end formats -->

<a name="amber"></a>
### Amber Electric

Amber Electric's forecast sensor, in Australian dollars per kWh.

<a name="comed"></a>
### ComEd

ComEd's hourly pricing sensor. It publishes only the current hour, so PowerPlan estimates the rest.

<a name="cz_energy_spot_prices"></a>
### Czech Energy Spot Prices

The Czech Energy Spot Prices integration, in koruna or euros per kWh or MWh.

<a name="easyenergy_action"></a>
### easyEnergy

easyEnergy's Dutch prices, fetched through the integration's own action.

<a name="energidataservice"></a>
### Energi Data Service

Energi Data Service's Danish spot prices, today's and tomorrow's.

<a name="energyzero_action"></a>
### EnergyZero

EnergyZero's Dutch prices, fetched through the integration's own action.

<a name="entsoe"></a>
### ENTSO-e

The ENTSO-e integration's day-ahead prices for your market area.

<a name="epex_spot"></a>
### EPEX Spot

The EPEX Spot integration's market price sensor. Pick the market price, not the total price: the total already holds your own surcharges.

<a name="frank_energie"></a>
### Frank Energie

Frank Energie's prices, all included: market price, markup, energy tax and VAT.

<a name="generic_list"></a>
### Price list in an attribute

Any sensor that holds a list of prices in an attribute. You say which attribute, which keys hold the start, the end and the price, and the unit.

<a name="hourly_attributes"></a>
### One attribute per hour

A sensor with one attribute per hour, such as `price_07h`. You say the name before the hour and the unit.

<a name="nordpool_core"></a>
### Nord Pool

Home Assistant's own Nord Pool integration. PowerPlan reads the whole curve through its action; this format reads only the price now.

<a name="nordpool_hacs"></a>
### Nord Pool (HACS)

The Nord Pool integration from HACS, with today's and tomorrow's prices in attributes.

<a name="octopus_energy"></a>
### Octopus Energy

Octopus Energy's day rates, including tomorrow's from the sensor beside today's.

<a name="pvpc"></a>
### PVPC

Spain's regulated PVPC price, today's and tomorrow's.

<a name="stromligning"></a>
### Strømligning

Strømligning's Danish prices, with the grid tariff, taxes and VAT already included.

<a name="tge"></a>
### TGE

The TGE fixing for Poland, in złoty per MWh.

<a name="tibber_action"></a>
### Tibber

Tibber's prices, fetched through Home Assistant's Tibber integration. If your account has two homes, PowerPlan asks which.

<a name="tibber_prices"></a>
### Tibber Prices (HACS)

The Tibber Prices integration from HACS, one home per setup.

<a name="zonneplan_one"></a>
### Zonneplan

Zonneplan's tariff, with its markup, the energy tax and VAT already included.

<a name="estimated_prices"></a>
## Estimated prices

Before tomorrow's prices are published, PowerPlan estimates them from the recent days' shape, so it can still plan the night. It marks those hours as estimated, and replaces them as soon as the real prices arrive.

## Price add-ons

A price add-on is a part of your price besides the spot price. PowerPlan adds the grid company's and the state's parts by itself; you tick only what your supplier adds.

<!-- generated:begin modifiers · tools/docs.py writes this block; change the add-on registry, not this table -->
| Price add-on | Asks for | Key |
|---|---|---|
| Tiered by monthly use | Tiers, Counted over | `cumulative_tier` |
| Day-type tariff (critical peak days) | Day types, Normal day type, Sensor that announces the day | `day_type` |
| export_price | – | `export_price` |
| Fixed state price (Norgespris) | Price per kWh, Cap per month | `fixed_price` |
| Taxes and levies | – | `levy` |
| Supplier markup | Multiplier, Markup per kWh | `spot_scale` |
| State subsidy (strømstøtte) | – | `subsidy_threshold` |
| Supplier's day and night prices | Periods, Price for other hours | `supplier_tou` |
| Grid energy charge (day/night) | – | `tou_schedule` |
| VAT | – | `vat` |
<!-- generated:end modifiers -->

<a name="cumulative_tier"></a>
### Tiered by monthly use

Your price per kWh rises once your use in the month passes a band. Enter each band's upper limit and price from your agreement.

<a name="day_type"></a>
### Day-type tariff (critical peak days)

Announced days cost more, such as Tempo's red days or a critical peak day. You pick the sensor that says the day's type, and give each type its price.

<a name="export_price"></a>
### Export price

What you are paid for power you sell back: fixed, the spot price minus an amount, a share of the spot price, or from a sensor. Setup asks this in its own question.

<a name="fixed_price"></a>
### Fixed state price (Norgespris)

A state scheme such as Norgespris replaces the spot price with a fixed price, up to a monthly cap. It cannot be combined with strømstøtte.

<a name="levy"></a>
### Taxes and levies

The electricity taxes and levies your country charges per kWh. PowerPlan knows them for most countries and states them in setup, with their source.

<a name="spot_scale"></a>
### Supplier markup

What your supplier adds per kWh on top of the spot price, as an amount, a factor, or both.

<a name="subsidy_threshold"></a>
### State subsidy (strømstøtte)

The state pays part of the spot price above a threshold, such as Norway's strømstøtte.

<a name="supplier_tou"></a>
### Supplier's day and night prices

Your supplier's own day and night prices, if your contract has them. Your grid company's day and night charge is part of the grid tariff.

<a name="tou_schedule"></a>
### Grid energy charge (day/night)

Your grid company's energy charge, often different by day and night. It comes with your grid tariff, and is never asked twice.

<a name="vat"></a>
### VAT

The VAT on electricity. PowerPlan knows the rate for your country, and for places with a different rate.

**See also:** [Set up a home](setup.md#prices) · [Grid tariffs](tariffs.md) · [Glossary](glossary.md)
