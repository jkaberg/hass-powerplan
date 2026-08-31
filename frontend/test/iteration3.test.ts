// D12 §9 20: iteration 3's pure halves - the row status and its debounce, a load's
// runs, the cheap bands, the forecast's buckets and totals, the deadline clock.

import { describe, expect, it } from "vitest";

import { DEBOUNCE_MS, rawStatus, StatusDebouncer } from "../src/status";
import { bucketize, cheapBands, forecastTotals, holdRuns, nextClock, type PlanSlot, planRuns, type PriceSlot, readable } from "../src/transforms";

const LABELS = {
  status_running: "Går",
  status_charging: "Lader",
  status_forced: "Kjør nå",
  status_planned: "Planlagt",
  status_waiting: "Venter",
  status_paused: "Strupet",
  status_manual: "Endret for hånd",
  status_not_controlled: "Manuell",
  status_observing: "Prøvemodus",
  status_unavailable: "Utilgjengelig",
  status_holding: "Holder",
  reason_paused: "Pause for å holde effekttrinnet",
  reason_manual: "Endret på enheten",
  reason_not_controlled: "Styres fra enheten — ingen plan",
  reason_observing: "PowerPlan ser bare på",
};

const Q = 15 * 60_000;
const T0 = Date.parse("2026-09-23T21:00:00Z"); // 23:00 in Oslo

function slot(i: number, planned: Record<string, number>, extra: Partial<PlanSlot> = {}): PlanSlot {
  return {
    start: new Date(T0 + i * Q).toISOString(),
    end: new Date(T0 + (i + 1) * Q).toISOString(),
    ceiling_kwh: 10,
    baseline_kwh: 0.35,
    baseline_p90_kwh: 0.5,
    planned_kwh: planned,
    ...extra,
  };
}

function price(i: number, total: number, confidence = "known"): PriceSlot {
  return { start: new Date(T0 + i * Q).toISOString(), end: new Date(T0 + (i + 1) * Q).toISOString(), total: String(total), confidence };
}

describe("rawStatus (R1)", () => {
  it("reads the plan, not a person, when the device was already set", () => {
    expect(rawStatus("manual_override", { reason_key: "already_at" }, true, true, LABELS).label).toBe("Går");
    expect(rawStatus("manual_override", { reason_key: "interval" }, true, true, LABELS)).toEqual({
      kind: "manual",
      label: "Endret for hånd",
      reason: "Endret på enheten",
    });
  });

  it("maps each plan_status to a kind", () => {
    expect(rawStatus("paused_peak", {}, false, true, LABELS).kind).toBe("paused");
    expect(rawStatus("not_controlled", {}, false, false, LABELS).label).toBe("Manuell");
    expect(rawStatus("charging", {}, true, true, LABELS).label).toBe("Lader");
    expect(rawStatus("run_now", {}, false, false, LABELS).label).toBe("Kjør nå");
    expect(rawStatus("device_unavailable", {}, false, true, LABELS).kind).toBe("unavailable");
    // A load waiting for its planned run is "planned"; with none it is waiting or idle.
    expect(rawStatus("waiting", {}, false, true, LABELS).label).toBe("Planlagt");
    expect(rawStatus("waiting", {}, false, false, LABELS).label).toBe("Venter");
    expect(rawStatus("idle", {}, false, false, LABELS)).toEqual({ kind: "idle", label: "" });
    // The integration's held status wins over a flapping state.
    expect(rawStatus("paused_peak", { display_status: "running_plan" }, true, true, LABELS).label).toBe("Går");
  });
});

describe("StatusDebouncer (R5)", () => {
  it("holds a flap for 90 s and shows a hand on the control at once", () => {
    const d = new StatusDebouncer();
    const running = rawStatus("running_plan", {}, true, true, LABELS);
    const paused = rawStatus("paused_peak", {}, true, true, LABELS);
    expect(d.view("tv", running, 0).kind).toBe("running");
    expect(d.view("tv", paused, 1_000).kind).toBe("running");
    expect(d.nextFlip()).toBe(1_000 + DEBOUNCE_MS);
    expect(d.view("tv", running, 30_000).kind).toBe("running");
    expect(d.view("tv", paused, 40_000).kind).toBe("running");
    expect(d.view("tv", paused, 40_000 + DEBOUNCE_MS).kind).toBe("paused");
    expect(d.view("tv", rawStatus("not_controlled", {}, false, false, LABELS), 40_001 + DEBOUNCE_MS).kind).toBe("manual");
  });
});

describe("planRuns (R3)", () => {
  it("merges consecutive quarters into one run with their energy", () => {
    const slots = [slot(0, { vvb: 0.7 }), slot(1, { vvb: 0.72 }), slot(2, { vvb: 0.73 }), slot(3, { vvb: 0.75 }), slot(5, { vvb: 0.1 })];
    const runs = planRuns(slots, "vvb");
    expect(runs).toHaveLength(2);
    expect(runs[0]!.start).toBe(T0);
    expect(runs[0]!.end).toBe(T0 + 4 * Q);
    expect(runs[0]!.kwh).toBeCloseTo(2.9, 6);
    expect(planRuns(slots, "car")).toEqual([]);
  });
});

describe("cheapBands (R4, P2)", () => {
  it("bands the lowest quarter of the range and merges neighbours", () => {
    const prices = [price(0, 0.73), price(1, 0.73), price(2, 0.86), price(3, 0.86), price(4, 0.73)];
    expect(cheapBands(prices, T0, T0 + 5 * Q)).toEqual([
      [T0, T0 + 2 * Q],
      [T0 + 4 * Q, T0 + 5 * Q],
    ]);
    expect(cheapBands([price(0, 0.73), price(1, 0.73)], T0, T0 + 2 * Q)).toEqual([]);
  });
});

describe("bucketize and forecastTotals (F1, F3)", () => {
  it("sums quarters into the window, keeps the ceiling per window and means the price", () => {
    const plan = [slot(0, { car: 1.3, vvb: 0.54 }), slot(1, { car: 1.3 }), slot(2, { car: 1.3 }), slot(3, { car: 1.3 }), slot(4, {})];
    const prices = [price(0, 0.73), price(1, 0.73), price(2, 0.73), price(3, 0.73), price(4, 0.86, "estimated"), price(5, 0.86), price(6, 0.86), price(7, 0.86)];
    const [first, second] = bucketize(plan, prices, 60, T0, 2);
    expect(first!.baseline).toBeCloseTo(1.4, 6);
    expect(first!.p90).toBeCloseTo(2, 6);
    expect(first!.loads).toEqual({ car: 5.2, vvb: 0.54 });
    expect(first!.ceiling).toBe(10);
    expect(first!.price).toBeCloseTo(0.73, 6);
    expect(first!.estimated).toBe(false);
    expect(second!.estimated).toBe(true);
    const totals = forecastTotals([first!, second!], ["car", "vvb"]);
    expect(totals.managed).toBeCloseTo(5.74, 6);
    expect(totals.cheapPct).toBe(100);
  });
});

describe("nextClock (R3)", () => {
  it("finds tomorrow's 06:00 from 23:20 in Oslo", () => {
    const now = Date.parse("2026-09-23T21:20:00Z");
    expect(new Date(nextClock("06:00:00", now, "Europe/Oslo")!).toISOString()).toBe("2026-09-24T04:00:00.000Z");
    expect(nextClock("", now, "Europe/Oslo")).toBeNull();
  });
});

describe("readable (R1)", () => {
  it("lifts a dark palette colour on a dark card and leaves it on a light one", () => {
    expect(readable("#094bad", true)).not.toBe("#094bad");
    expect(readable("#094bad", false)).toBe("#094bad");
    expect(readable("#f4bd4a", true)).toBe("#f4bd4a");
  });
});

describe("holding a floor's setpoint (D-0501)", () => {
  it("is its own status, not idle, and a planned run still wins", () => {
    expect(rawStatus("idle", {}, false, false, LABELS, true)).toEqual({ kind: "holding", label: "Holder" });
    expect(rawStatus("waiting", {}, false, true, LABELS, true).label).toBe("Planlagt");
    expect(rawStatus("running_plan", {}, true, true, LABELS, true).label).toBe("Går");
  });

  it("merges the holding stretches apart from the runs", () => {
    const slots = [
      slot(0, { hall: 0.22 }, { hold_kwh: {} }),
      slot(1, {}, { hold_kwh: { hall: 0.08 } }),
      slot(2, {}, { hold_kwh: { hall: 0.08 } }),
    ];
    expect(planRuns(slots, "hall")).toHaveLength(1);
    const holds = holdRuns(slots, "hall");
    expect(holds).toHaveLength(1);
    expect(holds[0]!.start).toBe(T0 + Q);
    expect(holds[0]!.kwh).toBeCloseTo(0.16, 6);
  });

  it("counts in the load's total but not in the cheap share, which is of what the plan moves", () => {
    const plan = [0, 1, 2, 3].map((i) => slot(i, i === 0 ? { hall: 0.3 } : {}, { hold_kwh: { hall: 0.1 } }));
    const prices = [price(0, 0.73), price(1, 0.73), price(2, 0.73), price(3, 0.73), price(4, 0.86), price(5, 0.86), price(6, 0.86), price(7, 0.86)];
    const later = [4, 5, 6, 7].map((i) => slot(i, {}, { hold_kwh: { hall: 0.1 } }));
    const buckets = bucketize([...plan, ...later], prices, 60, T0, 2);
    expect(buckets[0]!.hold).toEqual({ hall: 0.4 });
    const totals = forecastTotals(buckets, ["hall"]);
    expect(totals.loads.hall).toBeCloseTo(0.3 + 0.8, 6);
    expect(totals.cheapPct).toBe(100);
  });
});
