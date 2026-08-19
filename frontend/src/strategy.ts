// `ll-strategy-dashboard-powerplan` (D12 §2, §3): one websocket call, the
// layout back. The layout is built in Python from the site's registry; this
// element only fetches it, and shows why when it cannot.

import type { HomeAssistant } from "./ha";

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
    const message: Record<string, unknown> = {
      type: "powerplan/dashboard/config",
      language: hass.language,
    };
    for (const key of ["entry_id", "hidden_views", "hidden_cards"] as const) {
      if (config[key] !== undefined) message[key] = config[key];
    }
    try {
      return await hass.callWS(message);
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
