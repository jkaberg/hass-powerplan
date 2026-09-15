// Strategy shim (iteration 5): the newest PowerPlan bundle on the page decides the layout.
//
// A custom element can be defined once per page. When two PowerPlan bundles load (an old extra
// module from a cached index.html + the new Lovelace resource), whichever arrives first owns
// `ll-strategy-dashboard-powerplan` - and with it `generate()`. If that was the old one, it asks
// the backend for a WS command that no longer exists ("Unknown command").
//
// Every bundle calls installStrategies() instead of customElements.define(). The element's static
// generate() only forwards to globalThis.__ppStrategies[tag], which each bundle overwrites, so the
// last bundle to load wins regardless of which one defined the element. A bundle that finds the
// element defined by a DIFFERENT bundle also patches the old class's static generate() (possible
// because Home Assistant calls it on the class it gets from customElements.get) and shows the
// reload toast.
//
//   // powerplan.js (loader), replacing customElements.define(...) for each strategy tag:
//   installStrategies({ "ll-strategy-dashboard-powerplan": DashboardStrategy, ... }, () => hass);

import { BUNDLE, notify } from "./version-check";
import type { Hass } from "./r3-util";

type StrategyImpl = { generate: (config: any, hass: any) => Promise<any> | any };

export function installStrategies(impls: Record<string, StrategyImpl>, getHass: () => Hass | undefined): void {
  const g = globalThis as any;
  const table: Record<string, StrategyImpl> = (g.__ppStrategies ??= {});
  const other = g.__ppBundle && g.__ppBundle !== BUNDLE;
  for (const [tag, impl] of Object.entries(impls)) {
    table[tag] = impl;                                        // newest wins
    const forward = (config: any, hass: any) => (g.__ppStrategies[tag] as StrategyImpl).generate(config, hass);
    const existing = customElements.get(tag) as any;
    if (!existing) {
      customElements.define(tag, class extends HTMLElement { static generate = forward; });
    } else if (existing.generate !== forward) {
      try { Object.defineProperty(existing, "generate", { value: forward, configurable: true, writable: true }); } catch { /* frozen: reload fixes it */ }
    }
  }
  g.__ppBundle = BUNDLE;
  if (other) {
    const h = getHass() ?? (document.querySelector("home-assistant") as any)?.hass;
    if (h) notify(h);
  }
}
