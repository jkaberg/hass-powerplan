// The cards the layout names (D12 §5.2, §5.3), defined once this chunk loads.
// HA draws an error card for an undefined element and replaces it as soon as
// the element is defined, so a card never waits on the strategy or vice versa.

import { PowerplanAppliancesCard } from "./appliances-card";
import { PowerplanAttentionCard } from "./attention-card";
import { PowerplanMonthBars } from "./month-bars";
import { PowerplanPeriodSummary } from "./period-summary";
import { PowerplanPriceCard } from "./price-card";
import { PowerplanRunsCard } from "./runs-card";
import { PowerplanTimelineCard } from "./timeline-card";
import { PowerplanWindowCard } from "./window-card";

const define = (name: string, element: CustomElementConstructor) => {
  if (!customElements.get(name)) customElements.define(name, element);
};

define("powerplan-timeline-card", PowerplanTimelineCard);
define("powerplan-window-card", PowerplanWindowCard);
define("powerplan-period-summary", PowerplanPeriodSummary);
define("powerplan-runs-card", PowerplanRunsCard);
define("powerplan-appliances-card", PowerplanAppliancesCard);
define("powerplan-price-card", PowerplanPriceCard);
// Iteration 4 (`register-r4.ts`, merged here).
define("powerplan-attention-card", PowerplanAttentionCard);
define("powerplan-month-bars", PowerplanMonthBars);
