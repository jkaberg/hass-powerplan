// D12 §9 7: the timeline's slot-to-series transform and the gauge's stage colours.

import { describe, expect, it } from "vitest";

import {
  arcPoint,
  estimatedRanges,
  gauge,
  legendItems,
  niceMax,
  type PlanSlot,
  priceRuns,
  type PriceSlot,
  adviceItem,
  countingDays,
  currencyWord,
  dayKey,
  gaugeFont,
  moneyFormat,
  runRows,
  targetStep,
  nthHighest,
  midnights,
  monthGauge,
  topEntries,
  runsForLoad,
  slotReadout,
  stackOffsets,
  stageTone,
  stageWord,
  steps,
  type TimelineSlot,
  timelineSlots,
  toKw,
  TONE_COLOR,
  windowHours,
  withAlpha,
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
    expect(gauge(2.5, 4, 5)).toEqual({ max: 5, used: 0.5, projected: 0.8, over: false });
    const over = gauge(4, 6, 5);
    expect(over.projected).toBe(1);
    expect(over.over).toBe(true);
  });

  it("scales to 1,2 × the projection where no ceiling is billed", () => {
    const scale = gauge(2, 3, null);
    expect(scale.max).toBeCloseTo(3.6);
    expect(scale.over).toBe(false);
  });

  it("says the stage in words and the allowance in kW (B8)", () => {
    expect([0, 1, 2, 3, 4].map((stage) => stageWord(stageTone(stage)))).toEqual([
      "normal", "tight", "tight", "critical", "critical",
    ]);
    expect(toKw(11000, "W")).toBe(11);
    expect(toKw(13.7, "kW")).toBe(13.7);
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

// The live house one evening (D12 §9 13): Oslo, CEST (UTC+2).
const at = (iso: string) => Date.parse(iso);
function slot(startIso: string, minutes: number, extra: Partial<TimelineSlot> = {}): TimelineSlot {
  const start = at(startIso);
  return {
    start,
    end: start + minutes * 60_000,
    hours: minutes / 60,
    price: 0.8604,
    estimated: false,
    parties: [],
    ceilingKw: 10,
    baselineKw: null,
    productionKw: null,
    loadKw: {},
    loadKwh: {},
    holdKw: {},
    ...extra,
  };
}

describe("the redesigned timeline (D12 §5.2)", () => {
  const loads = ["tank", "hall", "bath_1", "bath_2"];

  it("stacks the loads on the rest of the house: 4,93 + 2,96 + 1,20 + 0,56 + 0,18 = 9,83 kW", () => {
    const s = slot("2026-09-23T20:00:00Z", 15, {
      baselineKw: 4.93,
      loadKw: { tank: 2.96, hall: 1.2, bath_1: 0.56, bath_2: 0.18 },
    });
    const stack = stackOffsets(s, loads);
    expect(stack.offset).toBe(4.93);
    expect(stack.total).toBeCloseTo(9.83);
    expect(stack.top).toBe(3);
    expect(stackOffsets(slot("2026-09-23T20:00:00Z", 15), loads).top).toBe(-1);
  });

  it("puts a 10 kW limit inside the plot: niceMax(10 × 1,2, 9,82 × 1,1) = 12", () => {
    expect(niceMax([10 * 1.2, 9.82 * 1.1])).toBe(12);
    expect(niceMax([2.96 * 1.3])).toBe(4);
    expect(niceMax([])).toBe(1);
  });

  it("opens at 12 h on a phone and 24 h on a desktop, unless the household chose", () => {
    const cfg = { hours: 24, narrow_hours: 12, narrow_width: 500 };
    expect(windowHours(364, cfg)).toBe(12);
    expect(windowHours(1306, cfg)).toBe(24);
    expect(windowHours(364, cfg, 48)).toBe(48);
  });

  it("joins equal prices into runs and splits where the price turns estimated", () => {
    // Hourly from 21:00 local: day price, night price 22–06, day price, then estimated.
    const prices = [0.8604, 0.7294, 0.7294, 0.8604, 0.8604, 0.8604];
    const slots = prices.map((price, i) =>
      slot(new Date(at("2026-09-23T19:00:00Z") + i * 3_600_000).toISOString(), 60, { price, estimated: i === 5 }),
    );
    const runs = priceRuns(slots);
    expect(runs.map((run) => [run.price, run.estimated])).toEqual([
      [0.8604, false],
      [0.7294, false],
      [0.8604, false],
      [0.8604, true],
    ]);
    expect(runs[1]!.alpha).toBeCloseTo(0.28);
    expect(runs[0]!.alpha).toBeCloseTo(0.62);
  });

  it("lists only the loads with energy in the window: four of ten from 20:15", () => {
    const names = Array.from({ length: 10 }, (_, i) => ({ id: `l${i}`, name: `L${i}` }));
    const planned = { l2: 0.74, l3: 0.11, l4: 0.05, l5: 0.26 };
    const slots = [slot("2026-09-23T20:00:00Z", 15, { loadKwh: planned })];
    const legend = legendItems(names, slots);
    expect(legend.map((item) => item.id)).toEqual(["l2", "l3", "l4", "l5"]);
    expect(legend[0]!.kwh).toBeCloseTo(0.74);
  });

  it("reads one slot: the sum against the limit, the loads, the price and the cost", () => {
    const s = slot("2026-09-23T20:00:00Z", 15, {
      price: 0.7294,
      baselineKw: 4.93,
      loadKw: { tank: 2.96, hall: 1.2, bath_1: 0.56, bath_2: 0.18 },
      loadKwh: { tank: 0.74, hall: 0.3, bath_1: 0.14, bath_2: 0.045 },
    });
    const readout = slotReadout(s, loads);
    expect(readout.sumKw).toBeCloseTo(9.83);
    expect(readout.limitKw).toBe(10);
    expect(readout.count).toBe(4);
    expect(readout.cost).toBeCloseTo(1.225 * 0.7294);
  });

  it("finds a load's runs: 22:00–22:30 and 00:00–00:30 on two nights", () => {
    const hall = { hall: 0.25 };
    const slots = [
      slot("2026-09-23T20:00:00Z", 15, { loadKwh: hall }),
      slot("2026-09-23T20:15:00Z", 15, { loadKwh: hall }),
      slot("2026-09-23T20:30:00Z", 15),
      slot("2026-09-23T22:00:00Z", 15, { loadKwh: hall }),
      slot("2026-09-23T22:15:00Z", 15, { loadKwh: hall }),
      slot("2026-09-24T22:00:00Z", 15, { loadKwh: hall }),
      slot("2026-09-24T22:15:00Z", 15, { loadKwh: hall }),
    ];
    expect(runsForLoad(slots, "hall")).toEqual([
      [at("2026-09-23T20:00:00Z"), at("2026-09-23T20:30:00Z")],
      [at("2026-09-23T22:00:00Z"), at("2026-09-23T22:30:00Z")],
      [at("2026-09-24T22:00:00Z"), at("2026-09-24T22:30:00Z")],
    ]);
  });
});

describe("the timeline's clock and colours", () => {
  it("finds local midnight, DST days included", () => {
    // 2026-10-25, Oslo: midnight is 22:00Z the day before; the next is 23:00Z (CET).
    const found = midnights(at("2026-10-24T12:00:00Z"), at("2026-10-26T12:00:00Z"), "Europe/Oslo");
    expect(found).toEqual([at("2026-10-24T22:00:00Z"), at("2026-10-25T23:00:00Z")]);
  });

  it("mixes a theme colour with an alpha, and leaves a variable alone", () => {
    expect(withAlpha("#03a9f4", 0.28)).toBe("rgba(3, 169, 244, 0.28)");
    expect(withAlpha("#fff", 0.5)).toBe("rgba(255, 255, 255, 0.5)");
    expect(withAlpha("rgb(3, 169, 244)", 0.62)).toBe("rgba(3, 169, 244, 0.62)");
    expect(withAlpha("var(--x)", 0.3)).toBe("var(--x)");
  });
});

describe("the month gauge (D12 §5.3)", () => {
  // The live house's Tensio ladder, 0–2–5–10–15–20 kW and above.
  const steps = [0, 2, 5, 10, 15, 20].map((from, i, all) => ({
    name: `${from}–${all[i + 1] ?? "∞"} kW`,
    from_kw: from,
    to_kw: all[i + 1] ?? null,
  }));

  it("puts 8,97 kW in the third step with the needle at 44,9 % of a 0–20 kW arc", () => {
    const g = monthGauge(8.97, steps, 2);
    expect(g.index).toBe(2);
    expect(g.max).toBe(20);
    expect(g.needle).toBeCloseTo(0.4485, 3);
    expect(g.ticks).toEqual([2, 5, 10, 15]);
    expect(g.segments.map((s) => s.tone)).toEqual(["ok", "ok", "ok", "warn", "alert"]);
    expect(g.segments.map((s) => s.current)).toEqual([false, false, true, false, false]);
    expect(g.barMax).toBe(15);
    expect(g.upper).toBe(10);
  });

  it("counts a threshold in the step above", () => {
    expect(monthGauge(10, steps, 2).index).toBe(3);
  });

  it("M1: target step_2 on the live select (target_kw null) draws 5–10 kW green at full opacity", () => {
    // The live select: `target_kw: null`, which `Number()` made 0 and every step red.
    const target = targetStep("step_2", { target_kw: null, lower_kw: 5, upper_kw: 10 }, steps);
    expect(target).toBe(2);
    const g = monthGauge(8.97, steps, target);
    expect(g.segments[2]).toMatchObject({ tone: "ok", current: true });
    expect(g.segments.map((s) => s.tone)).toEqual(["ok", "ok", "ok", "warn", "alert"]);
    expect(targetStep("auto", { target_kw: null }, steps)).toBeNull();
    expect(targetStep("auto", { target_kw: 7.5 }, steps)).toBe(2);
    expect(targetStep("auto", { lower_kw: 10 }, steps)).toBe(3);
    // No target: the current step is the goal.
    expect(monthGauge(3, steps, null).segments.map((s) => s.tone)).toEqual(["ok", "ok", "warn", "alert"]);
  });

  it("reads the live advice: the top 3, the headroom and the day that tips", () => {
    const items = [
      { key: "top_entries", severity: "info", entries: [["2026-09-21", 8.94], ["2026-09-17", 9.12], ["2026-09-13", 8.86]], n: 3 },
      { key: "step_headroom", severity: "info", to_next_kw: 1.03, next_name: "10–15 kW", fee_delta: "197", currency: "NOK" },
      { key: "days_that_matter", severity: "info", days: 1, kw: 11.95, n: 3 },
    ];
    expect(topEntries(items)).toEqual([["2026-09-17", 9.12], ["2026-09-21", 8.94], ["2026-09-13", 8.86]]);
    expect(adviceItem(items, "step_headroom")?.to_next_kw).toBe(1.03);
    expect(adviceItem(items, "days_that_matter")?.kw).toBe(11.95);
    expect(topEntries(undefined)).toEqual([]);
  });
});

describe("the days that count (D12 §5.7)", () => {
  const september: Array<[string, number]> = [
    ["2026-09-13", 8.86], ["2026-09-17", 9.12], ["2026-09-21", 8.94], ["2026-09-22", 8.44], ["2026-09-01", 5.1],
  ];

  it("keeps each month's top three, and 22 Sep's 8,44 is not among them", () => {
    const counting = countingDays([...september, ["2026-10-01", 3]]);
    expect([...counting].sort()).toEqual(["2026-09-13", "2026-09-17", "2026-09-21", "2026-10-01"]);
    expect(nthHighest(september)).toEqual(["2026-09-13", 8.86]);
  });

  it("names a day in the house's zone", () => {
    expect(dayKey(Date.parse("2026-09-22T22:30:00Z"), "Europe/Oslo")).toBe("2026-09-23");
  });
});

describe("iteration-2 polish (D12 §5.11)", () => {
  it("formats money in the viewer's language and the site's currency (G7)", () => {
    const plain = (text: string) => text.replace(/\s/g, " ");
    expect(plain(moneyFormat("nb", "NOK").format(2.78))).toBe("2,78 kr");
    expect(plain(moneyFormat("en", "NOK").format(2.78))).toBe("NOK 2.78");
    expect(currencyWord("nb", "NOK")).toBe("kr");
    expect(currencyWord("en", "NOK")).toBe("NOK");
  });

  it("lists the next runs by start, 'now' first, with the sum (N8)", () => {
    const loads = [
      { id: "tank", name: "Varmtvannsbereder", color: "#4269d0" },
      { id: "hall", name: "Gulvvarme inngang", color: "#f4bd4a" },
      { id: "bath", name: "Gulvvarme bad", color: "#ff725c" },
      { id: "sauna", name: "Badstue", color: "#6cc5b0" },
    ];
    const now = Date.parse("2026-09-23T20:12:00Z");
    const { rows, kwh, cost } = runRows(
      {
        tank: { planned_kwh: 3.81, cost: "2.78 NOK", next_start: "2026-09-23T21:00:00+00:00" },
        hall: { planned_kwh: 1.06, cost: "0.77 NOK", next_start: "2026-09-23T20:12:00+00:00" },
        bath: { planned_kwh: 0.84, cost: "0.61", next_start: "2026-09-23T20:30:00+00:00" },
        sauna: { planned_kwh: 0.0, cost: "0.00 NOK", next_start: null },
      },
      loads,
      now,
    );
    expect(rows.map((row) => [row.id, row.start])).toEqual([
      ["hall", null],
      ["bath", Date.parse("2026-09-23T20:30:00Z")],
      ["tank", Date.parse("2026-09-23T21:00:00Z")],
    ]);
    expect(kwh).toBeCloseTo(5.71, 6);
    expect(cost).toBeCloseTo(4.16, 6);
  });

  it("keeps the hour gauge's value inside its arc (H1)", () => {
    expect(gaugeFont("0,21 kWh".length, 146, 24)).toBe(36);
    expect(gaugeFont("0,28 kWh".length, 127, 24, 30)).toBe(30);
    expect(gaugeFont("10,21 kWh".length, 60, 24)).toBeLessThan(30);
  });
});
