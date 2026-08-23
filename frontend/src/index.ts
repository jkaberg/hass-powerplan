// PowerPlan's dashboard module (D12 §3, §5.5), loaded on every page by
// `frontend.add_extra_js_url`: the strategy HA lists in "Add dashboard" and
// the cards its layout names.
//
// The strategy element is defined first, at the module's top level, with
// nothing to fetch before it: HA gives a strategy element a few seconds to
// appear, and a cold load of a deep path used to miss that (D12 §5.10, B1).
// The cards and ECharts come behind a dynamic import.

import { PowerplanDashboardStrategy } from "./strategy";

/** The cards' module, named by its content hash; the build puts the name in (esbuild `define`). */
declare const CARDS_MODULE: string;
/** The build's hash, logged so Q/A can tell which bundle the browser runs. */
declare const BUILD_HASH: string;

const DOCS = "https://github.com/jkaberg/hass-powerplan/blob/main/docs/dashboard.md";

interface Registry<T> {
  customCards?: T[];
  customStrategies?: T[];
}

if (!customElements.get("ll-strategy-dashboard-powerplan")) {
  customElements.define("ll-strategy-dashboard-powerplan", PowerplanDashboardStrategy);
}
console.info("PowerPlan frontend", BUILD_HASH);

const registry = window as unknown as Registry<{ type: string } & Record<string, unknown>>;
registry.customCards ??= [];
registry.customStrategies ??= [];
if (!registry.customStrategies.some((entry) => entry.type === "powerplan")) {
  registry.customCards.push(
    {
      type: "powerplan-timeline-card",
      name: "PowerPlan timeline",
      description: "Prices, plans and the capacity limit for the next 12–48 hours.",
      documentationURL: DOCS,
    },
    {
      type: "powerplan-window-card",
      name: "PowerPlan capacity gauge",
      description: "This hour's usage, or the month's capacity step, against its limit.",
      documentationURL: DOCS,
    },
    {
      type: "powerplan-period-summary",
      name: "PowerPlan period summary",
      description: "Cost, savings, grid energy and the capacity step for the period the History picker shows.",
      documentationURL: DOCS,
    },
    {
      type: "powerplan-runs-card",
      name: "PowerPlan next runs",
      description: "Each appliance's next planned run, its energy and its cost.",
      documentationURL: DOCS,
    },
  );
  registry.customStrategies.push({
    type: "powerplan",
    strategyType: "dashboard",
    name: "PowerPlan",
    description: "Prices, plans and capacity for your home, in the Energy dashboard's shape.",
    documentationURL: DOCS,
  });
}

void import(/* @vite-ignore */ new URL(CARDS_MODULE, import.meta.url).href);
