// D12 §9 27 and §5.16 R5: the price card's spot from HA's states, the served bundle from HA's resources.

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { Hass, HassEntity } from "../src/r3-util";

const entity = (state: string, attributes: Record<string, unknown> = {}): HassEntity => ({
  entity_id: "sensor.x",
  state,
  attributes,
  last_changed: "2026-09-24T09:00:00Z",
});

const slot = (start: string, spot: number | undefined, confidence = "known") => ({
  start,
  end: new Date(Date.parse(start) + 3_600_000).toISOString(),
  total: "0.8779",
  confidence,
  ...(spot === undefined ? {} : { spot }),
});

const hass = (states: Record<string, HassEntity>): Hass =>
  ({ states, language: "nb", config: { time_zone: "Europe/Oslo", currency: "NOK" } }) as unknown as Hass;

const cfg = {
  type: "custom:powerplan-price-card",
  entities: { price: "sensor.price", price_forecast: "sensor.forecast", fixed_price_savings: "sensor.saving" },
};

let spotFromStates: typeof import("../src/price-card").spotFromStates;
let servedKey: typeof import("../src/version-check").servedKey;

beforeAll(async () => {
  const g = globalThis as Record<string, unknown>;
  g.HTMLElement ??= class {};
  ({ spotFromStates } = await import("../src/price-card"));
  ({ servedKey } = await import("../src/version-check"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("spotFromStates (§5.16 R2)", () => {
  it("builds iteration 4's Spot from price_forecast and fixed_price_savings", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-24T09:05:00Z")); // 11:05 in Oslo
    const h = hass({
      "sensor.forecast": entity("2026-09-25T22:00:00+00:00", {
        area: "NO3",
        vat: 0.25,
        fixed_price: 0.5,
        slots: [slot("2026-09-24T09:00:00Z", 0.834), slot("2026-09-24T22:00:00Z", 0.7)],
      }),
      "sensor.saving": entity("991.2", { today: 43.5, kwh: 1191.5, today_kwh: 43.3 }),
    });
    expect(spotFromStates(h, cfg)).toEqual({
      area: "NO3",
      currency: "NOK",
      vat: 0.25,
      fixed_price: 0.5,
      slots: [
        { start: "2026-09-24T09:00:00Z", end: "2026-09-24T10:00:00.000Z", spot: 0.834 },
        { start: "2026-09-24T22:00:00Z", end: "2026-09-24T23:00:00.000Z", spot: 0.7 },
      ],
      tomorrow_available: true,
      effect: { today_kwh: 43.3, today_nok: 43.5, month_kwh: 1191.5, month_nok: 991 },
    });
  });

  it("reads tomorrow only from a known slot after local midnight, and no effect without the sensor", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-24T09:05:00Z"));
    const h = hass({
      "sensor.forecast": entity("x", {
        slots: [slot("2026-09-24T21:00:00Z", 0.5), slot("2026-09-24T22:00:00Z", 0.6, "synthesised")],
      }),
    });
    const spot = spotFromStates(h, { ...cfg, entities: { price: "sensor.price", price_forecast: "sensor.forecast" } });
    expect(spot?.tomorrow_available).toBe(false);
    expect(spot?.fixed_price).toBeNull();
    expect(spot?.effect).toBeUndefined();
  });

  it("is undefined when no slot carries a spot", () => {
    const h = hass({ "sensor.forecast": entity("x", { slots: [slot("2026-09-24T09:00:00Z", undefined)] }) });
    expect(spotFromStates(h, cfg)).toBeUndefined();
  });
});

describe("servedKey (§5.16 R5)", () => {
  it("reads the v of PowerPlan's resource and nothing else", () => {
    const resources = [
      { url: "/hacsfiles/button-card/button-card.js?hacstag=1" },
      { url: "/powerplan_frontend/powerplan.js?v=abc123def456" },
    ];
    expect(servedKey(resources)).toBe("abc123def456");
    expect(servedKey([{ url: "/powerplan_frontend/powerplan.jsx?v=1" }])).toBeNull();
    expect(servedKey([])).toBeNull();
    expect(servedKey(undefined)).toBeNull();
  });
});

describe("the strategy (§5.16 R1)", () => {
  it("asks powerplan.get_dashboard through call_service and returns its response", async () => {
    const g = globalThis as Record<string, unknown>;
    g.location ??= { pathname: "/dashboard-powerplan/overview" };
    g.window ??= { setInterval: () => 0 };
    const { PowerplanDashboardStrategy } = await import("../src/strategy");
    const sent: Record<string, unknown>[] = [];
    const layout = { views: [{ path: "overview", back_path: "{dashboard}/history" }] };
    const hass = {
      language: "nb",
      callWS: async (msg: Record<string, unknown>) => {
        sent.push(msg);
        if (msg.type === "lovelace/resources") return [];
        return { context: {}, response: layout };
      },
    } as never;
    const out = (await PowerplanDashboardStrategy.generate({ entry_id: "01J", hidden_views: ["history"] }, hass)) as typeof layout;
    expect(sent[sent.length - 1]).toEqual({
      type: "call_service",
      domain: "powerplan",
      service: "get_dashboard",
      service_data: { language: "nb", site: "01J", hidden_views: ["history"] },
      return_response: true,
    });
    expect(sent.some((m) => String(m.type).startsWith("powerplan/"))).toBe(false);
    expect(out.views[0]!.back_path).toBe("/dashboard-powerplan/history");
  });

  it("shows why in a markdown card when the action fails", async () => {
    const { PowerplanDashboardStrategy } = await import("../src/strategy");
    const hass = {
      language: "en",
      callWS: async (msg: Record<string, unknown>) => {
        if (msg.type === "call_service") throw { code: "service_validation_error", message: "Unknown home: nope" };
        return [];
      },
    } as never;
    const out = (await PowerplanDashboardStrategy.generate({ entry_id: "nope" }, hass)) as {
      views: { sections: { cards: { content: string }[] }[] }[];
    };
    expect(out.views[0]!.sections[0]!.cards[0]!.content).toContain("Unknown home: nope");
  });
});
