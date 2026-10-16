// The cheap-hours line (D-0840): the lowest quarter of the known prices, not of the min-to-max range.
import { describe, expect, it } from "vitest";

import { cheapLimit } from "../src/r3-util";
import { bucketize } from "../src/forecast";

// NO3 on 15 Oct 2026, spot with VAT and Tensio's grid tariff, per quarter from local midnight: a 35 % spread
// (1,30-1,87 around a 1,64 mean) whose one dip, 1,30 at 06:45, sat 0,17 under the next-cheapest quarter.
const DAY = [1.6489, 1.5527, 1.5423, 1.5334, 1.5408, 1.535, 1.5336, 1.5317, 1.5318, 1.5317, 1.5317, 1.5317, 1.5318, 1.5317, 1.5317, 1.531, 1.5318, 1.5317, 1.5317, 1.5286, 1.5327, 1.534, 1.5345, 1.5399, 1.5308, 1.5098, 1.4954, 1.3035, 1.4719, 1.4867, 1.4965, 1.505, 1.5427, 1.5635, 1.627, 1.6404, 1.6351, 1.6338, 1.5727, 1.6717, 1.663, 1.6676, 1.6867, 1.6761, 1.7843, 1.7767, 1.739, 1.6719, 1.6719, 1.6815, 1.6734, 1.6743, 1.6717, 1.6718, 1.6823, 1.6823, 1.6634, 1.6612, 1.6575, 1.6507, 1.6278, 1.5572, 1.6761, 1.7139, 1.7075, 1.7634, 1.8006, 1.8318, 1.8001, 1.8413, 1.8631, 1.8722, 1.8536, 1.8441, 1.8393, 1.8433, 1.8317, 1.8551, 1.8496, 1.8334, 1.8645, 1.8479, 1.7932, 1.7479, 1.7876, 1.7326, 1.7045, 1.6482, 1.5633, 1.5278, 1.4868, 1.4662, 1.4744, 1.4626, 1.4542, 1.4499];

describe("cheapLimit", () => {
  const known = DAY.map((p) => ({ p, est: false }));

  it("marks a quarter of the day, where the range rule marked one quarter hour", () => {
    const lo = Math.min(...DAY), hi = Math.max(...DAY);
    expect(DAY.filter((p) => p <= lo + (hi - lo) * 0.25)).toHaveLength(1);
    const limit = cheapLimit(known)!;
    expect(DAY.filter((p) => p <= limit)).toHaveLength(25); // 24 and a tie
  });

  it("ranks the known prices alone: a flat estimate beside them moves nothing", () => {
    const estimated = DAY.map(() => ({ p: 1.6029, est: true }));
    expect(cheapLimit([...known, ...estimated])).toBe(cheapLimit(known));
    expect(cheapLimit(estimated)).toBeNull();
  });

  it("says nothing on a flat window or an empty one", () => {
    expect(cheapLimit([])).toBeNull();
    expect(cheapLimit([1, 1, 1, 1].map((p) => ({ p, est: false })))).toBeNull();
  });
});

// Norgespris on 16 Oct 2026 (CEST), two levels: 0,8779 from 06:00 to 22:00 and 0,7379 from 22:00 to 06:00.
const Q = 15 * 60e3;
const DAY0 = Date.parse("2026-10-16T00:00:00+02:00");
const at = (h: number) => DAY0 + h * 3600e3;
const level = (t: number) => { const h = ((t - DAY0) / 3600e3) % 24; return h >= 6 && h < 22 ? 0.8779 : 0.7379; };
const slots = (from: number, to: number, est: (t: number) => boolean) =>
  Array.from({ length: Math.round((to - from) / Q) }, (_, i) => {
    const s = from + i * Q;
    return { s, e: s + Q, p: level(s), est: est(s) };
  });
/** The hours [from, to) of the quarters a band marks, merged. */
const marked = (rows: { s: number; e: number; p: number; est: boolean }[], fixed: boolean) => {
  const thr = cheapLimit(rows, fixed);
  const out: [number, number][] = [];
  for (const r of rows) {
    if (thr == null || !(fixed || !r.est) || r.p > thr) continue;
    const last = out[out.length - 1];
    if (last && last[1] === r.s) last[1] = r.e; else out.push([r.s, r.e]);
  }
  return out.map(([a, b]) => [(a - DAY0) / 3600e3, (b - DAY0) / 3600e3]);
};

describe("a two-level fixed price (16 Oct, viewed at 07:30)", () => {
  const today = slots(at(7.5), at(24), () => false);
  const tomorrow = slots(at(24), at(48), () => true);   // the spot is unpublished, so every slot is an estimate

  it("marks the night only, not the whole day", () => {
    expect(today.filter((x) => x.p === 0.8779)).toHaveLength(58);
    expect(marked([...today, ...tomorrow], false)).toEqual([[22, 24]]);
  });

  it("under the fixed price tomorrow's night counts too", () => {
    expect(marked([...today, ...tomorrow], true)).toEqual([[22, 30], [46, 48]]);
  });

  it("the plan card's buckets and share follow the same rule", () => {
    const rows = [...today, ...tomorrow];
    const b = bucketize([], 15, at(7), 41, rows);
    const thr = cheapLimit(b.flatMap((k) => (k.price == null ? [] : [{ p: k.price, est: k.estimated }])), true);
    expect(thr).toBeCloseTo(0.7379, 6);
  });

  it("the price card draws the night over the whole day, from midnight", async () => {
    const g = globalThis as any;
    g.HTMLElement ??= class {};
    const { cheapBandRuns } = await import("../src/price-card");
    const pts = () => [...slots(at(0), at(24), () => false), ...slots(at(24), at(48), () => true)]
      .map((x) => ({ s: x.s, e: x.e, din: x.p, est: x.est }));
    expect(cheapBandRuns(pts(), true).map(([a, b]) => [(a - DAY0) / 3600e3, (b - DAY0) / 3600e3]))
      .toEqual([[0, 6], [22, 30], [46, 48]]);
  });

  it("a perfectly flat curve gives no band", () => {
    const flat = slots(at(0), at(24), () => false).map((x) => ({ ...x, p: 0.8779 }));
    expect(marked(flat, true)).toEqual([]);
    expect(marked(flat, false)).toEqual([]);
  });
});

describe("15 Oct spot day", () => {
  it("still marks 01:45-05:00, 06:00-08:00 and 22:15-24:00", () => {
    const rows = DAY.map((p, i) => ({ s: i * Q, e: (i + 1) * Q, p, est: false }));
    const thr = cheapLimit(rows)!;
    const hrs = rows.filter((r) => r.p <= thr).map((r) => r.s / 3600e3);
    expect(hrs[0]).toBe(1.75);
    expect(Math.max(...hrs.filter((h) => h < 5))).toBe(4.75);
    expect(hrs.filter((h) => h >= 6 && h < 8)).toHaveLength(8);
    expect(hrs.filter((h) => h >= 22.25)).toHaveLength(7);
    expect(hrs.every((h) => h < 5 || (h >= 6 && h < 8) || h >= 22.25)).toBe(true);
  });
});
