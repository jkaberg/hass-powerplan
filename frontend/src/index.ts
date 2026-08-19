// PowerPlan's dashboard module (D12 §3, §5.5), loaded on every page by
// `frontend.add_extra_js_url`: the strategy HA lists in "Add dashboard" and
// the two cards its layout names.

import { PowerplanDashboardStrategy } from "./strategy";
import { PowerplanTimelineCard } from "./timeline-card";
import { PowerplanWindowCard } from "./window-card";

const DOCS = "https://github.com/jkaberg/hass-powerplan/blob/main/docs/dashboard.md";

interface Registry<T> {
  customCards?: T[];
  customStrategies?: T[];
}

const define = (name: string, element: CustomElementConstructor) => {
  if (!customElements.get(name)) customElements.define(name, element);
};

define("ll-strategy-dashboard-powerplan", PowerplanDashboardStrategy);
define("powerplan-timeline-card", PowerplanTimelineCard);
define("powerplan-window-card", PowerplanWindowCard);

const registry = window as unknown as Registry<{ type: string } & Record<string, unknown>>;
registry.customCards ??= [];
registry.customStrategies ??= [];
if (!registry.customStrategies.some((entry) => entry.type === "powerplan")) {
  registry.customCards.push(
    {
      type: "powerplan-timeline-card",
      name: "PowerPlan timeline",
      description: "Prices, plans and the capacity limit for the next 24–48 hours.",
      documentationURL: DOCS,
    },
    {
      type: "powerplan-window-card",
      name: "PowerPlan capacity gauge",
      description: "The current capacity window against its limit.",
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
