// D12 §9 29: iteration 5 (§5.17) - the newest bundle answers the strategy, and savings
// without a reference are hidden.

import { describe, expect, it } from "vitest";

import { type HassEntity, savingsView } from "../src/r3-util";

const TAG = "ll-strategy-dashboard-powerplan";

describe("installStrategies (§5.17 S1)", () => {
  it("forwards generate() to the newest bundle, also through an element an older bundle defined", async () => {
    const g = globalThis as Record<string, unknown>;
    g.HTMLElement ??= class {};
    const registry = new Map<string, unknown>();
    g.customElements = { get: (n: string) => registry.get(n), define: (n: string, c: unknown) => registry.set(n, c) };
    const toasts: string[] = [];
    g.document = {
      querySelector: () => null,
      body: { dispatchEvent: (e: { type: string }) => toasts.push(e.type) },
    };
    // An older bundle, without the shim, defined the element and left its key.
    class Old {
      static generate() {
        return "old";
      }
    }
    registry.set(TAG, Old);
    g.__ppBundle = "older-key";
    const { installStrategies } = await import("../src/strategy-shim");
    const hass = { language: "nb", states: {} } as never;

    installStrategies({ [TAG]: { generate: () => "new" } }, () => hass);
    expect((registry.get(TAG) as typeof Old).generate()).toBe("new");
    expect(toasts).toEqual(["hass-notification"]);

    installStrategies({ [TAG]: { generate: () => "newer" } }, () => hass);
    expect((registry.get(TAG) as typeof Old).generate()).toBe("newer");
    expect(toasts).toHaveLength(1);
  });
});

describe("savingsView (§5.17)", () => {
  const entity = (state: string, attributes: Record<string, unknown> = {}): HassEntity => ({
    entity_id: "sensor.x",
    state,
    attributes,
    last_changed: "2026-09-24T20:00:00Z",
  });

  it("hides a saving the backend marks as having no reference", () => {
    expect(savingsView(entity("0E-8", { savings_confidence: "none" }), entity("0.59"))).toEqual({ value: null, missing: true });
    expect(savingsView(entity("0.12", { savings_confidence: "estimated" }), entity("0.59"))).toEqual({ value: 0.12, missing: false });
  });
});
