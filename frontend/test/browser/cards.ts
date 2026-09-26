// The cards the fit harness mounts (D9 §5.16): each as `layout.py` lays it out on the reference house -
// its tag, its config, its section span and, for a fixed height, its grid rows.

import nb from "../../../custom_components/powerplan/translations/nb.json";
import { color, E, LOADS, loadEntity } from "../fixtures/house";

/** The custom cards' own words, as `layout._labels` passes them. */
const labels = Object.fromEntries(
  Object.entries(nb.selector.dashboard.options as Record<string, string>)
    .filter(([k]) => k.startsWith("card_") || k.startsWith("summary_"))
    .map(([k, v]) => [k.replace(/^card_/, ""), v]),
);

const loads = LOADS.map((l, i) => ({ id: l.id, name: l.name, color: color(i) }));
const RUN_KINDS = new Set(["ev", "water_heater", "appliance_cycle", "generic_switch", "battery"]);

export interface Spec { name: string; tag: string; config: Record<string, unknown>; span: number; rows?: number }

export const SPECS: Spec[] = [
  { name: "hour gauge", tag: "powerplan-window-card", span: 1, rows: 6, config: { entry_id: "e", mode: "hour", labels,
    entities: { window_used: E.window_used, window_projected: E.window_projected, ceiling: E.ceiling, allowance: E.allowance, stage: E.stage, peak_warning: E.peak_warning, next_peak_warning: E.next_peak_warning } } },
  { name: "capacity step", tag: "powerplan-window-card", span: 1, config: { entry_id: "e", mode: "month", labels,
    entities: { metric: E.metric, level: E.level, projected_level: E.projected_level, advice: E.advice, target: E.target, savings: E.savings, window_used: E.window_used, plan: E.plan } } },
  { name: "peaks", tag: "powerplan-window-card", span: 1, config: { entry_id: "e", mode: "peaks", labels, grid_entities: [E.grid],
    entities: { window_used: E.window_used, ceiling: E.ceiling, advice: E.advice, level: E.level, target: E.target, stage: E.stage },
    loads: LOADS.filter((l) => RUN_KINDS.has(l.kind)).map((l) => ({ id: l.id, name: l.name, status: loadEntity(l.slug, "planstatus") })) } },
  { name: "price", tag: "powerplan-price-card", span: 2, config: { entry_id: "e",
    entities: { price: E.price, price_forecast: E.price_forecast, fixed_price_savings: E.fixed_price_savings, refresh: E.refresh, plan: E.plan },
    loads: loads.map(({ id, name }) => ({ id, name })) } },
  { name: "plan", tag: "powerplan-timeline-card", span: 3, config: { entry_id: "e", hours: 24, hours_options: [24, 48], narrow_hours: 12, loads,
    entities: { plan: E.plan, price_forecast: E.price_forecast }, show: ["plan", "baseline", "reserve", "ceiling"], currency: "NOK", labels, rail_width: 256, bucket: "window" } },
  { name: "appliances", tag: "powerplan-appliances-card", span: 3, config: { entry_id: "e", entities: { plan: E.plan, price_forecast: E.price_forecast }, hours: 24, rail_width: 256, currency: "NOK",
    loads: LOADS.map((l, i) => ({ id: l.id, name: l.name, color: color(i), icon: "mdi:heating-coil", kind: l.kind, status: loadEntity(l.slug, "planstatus"),
      control: loadEntity(l.slug, "styring"), cost: loadEntity(l.slug, "kostnad_denne_maneden"), savings: loadEntity(l.slug, "besparelse_denne_maneden"), energy: loadEntity(l.slug, "energi_totalt") })) } },
  { name: "month", tag: "powerplan-month-bars", span: 1, config: { entity: E.cost, savings: E.savings, deviations: E.deviations,
    load_names: Object.fromEntries(LOADS.map((l) => [l.id, l.name])) } },
  { name: "attention", tag: "powerplan-attention-card", span: 3, config: { meter_status: E.meter_health } },
  { name: "summary", tag: "powerplan-period-summary", span: 3, config: { entry_id: "e", grid_entities: [E.grid], labels,
    entities: { cost: E.cost, savings: E.savings, metric: E.metric, level: E.level, window_used: E.window_used, advice: E.advice } } },
  { name: "cost table", tag: "powerplan-period-summary", span: 2, config: { entry_id: "e", view: "table", currency: "NOK", labels,
    loads: LOADS.map((l, i) => ({ id: l.id, name: l.name, color: color(i), cost_month: loadEntity(l.slug, "kostnad_denne_maneden"), savings_month: loadEntity(l.slug, "besparelse_denne_maneden") })) } },
  { name: "usage", tag: "powerplan-timeline-card", span: 2, config: { entry_id: "e", mode: "history", grid_entities: [E.grid], currency: "NOK", labels,
    entities: { window_used: E.window_used, ceiling: E.ceiling, price: E.price, price_forecast: E.price_forecast, advice: E.advice, stage: E.stage } } },
  { name: "appliance plan", tag: "powerplan-timeline-card", span: 2, config: { entry_id: "e", hours: 24, hours_options: [12, 24, 48], currency: "NOK", labels,
    loads: [loads[1]], entities: { plan: E.plan, price_forecast: E.price_forecast, deadline: loadEntity(LOADS[1].slug, "planstatus") }, show: ["plan", "price"] } },
  { name: "energy ring", tag: "powerplan-period-summary", span: 1, config: { entry_id: "e", view: "energy", grid_entities: [E.grid], labels,
    loads: LOADS.map((l, i) => ({ id: l.id, name: l.name, color: color(i), energy: loadEntity(l.slug, "energi_totalt"), savings_month: loadEntity(l.slug, "besparelse_denne_maneden") })) } },
  { name: "day profile", tag: "powerplan-day-profile", span: 2, config: { entry_id: "e", entities: { savings: E.savings }, labels } },
  ...[7, 1].map((i) => {
    const l = LOADS[i]!;
    return { name: `level ${l.slug}`, tag: "powerplan-level-card", span: 1, config: { entry_id: "e", labels,
      load: { id: l.id, name: l.name, color: color(i), kind: l.kind }, level: { entity: loadEntity(l.slug, "temp"), unit: "°C" },
      entities: { status: loadEntity(l.slug, "planstatus"), plan: E.plan } } };
  }),
  { name: "next runs", tag: "powerplan-runs-card", span: 1, config: { entry_id: "e", entities: { plan: E.plan }, labels,
    loads: LOADS.map((l, i) => ({ id: l.id, name: l.name, color: color(i), status: loadEntity(l.slug, "planstatus") })) } },
];
