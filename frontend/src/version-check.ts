// Stale-bundle guard (iteration 4).
//
// Seen live: a tab opened at 10:49 kept bundle 29f8ef4f after the server moved to 89716faf; the new
// layout referenced cards the old bundle didn't have, so Home Assistant drew "Konfigurasjonsfeil".
// Two parts, both called from the strategy loader (powerplan.js):
//
//   1. guardUnknownCards(config, KNOWN_TAGS) - right after the strategy config arrives: every
//      custom:powerplan-* type this bundle doesn't know gets a small placeholder card instead of
//      an error card, and the reload toast is shown.
//   2. startVersionCheck(hass) - compares this bundle's hash with the `v` of PowerPlan's Lovelace
//      resource (HA's own `lovelace/resources`, D12 §5.16 R5) on start, on every reconnect and every
//      30 min; shows Home Assistant's own toast on mismatch. No such resource (YAML): no check.
//
// BUNDLE is injected at build time: esbuild --define:__PP_BUNDLE__='"<hash used in ?v=>"'.

import { Hass, fire, pick } from "./r3-util";

declare const __PP_BUNDLE__: string;
export const BUNDLE: string = typeof __PP_BUNDLE__ === "string" ? __PP_BUNDLE__ : "dev";

const V_LABELS: Record<string, Record<string, string>> = {
  nb: { updated: "PowerPlan er oppdatert. Last inn siden for å bruke den nye versjonen.", reload: "Last inn", placeholder: "Ny versjon av PowerPlan" },
  en: { updated: "PowerPlan was updated. Reload the page to use the new version.", reload: "Reload", placeholder: "New PowerPlan version" },
};

let notified = false;

function notify(hass: Hass): void {
  if (notified) return;
  notified = true;
  const L = pick(V_LABELS, hass);
  // Home Assistant's notification manager listens for this event on the <home-assistant> element.
  // duration -1 keeps the toast until the user acts or dismisses it.
  fire(document.querySelector("home-assistant") ?? document.body, "hass-notification", {
    message: L.updated, duration: -1, dismissable: true, action: { text: L.reload, action: () => location.reload() },
  });
}

export function startVersionCheck(hass: Hass, everyMs = 30 * 60e3): () => void {
  const w = window as any;
  if (w.__ppVersionCheck) return w.__ppVersionCheck;
  const check = async () => {
    try {
      const served = servedKey(await hass.callWS<{ url: string }[]>({ type: "lovelace/resources" }));
      if (served && BUNDLE !== "dev" && served !== BUNDLE) notify(hass);
    } catch { /* no Lovelace resources: nothing to compare */ }
  };
  check();
  const t = window.setInterval(check, everyMs);
  const conn = hass.connection;
  conn?.addEventListener?.("ready", check);
  const stop = () => { clearInterval(t); conn?.removeEventListener?.("ready", check); w.__ppVersionCheck = undefined; };
  w.__ppVersionCheck = stop;
  return stop;
}

/** The `v` of PowerPlan's module among HA's Lovelace resources, or null without one (D12 §5.16 R5). */
export function servedKey(resources: readonly { url: string }[] | null | undefined): string | null {
  const ours = resources?.find((r) => r.url.split("?")[0] === "/powerplan_frontend/powerplan.js");
  return ours ? new URL(ours.url, "http://x").searchParams.get("v") : null;
}

/** Walks a dashboard config and swaps unknown custom:powerplan-* cards for a placeholder. */
export function guardUnknownCards(config: any, known: Set<string>, hass: Hass): any {
  let missing = false;
  const walk = (node: any): any => {
    if (Array.isArray(node)) return node.map(walk);
    if (!node || typeof node !== "object") return node;
    const out: any = {};
    for (const [k, v] of Object.entries(node)) out[k] = walk(v);
    if (typeof out.type === "string" && out.type.startsWith("custom:powerplan-") && !known.has(out.type.slice(7))) {
      missing = true;
      return { type: "custom:powerplan-placeholder-card", grid_options: out.grid_options };
    }
    return out;
  };
  const res = walk(config);
  if (missing) {
    if (!customElements.get("powerplan-placeholder-card")) customElements.define("powerplan-placeholder-card", PlaceholderCard);
    notify(hass);
  }
  return res;
}

class PlaceholderCard extends HTMLElement {
  setConfig(): void { /* nothing to configure */ }
  set hass(h: Hass) {
    if (this.shadowRoot) return;
    const L = pick(V_LABELS, h);
    this.attachShadow({ mode: "open" }).innerHTML = `<style>
      ha-card { display: flex; align-items: center; gap: 12px; padding: 12px 8px 12px 16px; min-height: 56px; box-sizing: border-box; }
      span { flex: 1 1 auto; font: var(--ha-font-size-m, 14px) var(--ha-font-family-body, Roboto, sans-serif); color: var(--secondary-text-color); }
      button { height: 40px; padding: 0 12px; border: 0; border-radius: 20px; background: transparent; cursor: pointer;
               color: var(--primary-color); font: 500 var(--ha-font-size-m, 14px) var(--ha-font-family-body, Roboto, sans-serif); }
    </style><ha-card><span>${L.placeholder}</span><button type="button">${L.reload}</button></ha-card>`;
    this.shadowRoot!.querySelector("button")!.addEventListener("click", () => location.reload());
  }
  getCardSize(): number { return 1; }
}
