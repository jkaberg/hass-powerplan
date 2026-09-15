// PowerPlan's dashboard module (D12 §3, §5.5), a Lovelace resource the
// integration keeps (§5.16 R4; `frontend.add_extra_js_url` on every page where
// resources are YAML): the strategy HA lists in "Add dashboard" and the cards
// its layout names.
//
// The strategy element is defined first, at the module's top level, with
// nothing to fetch before it: HA gives a strategy element a few seconds to
// appear, and a cold load of a deep path used to miss that (D12 §5.10, B1).
// The cards and ECharts come behind a dynamic import.

import "./bundle";
import { PowerplanDashboardStrategy } from "./strategy";
import { installStrategies } from "./strategy-shim";

/** The cards' module, named by its content hash; the build puts the name in (esbuild `define`). */
declare const CARDS_MODULE: string;
/** The build's hash, logged so Q/A can tell which bundle the browser runs. */
declare const BUILD_HASH: string;

const DOCS = "https://github.com/jkaberg/hass-powerplan/blob/main/docs/dashboard.md";

interface Registry<T> {
  customCards?: T[];
  customStrategies?: T[];
}

// Iteration 5: the newest bundle on the page answers `generate()`, whichever defined the element first.
installStrategies({ "ll-strategy-dashboard-powerplan": PowerplanDashboardStrategy }, () => undefined);
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
    {
      type: "powerplan-appliances-card",
      name: "PowerPlan appliances",
      description: "One row per appliance with its status and its next 24 hours as a lane.",
      documentationURL: DOCS,
    },
    {
      type: "powerplan-price-card",
      name: "PowerPlan electricity price",
      description: "Your price today and tomorrow, and what it would be without a fixed price.",
      documentationURL: DOCS,
    },
    {
      type: "powerplan-attention-card",
      name: "PowerPlan – Trenger oppmerksomhet",
      description: "PowerPlan repairs and meter status, only when something is wrong.",
      documentationURL: DOCS,
    },
    {
      type: "powerplan-month-bars",
      name: "PowerPlan – Kostnad per dag",
      description: "Daily cost on a fixed axis for the whole month.",
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
