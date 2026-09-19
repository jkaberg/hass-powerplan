// D12 §9 24: iteration 4 (§5.15), its pure halves - the status line and its 90 s hold,
// the plan's runs and lowered hours, the whole-house buckets, honest savings, the stale-bundle guard.

import { describe, expect, it } from "vitest";

import { bucketize, capOf, cheapShare, totalOf } from "../src/forecast";
import { type HassEntity, loweredFor, readPlan, runsFor, savingsView, windows } from "../src/r3-util";
import { DEBOUNCE_MS, rawStatus, STATUS_LABELS, StatusDebouncer } from "../src/status";

const L = STATUS_LABELS.nb!;
const T0 = Date.parse("2026-09-24T20:00:00Z");
const H = 3_600_000;

const entity = (state: string, attributes: Record<string, unknown> = {}): HassEntity => ({
  entity_id: "sensor.x",
  state,
  attributes,
  last_changed: "2026-09-24T20:00:00Z",
});

const none = { runNow: false, futureRun: false, holdNow: false };

describe("rawStatus (F2)", () => {
  it("reads the state as a word, the plan's own run first", () => {
    expect(rawStatus(entity("running_plan"), undefined, none, L)).toEqual({ kind: "running", word: "Går" });
    expect(rawStatus(entity("waiting"), undefined, { ...none, runNow: true }, L).kind).toBe("running");
    expect(rawStatus(entity("idle"), undefined, { ...none, futureRun: true }, L).word).toBe("Planlagt");
    expect(rawStatus(entity("idle"), undefined, none, L)).toEqual({ kind: "idle", word: "" });
  });

  it("holding is its own kind, and the backend's held status wins", () => {
    expect(rawStatus(entity("idle"), undefined, { ...none, holdNow: true }, L).kind).toBe("holding");
    expect(rawStatus(entity("running_plan", { display_status: "waiting" }), undefined, none, L).kind).toBe("waiting");
  });

  it("already_at is the plan, not a hand on the dial; the control select speaks first", () => {
    expect(rawStatus(entity("manual_override", { reason_key: "already_at" }), undefined, none, L).kind).toBe("idle");
    expect(rawStatus(entity("manual_override", { reason_key: "setpoint" }), undefined, none, L).kind).toBe("manual");
    expect(rawStatus(entity("running_plan"), entity("off"), none, L)).toMatchObject({ kind: "manual", word: "Av" });
    expect(rawStatus(entity("unavailable"), undefined, none, L).kind).toBe("unavailable");
  });
});

describe("StatusDebouncer (R5)", () => {
  it("shows a flap only after it has held 90 s, and manual at once", () => {
    const d = new StatusDebouncer();
    const running = { kind: "running" as const, word: "Går" };
    const paused = { kind: "paused" as const, word: "Strupet" };
    expect(d.view("tv", running, 0)).toEqual(running);
    expect(d.view("tv", paused, 10_000)).toEqual(running);
    expect(d.nextFlip()).toBe(10_000 + DEBOUNCE_MS);
    expect(d.view("tv", paused, 10_000 + DEBOUNCE_MS)).toEqual(paused);
    expect(d.view("tv", { kind: "manual", word: "Manuell" }, 10_001 + DEBOUNCE_MS).kind).toBe("manual");
  });
});

const slot = (h: number, planned: Record<string, number>, extra: Record<string, unknown> = {}) => ({
  start: new Date(T0 + h * H).toISOString(),
  end: new Date(T0 + (h + 1) * H).toISOString(),
  planned_kwh: planned,
  baseline_kwh: 1,
  baseline_p90_kwh: 1.5,
  ceiling_kwh: 5,
  ...extra,
});

const plan = entity("12.3", {
  window_min: 60,
  slots: [
    slot(0, { vvb: 0 }, { hold_kwh: { floor: 0.2 }, paused: ["floor"] }),
    slot(1, { vvb: 2 }, { hold_kwh: { floor: 0.2 } }),
    slot(2, { vvb: 1.5 }),
    slot(3, { vvb: 0 }),
  ],
});

describe("the plan's runs (F2, F4)", () => {
  it("merges consecutive planned slots and finds lowered hours only for a load that holds", () => {
    const { slots, windowMin } = readPlan(plan);
    expect(windowMin).toBe(60);
    const [run] = runsFor("vvb", slots);
    expect(run).toMatchObject({ start: new Date(T0 + H), end: new Date(T0 + 3 * H), kwh: 3.5 });
    expect(loweredFor("floor", slots)).toEqual([{ start: new Date(T0), end: new Date(T0 + H), kwh: 0 }]);
    expect(loweredFor("vvb", slots)).toEqual([]);
    expect(windows(slots, () => true)).toHaveLength(1);
  });
});

describe("the whole-house buckets (F3)", () => {
  it("sums slots per window, caps at P90 and counts moved energy in the cheap quarter", () => {
    const { slots } = readPlan(plan);
    const prices = [0, 1, 2, 3].map((h) => ({ s: T0 + h * H, e: T0 + (h + 1) * H, p: h === 1 ? 0.1 : 1, est: h === 3 }));
    const b = bucketize(slots, 60, T0, 4, prices);
    expect(b).toHaveLength(4);
    expect(b[1]).toMatchObject({ baseline: 1, p90: 1.5, hold: 0.2, moved: { vvb: 2 }, ceiling: 5, price: 0.1 });
    expect(totalOf(b[1]!)).toBeCloseTo(3.2);
    expect(capOf(b[1]!)).toBeCloseTo(3.7);
    expect(b[3]!.estimated).toBe(true);
    // 2 of the 3,5 moved kWh land in the one cheap hour.
    expect(cheapShare(b)).toBe(57);
  });
});

describe("savingsView (F10)", () => {
  it("is missing without a reference, and a real negative saving stays negative", () => {
    expect(savingsView(entity("unknown", { reason: "no_reference" }), entity("0.59"))).toEqual({ value: null, missing: true });
    expect(savingsView(entity("-0.59"), entity("0.59"))).toEqual({ value: null, missing: true });
    expect(savingsView(entity("-0.20"), entity("0.59"))).toEqual({ value: -0.2, missing: false });
  });
});

describe("guardUnknownCards (F7)", () => {
  it("swaps a card this bundle does not know for the placeholder and keeps the rest", async () => {
    const g = globalThis as Record<string, unknown>;
    g.HTMLElement ??= class {};
    const defined: string[] = [];
    g.customElements ??= { get: (n: string) => defined.includes(n), define: (n: string) => defined.push(n) };
    g.document ??= { querySelector: () => null, body: { dispatchEvent: () => true } };
    const { guardUnknownCards } = await import("../src/version-check");
    const hass = { language: "nb", states: {} } as never;
    const config = { views: [{ cards: [{ type: "custom:powerplan-new-card", grid_options: { columns: 12 } }, { type: "custom:powerplan-price-card" }] }] };
    const out = guardUnknownCards(config, new Set(["powerplan-price-card"]), hass);
    expect(out.views[0].cards).toEqual([
      { type: "custom:powerplan-placeholder-card", grid_options: { columns: 12 } },
      { type: "custom:powerplan-price-card" },
    ]);
    expect(defined).toContain("powerplan-placeholder-card");
  });
});

describe("the run ahead (D-0630)", () => {
  it("a waiting row can say when its run starts, in both languages", () => {
    expect(STATUS_LABELS.nb!.from).toBe("fra {time}");
    expect(STATUS_LABELS.en!.from).toBe("from {time}");
    expect(rawStatus(entity("waiting"), undefined, { ...none, futureRun: true }, L).kind).toBe("waiting");
  });
});
