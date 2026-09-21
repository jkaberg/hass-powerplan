<!-- kind: help -->
[PowerPlan docs](README.md) › Help › Limitations

# Limitations

What PowerPlan does not do, or not yet. Each item says what to do meanwhile, where there is something.

## Meter and prices

- PowerPlan needs a power reading from your electricity meter to protect the fuse and the capacity step. Without one, choose **Cheapest hours only**.
- A meter that reports less often than every minute makes PowerPlan react later; keep a safety margin.
- Where no price source publishes tomorrow's prices, PowerPlan plans on estimates.

## Appliances

- Chargers with only a mode, such as myenergi zappi and Ohme, and charging through the car's own integration, are not supported yet. [Devices](devices.md) lists what is.
- A charger whose integration adds no device, such as KEBA, cannot be added.
- A dishwasher, washer or dryer without remote start can only be told the best time, not started.
- A heat pump plans heating or cooling as you set it; switch **Plan for cooling** yourself when the season changes.

## Solar and batteries

- Solar panels count only as what the meter sees. Planning around your own production, and a battery together with solar, come in a later version.

## Homes

- Each home is one electricity meter. A second meter, such as a cabin, is a second home.
- A second charger on one Zaptec installation shares its current and is not planned apart yet.

## Documentation

- The pages are in English. The screens in Home Assistant are in English and Norwegian.

**See also:** [Troubleshooting](troubleshooting.md) · [How it works](how-it-works.md)
