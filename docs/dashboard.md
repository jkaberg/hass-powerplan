# The PowerPlan dashboard

PowerPlan ships a dashboard that shows your home's past, present and future: what the power costs, what each appliance will run and when, and how close you are to the next capacity step. It looks like Home Assistant's own Energy dashboard and uses Home Assistant's own cards wherever it can.

## Add the dashboard

**Home Assistant 2026.5 or newer:** *Settings → Dashboards → Add dashboard*, choose **PowerPlan**, and give it a name and an icon.

**Home Assistant 2026.3 or 2026.4:** the dialog does not list it yet. Add an empty dashboard, open it, choose *⋮ → Edit dashboard → ⋮ → Raw configuration editor*, and replace everything with:

```yaml
strategy:
  type: custom:powerplan
```

## What you see

| tab | shows, top to bottom |
|---|---|
| Now | anything that needs your attention - PowerPlan's repairs and a meter that lags or is silent, each with one line of why and a button; nothing when all is well; this hour's usage against its limit; your electricity price today and tomorrow - with a fixed price such as Norgespris, also what it would be without it - and whether tomorrow's prices are in; when this hour's price is not known, why, and a button to fetch it again; the whole house for the next 24 hours, hour by hour, with a button to plan again; one row per appliance with its status and its next start - tap it for its controls, its plan and why, and from there its own page; your capacity step; this month's cost and savings over a bar for each day of the month |
| History | a summary of the period - cost, savings, energy from the grid and your capacity basis or highest hour; your usage hour by hour or day by day, with a link to the Energy dashboard; each day's highest hour and whether it counts toward your capacity step; savings per appliance; cost per appliance; cost and savings over time; what happened. The period picker at the bottom sets every graph, as on the Energy dashboard |
| an appliance's page | its control and status - with the reason for what it is doing now, its own settings (charge to, comfort, ready by, hours per day), its plan for the next 24 hours, why the plan looks the way it does, and this month's cost, savings and energy. The arrow at the top goes back to *Now* |

The three buttons at the top of *Now* are automatic control, presence and your capacity target; tap one to change it. Each appliance keeps one colour on every card. The planned runs are also in Home Assistant's *Calendar* panel as **Planned runs**.

In the Plan card on *Now*, each bar is an hour: grey is the rest of the house, the darker grey is what keeps rooms and water at temperature, and the coloured part is what PowerPlan moved in time. The dashed cap over a bar is what the hour *could reach* on a busy day, the red dashed line is your power target, and green columns are the cheapest hours. On a row in the appliances card, a solid block is a run PowerPlan moved, the empty track means it only holds its temperature, and a hatch means it is lowered in expensive hours.

When PowerPlan is updated while a tab is open, Home Assistant shows **PowerPlan was updated. Reload the page to use the new version.** with a **Reload** button.

Two cards are PowerPlan's own:

- **The timeline** shows the next 24 hours (12 on a phone; the buttons above it switch between 12, 24 and 48). Each appliance's bar sits on top of the grey area, the rest of the house, so the top of a bar is the total - compare it with the dashed red line, your power target. The strip under the times is the price: darker is dearer, and a hatched strip means the price is estimated, not yet published. Everything is in kW, so a quarter-hour and an hour read the same way. Point at a slot, or tap it on a phone, to see the appliances, the sum against the target, the price and the cost. Tap a name below the chart to hide it. On an appliance's page the timeline shows that appliance alone, with a line at the time it must be ready.
- **The capacity gauge** shows this hour's usage against its limit. The pale part of the arc and the small tick show where the hour is expected to end. The label at the top says *Normal*, *Tight* or *Critical* - or when a peak is expected - and the three figures at the bottom are the expected total, the minutes left of the hour and the power still available. Tap it for the hour's history.

## It follows your setup

The dashboard is rebuilt each time it opens. A new appliance, or a setting you turn on or off on an appliance's page, shows up the next time you open the dashboard. A row that is off or hidden is left out.

If you have **more than one PowerPlan home**, each gets its own tabs and appliance pages, named after the home, for example *Cabin · Now*. To show one home only, add its entry id:

```yaml
strategy:
  type: custom:powerplan
  entry_id: 01J…   # the id at the end of the address when you open the home under Settings → Devices & services → PowerPlan
```

## Options

```yaml
strategy:
  type: custom:powerplan
  hidden_views: [history]          # overview, history, appliances (every appliance page)
  hidden_cards: [markdown]         # any card type
```

## Taking control

*⋮ → Edit dashboard → Take control* turns the dashboard into an ordinary one that you can edit freely. It then no longer follows your setup: an appliance you add later does not appear on it. To go back, delete it and add the PowerPlan dashboard again.

## Your appliances in the Energy dashboard

PowerPlan does not change your Energy settings. To see your appliances in Home Assistant's own device graphs, and in the History tab here, add each appliance's **Energy** sensor under *Settings → Dashboards → Energy → Individual devices*.

## If it does not load

The dashboard then shows one card that says why. See [troubleshooting](troubleshooting.md).
