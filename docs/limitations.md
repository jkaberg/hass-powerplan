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

- PowerPlan plans on the solar forecast your energy dashboard shows. Without one, it uses your solar power only as it comes, not ahead.
- PowerPlan never limits your panels or what they send to the grid.
- A home battery is steered through its inverter's integration: [Devices](devices.md) lists the ones PowerPlan knows. Home Assistant's own Tesla Powerwall integration, which talks to the Powerwall on your network, offers no way to steer the battery: use Tesla Fleet, Teslemetry or Tessie. PowerPlan cannot make a Powerwall discharge; it covers your home by itself.
- Some batteries cannot hold their charge for later: their integration has no setting that stops them discharging. PowerPlan then plans as if they run on their own between the hours it charges or discharges them.
- Enphase batteries cannot be steered: from Envoy firmware 8.2.4225 the Envoy refuses the settings Home Assistant would change.

## Homes

- Each home is one electricity meter. A second meter, such as a cabin, is a second home.
- A second charger on one Zaptec installation shares its current and is not planned apart yet.

## Documentation

- The pages are in English. The screens in Home Assistant are in English and Norwegian.

**See also:** [Troubleshooting](troubleshooting.md) · [How it works](how-it-works.md)
