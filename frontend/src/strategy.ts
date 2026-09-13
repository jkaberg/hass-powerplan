// `ll-strategy-dashboard-powerplan` (D12 §2, §3): one call, the layout back.
// The layout is built in Python from the site's registry and answered by the
// response action `powerplan.get_dashboard` (§5.16 R1), sent as HA's own
// `call_service` message; this element fetches it, points its `{dashboard}`
// paths at the dashboard it is on, and shows why when it cannot.

import type { HomeAssistant } from "./ha";
import type { Hass } from "./r3-util";
import { dashboardPath, resolvePaths } from "./transforms";
import { guardUnknownCards, startVersionCheck } from "./version-check";

/** Every card tag this bundle defines (`cards.ts`); a layout naming another gets a placeholder (iteration 4, F7). */
export const KNOWN_TAGS = new Set([
  "powerplan-timeline-card",
  "powerplan-window-card",
  "powerplan-period-summary",
  "powerplan-runs-card",
  "powerplan-appliances-card",
  "powerplan-price-card",
  "powerplan-attention-card",
  "powerplan-month-bars",
]);

const TROUBLESHOOTING = "https://github.com/jkaberg/hass-powerplan/blob/main/docs/troubleshooting.md";

// The one text the integration's translations cannot carry: the call that
// would fetch them has failed.
const FAILED: Record<string, [string, string]> = {
  en: ["The PowerPlan dashboard could not load.", "Troubleshooting"],
  nb: ["PowerPlan-dashbordet kunne ikke lastes.", "Feilsøking"],
};

interface StrategyConfig {
  entry_id?: string;
  hidden_views?: string[];
  hidden_cards?: string[];
}

export class PowerplanDashboardStrategy extends HTMLElement {
  static async generate(config: StrategyConfig, hass: HomeAssistant): Promise<unknown> {
    const data: Record<string, unknown> = { language: hass.language };
    if (config.entry_id !== undefined) data.site = config.entry_id;
    for (const key of ["hidden_views", "hidden_cards"] as const) {
      if (config[key] !== undefined) data[key] = config[key];
    }
    const message = { type: "call_service", domain: "powerplan", service: "get_dashboard", service_data: data, return_response: true };
    const ha = hass as unknown as Hass;
    startVersionCheck(ha);
    try {
      const reply = await hass.callWS<{ response: unknown }>(message);
      return guardUnknownCards(resolvePaths(reply.response, dashboardPath(location.pathname)), KNOWN_TAGS, ha);
    } catch (err) {
      const base = (hass.language || "en").split("-")[0]!;
      const [failed, help] = FAILED[["no", "nn"].includes(base) ? "nb" : base] ?? FAILED.en!;
      const detail = err as { message?: string; code?: string };
      const why = detail?.message || detail?.code || String(err);
      return {
        title: "PowerPlan",
        views: [
          {
            title: "PowerPlan",
            type: "sections",
            sections: [
              {
                type: "grid",
                cards: [
                  { type: "markdown", content: `**${failed}**\n\n${why}\n\n[${help}](${TROUBLESHOOTING})` },
                ],
              },
            ],
          },
        ],
      };
    }
  }
}
