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
| Now | anything that needs your attention (repairs, a meter that is not reporting); this hour's usage against its limit; the plan for the next 24 hours, with a button to plan again; one tile per appliance - tap it for the appliance's own page; your capacity step; this month's cost and savings, the price now and whether tomorrow's prices are in; the next runs as a table |
| History | a summary of the month; your usage, with a link to the Energy dashboard; the highest hour per day; savings per appliance; cost per appliance this month; cost and savings over time; what happened. The period picker at the bottom sets every graph, as on the Energy dashboard |
| an appliance's page | its control and status, its own settings (charge to, comfort, ready by, hours per day), its plan for the next 24 hours, why the plan looks the way it does, and this month's cost, savings and energy. The arrow at the top goes back to *Now* |

The three buttons at the top of *Now* are automatic control, presence and your capacity target; tap one to change it. Each appliance keeps one colour on every card. The planned runs are also in Home Assistant's *Calendar* panel as **Planned runs**.

Two cards are PowerPlan's own:

- **The timeline** shows the next 24 hours. The bars are what each appliance is planned to use, the shaded area is the rest of the house, the dashed red line is the capacity limit and the blue line is the price. Everything is in kW, so a quarter-hour and an hour read the same way. A grey band marks prices that are estimated, not yet published. Tap a slot to see its energy, price and cost. Tap a name in the legend to hide it.
- **The capacity gauge** shows how much of this window's limit is used. The needle is where the window is expected to end. Green means all is well, amber means PowerPlan is holding back, and red means it is shedding or the limit is at risk. Tap it for the window's history.

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
  hidden_cards: [markdown, repairs] # any card type
```

## Taking control

*⋮ → Edit dashboard → Take control* turns the dashboard into an ordinary one that you can edit freely. It then no longer follows your setup: an appliance you add later does not appear on it. To go back, delete it and add the PowerPlan dashboard again.

## Your appliances in the Energy dashboard

PowerPlan does not change your Energy settings. To see your appliances in Home Assistant's own device graphs, and in the History tab here, add each appliance's **Energy** sensor under *Settings → Dashboards → Energy → Individual devices*.

## If it does not load

The dashboard then shows one card that says why. See [troubleshooting](troubleshooting.md).
