// D12 §9 35–41: the pure halves of the iteration-6 charts, on the reference house's numbers.
import { describe, expect, it } from "vitest";
import { costParts, plannedByHour } from "../src/r3-util";
import plan from "./fixtures/plan.json";
import {
  heldHours, hourStrip, previousPeriod, versus,
  carpetAlpha, carpetGrid, energySlices, headroomStrip, laneRuns, levelSeries, profileSummary, profileView, runHours,
  type TariffStep,
} from "../src/transforms";

const STEPS: TariffStep[] = [
  { name: "0–2 kW", from_kw: 0, to_kw: 2, fee: "131.00 NOK" },
  { name: "2–5 kW", from_kw: 2, to_kw: 5, fee: "233.00 NOK" },
  { name: "5–10 kW", from_kw: 5, to_kw: 10, fee: "397.00 NOK" },
  { name: "10–15 kW", from_kw: 10, to_kw: 15, fee: "585.00 NOK" },
  { name: "over 15 kW", from_kw: 15, to_kw: null, fee: "775.00 NOK" },
];

describe("V1 headroomStrip (§9 35)", () => {
  it("runs from the current step to the next on the house", () => {
    const s = headroomStrip(8.97, STEPS, 2)!;
    expect([s.lo, s.hi]).toEqual([5, 15]);
    expect(s.at(8.97)).toBeCloseTo(0.397, 3);
    expect(s.at(9.33)).toBeCloseTo(0.433, 3);
    expect(s.at(11.95)).toBeCloseTo(0.695, 3);
    expect(s.segments.map((x) => [x.tone, x.current])).toEqual([["ok", true], ["warn", false]]);
  });
  it("ends a top step at 1,25 × its lower bound", () => {
    const s = headroomStrip(16, STEPS, 4)!;
    expect(s.hi).toBeCloseTo(18.75);
    expect(s.segments).toHaveLength(1);
  });
  it("has nothing to draw without steps", () => expect(headroomStrip(8.97, [], null)).toBeNull());
});

describe("V2 costParts (§9 36)", () => {
  const house = { by_party: { grid: "412.51319502", supplier: "36.0404", state: "22.0449128800" }, capacity_fee: "397.00 NOK", energy_cost: "73.60 NOK" };
  it("splits the house's month by party", () => {
    const parts = costParts(house);
    expect(parts.map((p) => p.key)).toEqual(["capacity", "grid", "supplier", "state"]);
    expect(parts.map((p) => +p.value.toFixed(2))).toEqual([397, 15.51, 36.04, 22.04]);
    expect(+parts.reduce((a, p) => a + p.value, 0).toFixed(2)).toBe(470.6);
  });
  it("leaves a zero part out", () => expect(costParts({ ...house, by_party: { grid: "397", supplier: "10", state: "0" } }).map((p) => p.key)).toEqual(["capacity", "supplier"]));
  it("falls back to energy and capacity", () => expect(costParts({ capacity_fee: "397.00 NOK", energy_cost: "73.60 NOK" }).map((p) => p.key)).toEqual(["energy", "capacity"]));
});

describe("V4 carpet (§9 38)", () => {
  const zone = "Europe/Oslo";
  it("places an autumn DST day's 25 hours in 24 cells", () => {
    const start = new Date("2026-10-24T22:00:00Z"), end = new Date("2026-10-25T23:00:00Z");
    const rows = Array.from({ length: 25 }, (_, i) => ({ start: start.getTime() + i * 3_600_000, max: i }));
    const [day] = carpetGrid(rows, start, end, zone);
    expect(day!.day).toBe("2026-10-25");
    expect(day!.cells).toHaveLength(24);
    expect(day!.cells[2]).toBe(3); // the repeated 02:00 keeps the later hour
    expect(day!.cells[23]).toBe(24);
  });
  it("leaves the spring day's skipped hour empty", () => {
    const start = new Date("2026-03-28T23:00:00Z"), end = new Date("2026-03-29T22:00:00Z");
    const rows = Array.from({ length: 23 }, (_, i) => ({ start: start.getTime() + i * 3_600_000, max: 1 }));
    const [day] = carpetGrid(rows, start, end, zone);
    expect(day!.cells.filter((v) => v === null)).toHaveLength(1);
    expect(day!.cells[2]).toBeNull();
  });
  it("fills from 0,07 to 0,95 of the ceiling", () => {
    expect(carpetAlpha(0, 9.7)).toBeCloseTo(0.07);
    expect(carpetAlpha(9.7, 9.7)).toBeCloseTo(0.95);
    expect(carpetAlpha(20, 9.7)).toBeCloseTo(0.95);
  });
  it("marks an hour with five minutes of charging, not four", () => {
    const t = Date.parse("2026-09-20T20:00:00Z") / 1000;
    const four = runHours({ ev: [{ s: "charging", lu: t }, { s: "done", lu: t + 240 }] }, { ev: "car" }, (t + 3600) * 1000, zone);
    const five = runHours({ ev: [{ s: "charging", lu: t }, { s: "done", lu: t + 300 }] }, { ev: "car" }, (t + 3600) * 1000, zone);
    expect(four.size).toBe(0);
    expect([...five.entries()]).toEqual([["2026-09-20 22", new Set(["car"])]]);
  });
});

describe("V5 energySlices (§9 39)", () => {
  const loads = [5, 40, 30, 20, 10, 3].map((kwh, i) => ({ id: `l${i}`, name: `L${i}`, color: "#000", kwh, moved: i === 1 ? 12 : null }));
  it("keeps four appliances and folds the rest", () => {
    const s = energySlices(loads, 200, (n) => `${n} andre`, "Resten");
    expect(s.map((x) => x.key)).toEqual(["l1", "l2", "l3", "l4", "other", "rest"]);
    expect(s[4]).toMatchObject({ name: "2 andre", kwh: 8 });
    expect(s[5]!.kwh).toBeCloseTo(92);
    expect(s[0]!.moved).toBe(12);
  });
  it("never gives the house less than nothing", () => expect(energySlices(loads, 50, () => "", "R").some((x) => x.key === "rest")).toBe(false));
  it("has no house without a grid source", () => expect(energySlices(loads, null, () => "", "R").some((x) => x.key === "rest")).toBe(false));
});

describe("V6 day profile (§9 40)", () => {
  const cf = [0.9, 0.8, 0.8, 0.8, 0.8, 0.9, 1.3, 2.1, 2.0, 1.5, 1.3, 1.3, 1.3, 1.3, 1.4, 1.6, 2.2, 3.1, 3.4, 3.0, 2.6, 2.0, 1.5, 1.1];
  const delta = [0.7, 0.7, 0.4, 0.2, 0, 0, 0, -0.2, -0.2, 0, 0, 0, 0, 0, 0, -0.1, -0.3, -0.7, -0.9, -0.7, -0.4, 0, 0.7, 0.8];
  const sep = { month: "2026-09", kwh: cf.map((v, i) => (v + delta[i]!) * 20), cf_kwh: cf.map((v) => v * 20), days: 20 };
  const aug = { ...sep, month: "2026-08" };
  it("picks this month, last month or none by the period's start", () => {
    const a = { day_profile: sep, previous_day_profile: aug };
    expect(profileView(a, new Date("2026-09-10T00:00:00Z"), "Europe/Oslo")?.month).toBe("2026-09");
    expect(profileView(a, new Date("2026-08-10T00:00:00Z"), "Europe/Oslo")?.month).toBe("2026-08");
    expect(profileView(a, new Date("2026-06-10T00:00:00Z"), "Europe/Oslo")).toBeNull();
    expect(profileView(a, new Date("2026-08-31T22:30:00Z"), "Europe/Oslo")?.month).toBe("2026-09"); // Oslo's 1 Sep
  });
  it("moves ½ Σ |Δ| a day, from the evening to the night", () => {
    const s = profileSummary(sep);
    expect(s.movedPerDay).toBeCloseTo(3.5);
    expect(s.from).toEqual([15, 21]);
    expect(s.to).toEqual([22, 4]);
  });
});

describe("V7 level chart (§9 41)", () => {
  it("reads a state and an attribute, and drops unavailable", () => {
    expect(levelSeries([{ s: "21.4", lu: 1 }, { s: "unavailable", lu: 2 }, { s: "21.6", lu: 3 }])).toEqual([{ t: 1000, v: 21.4 }, { t: 3000, v: 21.6 }]);
    const climate = [{ s: "heat", lu: 1, a: { current_temperature: 22.5 } }, { s: "heat", lu: 2 }, { s: "heat", lu: 3, a: { current_temperature: 22.1 } }];
    expect(levelSeries(climate, "current_temperature").map((p) => p.v)).toEqual([22.5, 22.5, 22.1]);
  });
  it("merges the TV-stua states into heating and waiting runs", () => {
    const rows = [{ s: "running_plan", lu: 0 }, { s: "running_plan", lu: 900 }, { s: "waiting", lu: 1800 }, { s: "idle", lu: 2700 }, { s: "running_plan", lu: 3600 }];
    expect(laneRuns(rows, 4_500_000)).toEqual([
      { start: 0, end: 1_800_000, kind: "run" },
      { start: 1_800_000, end: 2_700_000, kind: "wait" },
      { start: 3_600_000, end: 4_500_000, kind: "run" },
    ]);
  });
});

describe("V3 plannedByHour (§9 37)", () => {
  it("marks 22, 23, 00 and 01 on the house's plan and names the water heater's 2,92 kWh", () => {
    const t0 = Date.parse("2026-09-25T22:00:00Z"); // local midnight, 26 Sep
    const hours = plannedByHour(plan.slots, t0, t0 + 48 * 3_600_000);
    const local = [...hours.keys()].map((t) => new Date(t).toLocaleString("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone: "Europe/Oslo" }));
    expect(local).toEqual(["22", "23", "00", "01"]);
    expect(hours.values().next().value!.get("01M37FMNCJC8PNNHH17N8CJ3GY")).toBeCloseTo(2.92, 2);
  });
  it("leaves out an hour of crumbs", () => {
    const t0 = 0;
    expect(plannedByHour([{ start: new Date(0).toISOString(), planned_kwh: { a: 0.04 } }], t0, 3_600_000).size).toBe(0);
  });
});

describe("H1 hourStrip (§9 44)", () => {
  const now = Date.parse("2026-09-26T17:30:00Z");
  const hour = (h: number) => Date.parse("2026-09-26T00:00:00Z") + h * 3_600_000;
  it("gives the 12 closed hours before the one in progress", () => {
    const used = [{ start: hour(16), max: 10.2 }, { start: hour(15), max: 3.1 }];
    const strip = hourStrip(used, [{ start: hour(16), mean: 9.7 }, { start: hour(15), mean: 9.7 }], [{ start: hour(15), max: 1 }], now);
    expect(strip).toHaveLength(12);
    expect(strip[11]!.start).toBe(hour(16));
    expect(strip[0]!.start).toBe(hour(5));
    expect(strip[11]).toMatchObject({ kwh: 10.2, over: true, held: false });
    expect(strip[10]).toMatchObject({ kwh: 3.1, over: false, held: true });
    expect(strip[0]).toMatchObject({ kwh: null, over: false, held: false });
  });
});

describe("H4 previousPeriod and versus (§9 45)", () => {
  const zone = "Europe/Oslo";
  const sep = { start: new Date("2026-08-31T22:00:00Z"), end: new Date("2026-09-30T22:00:00Z") };
  it("gives August for a whole September", () => {
    const p = previousPeriod(sep, Date.parse("2026-10-05T00:00:00Z"), zone);
    expect(p.start.toISOString()).toBe("2026-07-31T22:00:00.000Z");
    expect(p.end.toISOString()).toBe("2026-08-30T22:00:00.000Z");
  });
  it("gives the same days of August for September in progress", () => {
    const p = previousPeriod(sep, Date.parse("2026-09-26T17:30:00Z"), zone);
    expect(p.start.toISOString()).toBe("2026-07-31T22:00:00.000Z");
    expect(p.end.getTime() - p.start.getTime()).toBe(Date.parse("2026-09-26T17:30:00Z") - sep.start.getTime());
  });
  it("gives the day before for a day", () => {
    const day = { start: new Date("2026-09-25T22:00:00Z"), end: new Date("2026-09-26T22:00:00Z") };
    const p = previousPeriod(day, Date.parse("2026-09-27T00:00:00Z"), zone);
    expect(p.start.toISOString()).toBe("2026-09-24T22:00:00.000Z");
    expect(p.end.toISOString()).toBe("2026-09-25T22:00:00.000Z");
  });
  it("compares in whole percent, never against nothing", () => {
    expect(versus(88, 100)).toBe(-12);
    expect(versus(5, 0)).toBeNull();
    expect(versus(null, 10)).toBeNull();
  });
});

describe("H2, H3 heldHours (§9 46)", () => {
  it("keys the hours whose control level reached 1, in the house's zone", () => {
    const rows = [{ start: Date.parse("2026-09-26T16:00:00Z"), max: 1 }, { start: Date.parse("2026-09-26T17:00:00Z"), max: 0 }, { start: Date.parse("2026-09-26T18:00:00Z"), max: 3 }];
    expect([...heldHours(rows, "Europe/Oslo")]).toEqual(["2026-09-26 18", "2026-09-26 20"]);
    expect([...heldHours(rows, "Europe/Oslo", true)]).toEqual(["2026-09-26"]);
  });
});
