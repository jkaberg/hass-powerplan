// D12 §9 7: the timeline's slot-to-series transform and the gauge's stage colours.

import { describe, expect, it } from "vitest";

import {
  arcPoint,
  estimatedRanges,
  gauge,
  type PlanSlot,
  type PriceSlot,
  stageTone,
  steps,
  timelineSlots,
  TONE_COLOR,
} from "../src/transforms";

const QUARTER = 15 * 60_000;

/** Quarter slots over Oslo's local day `[startUtc, endUtc)`, as the plan sensor publishes them. */
function quarters(startUtc: string, endUtc: string): { prices: PriceSlot[]; plan: PlanSlot[] } {
  const prices: PriceSlot[] = [];
  const plan: PlanSlot[] = [];
  for (let t = Date.parse(startUtc); t < Date.parse(endUtc); t += QUARTER) {
    const start = new Date(t).toISOString();
    const end = new Date(t + QUARTER).toISOString();
    prices.push({ start, end, total: "1.25", confidence: "known" });
    plan.push({ start, end, ceiling_kwh: 5, baseline_kwh: 0.25, planned_kwh: { ev: 2.75 } });
  }
  return { prices, plan };
}

function contiguous(slots: Array<{ start: number; end: number }>): boolean {
  return slots.every((slot, i) => i === 0 || slots[i - 1]!.end === slot.start);
}

describe("timelineSlots", () => {
  it("draws the autumn DST day's 100 quarter slots without a gap", () => {
    // 2026-10-25 in Europe/Oslo: 00:00 CEST (22:00Z the day before) to 00:00 CET (23:00Z).
    const { prices, plan } = quarters("2026-10-24T22:00:00Z", "2026-10-25T23:00:00Z");
    const slots = timelineSlots(prices, plan, 60, ["ev"], Date.parse("2026-10-24T22:00:00Z"), 25);
    expect(slots).toHaveLength(100);
    expect(contiguous(slots)).toBe(true);
    expect(slots[slots.length - 1]!.end).toBe(Date.parse("2026-10-25T23:00:00Z"));
  });

  it("draws the spring DST day's 92 quarter slots without a gap", () => {
    // 2026-03-29 in Europe/Oslo: 00:00 CET (23:00Z) to 00:00 CEST (22:00Z).
    const { prices, plan } = quarters("2026-03-28T23:00:00Z", "2026-03-29T22:00:00Z");
    const slots = timelineSlots(prices, plan, 60, ["ev"], Date.parse("2026-03-28T23:00:00Z"), 23);
    expect(slots).toHaveLength(92);
    expect(contiguous(slots)).toBe(true);
    const points = steps(slots, (slot) => slot.price);
    expect(points.every(([, value]) => value !== null)).toBe(true);
  });

  it("turns energy into power: a slot's kWh over the slot, a window's ceiling over the window", () => {
    const { prices, plan } = quarters("2026-01-15T10:00:00Z", "2026-01-15T11:00:00Z");
    const [slot] = timelineSlots(prices, plan, 60, ["ev"], Date.parse("2026-01-15T10:00:00Z"), 1);
    expect(slot!.loadKw.ev).toBeCloseTo(11);
    expect(slot!.loadKwh.ev).toBeCloseTo(2.75);
    expect(slot!.baselineKw).toBeCloseTo(1);
    expect(slot!.ceilingKw).toBeCloseTo(5);
    expect(slot!.price).toBe(1.25);
  });

  it("flags estimated and synthesised prices, and joins them into ranges", () => {
    const { prices, plan } = quarters("2026-01-15T10:00:00Z", "2026-01-15T12:00:00Z");
    prices.forEach((price, i) => {
      if (i >= 4) price.confidence = i % 2 ? "estimated" : "synthesised";
    });
    const slots = timelineSlots(prices, plan, 60, ["ev"], Date.parse("2026-01-15T10:00:00Z"), 2);
    expect(slots.map((slot) => slot.estimated)).toEqual([false, false, false, false, true, true, true, true]);
    expect(estimatedRanges(slots)).toEqual([
      [Date.parse("2026-01-15T11:00:00Z"), Date.parse("2026-01-15T12:00:00Z")],
    ]);
  });

  it("starts at the slot in progress and stops at the horizon", () => {
    const { prices, plan } = quarters("2026-01-15T10:00:00Z", "2026-01-16T10:00:00Z");
    const now = Date.parse("2026-01-15T10:20:00Z");
    const slots = timelineSlots(prices, plan, 60, ["ev"], now, 1);
    expect(slots[0]!.start).toBe(Date.parse("2026-01-15T10:15:00Z"));
    expect(slots[slots.length - 1]!.start).toBeLessThan(now + 3_600_000);
  });

  it("uses the price grid when nothing is planned, and a missing ceiling is a gap", () => {
    const { prices, plan } = quarters("2026-01-15T10:00:00Z", "2026-01-15T11:00:00Z");
    const bare = timelineSlots(prices, [], 60, ["ev"], Date.parse("2026-01-15T10:00:00Z"), 1);
    expect(bare).toHaveLength(4);
    expect(bare[0]!.loadKw.ev).toBe(0);
    expect(bare[0]!.ceilingKw).toBeNull();
    plan[1]!.ceiling_kwh = null;
    const gapped = timelineSlots(prices, plan, 60, ["ev"], Date.parse("2026-01-15T10:00:00Z"), 1);
    expect(steps(gapped, (slot) => slot.ceilingKw)[1]![1]).toBeNull();
  });
});

describe("the window gauge", () => {
  it("colours by the ladder stage: 0 green, 1–2 amber, 3–4 red", () => {
    expect([0, 1, 2, 3, 4].map(stageTone)).toEqual(["ok", "warn", "warn", "alert", "alert"]);
    expect(stageTone(null)).toBe("ok");
    expect(TONE_COLOR.ok).toContain("--success-color");
    expect(TONE_COLOR.warn).toContain("--warning-color");
    expect(TONE_COLOR.alert).toContain("--error-color");
  });

  it("scales against the ceiling and clamps to the arc", () => {
    expect(gauge(2.5, 4, 5)).toEqual({ max: 5, used: 0.5, needle: 0.8, over: false });
    const over = gauge(4, 6, 5);
    expect(over.needle).toBe(1);
    expect(over.over).toBe(true);
  });

  it("scales to the projection where no ceiling is billed", () => {
    const scale = gauge(2, 3, null);
    expect(scale.max).toBe(3);
    expect(scale.over).toBe(false);
  });

  it("draws the semicircle from left to right over the top", () => {
    const [x0, y0] = arcPoint(0, 40);
    const [xm, ym] = arcPoint(0.5, 40);
    const [x1] = arcPoint(1, 40);
    expect(x0).toBeCloseTo(-40);
    expect(y0).toBeCloseTo(0);
    expect(xm).toBeCloseTo(0);
    expect(ym).toBeCloseTo(-40);
    expect(x1).toBeCloseTo(40);
  });
});
