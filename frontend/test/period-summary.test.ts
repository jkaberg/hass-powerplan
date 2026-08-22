// D12 §5.7: the period summary's pure half, on the live house's values.

import { describe, expect, it } from "vitest";

import { totalChange } from "../src/energy";
import {
  countsDecision,
  formatSummary,
  inMonthOf,
  periodKind,
  sumChanges,
  summaryMode,
  topEntries,
  withoutDate,
} from "../src/transforms";

const OSLO = "Europe/Oslo";
/** Midnight in Oslo (CEST, UTC+2) on a September day. */
const oslo = (day: number, month = 9) => new Date(Date.UTC(2026, month - 1, day, -2));
const NOW = new Date(Date.UTC(2026, 8, 23, 18, 25));
/** Strip the grouping space, which Intl writes as U+00A0 or U+202F. */
const plain = (text: string) => text.replace(/[  ]/g, " ");

describe("the period summary", () => {
  it("reads a day, a calendar month and any other range in HA's zone", () => {
    expect(periodKind({ start: oslo(22), end: oslo(23) }, OSLO)).toBe("day");
    // The picker's own day ends at 23:59:59.999.
    expect(periodKind({ start: oslo(22), end: new Date(oslo(23).getTime() - 1) }, OSLO)).toBe("day");
    expect(periodKind({ start: oslo(1), end: oslo(1, 10) }, OSLO)).toBe("month");
    expect(periodKind({ start: oslo(1), end: oslo(24) }, OSLO)).toBe("range");
    expect(periodKind({ start: oslo(14), end: oslo(21) }, OSLO)).toBe("range");
  });

  it("shows the capacity step for the month in progress only", () => {
    expect(summaryMode("month", inMonthOf(oslo(1), NOW, OSLO))).toBe("capacity");
    expect(summaryMode("month", inMonthOf(oslo(1, 8), NOW, OSLO))).toBe("peak");
    expect(summaryMode("day", true)).toBe("hour");
    expect(summaryMode("range", true)).toBe("peak");
  });

  it("totals September to the 23rd: 417,82 kr, 2,53 kr, 1 177 kWh, 8,97 kW", () => {
    const cost = [
      { start: 0, end: 1, change: 400.5 },
      { start: 1, end: 2, change: 17.32 },
    ];
    expect(formatSummary(totalChange(cost)!, "money", "nb")).toBe("417,82");
    expect(formatSummary(2.53, "money", "nb")).toBe("2,53");
    const grid = { "sensor.import_a": [{ change: 1000 }, { change: 100.4 }], "sensor.import_b": [{ change: 76.6 }] };
    const kwh = sumChanges(grid, ["sensor.import_a", "sensor.import_b"])!;
    expect(kwh).toBeCloseTo(1177);
    expect(plain(formatSummary(kwh, "kwh", "nb"))).toBe("1 177");
    expect(formatSummary(8.97, "kw", "nb")).toBe("8,97");
    expect(formatSummary(42.26, "kwh", "nb")).toBe("42,3");
    expect(sumChanges({ "sensor.import_a": [] }, ["sensor.import_a", "sensor.none"])).toBeNull();
  });

  it("says 22 September's 8,44 kWh hour does not count against the 13th's 8,86", () => {
    const items = [
      {
        key: "top_entries",
        entries: [
          ["2026-09-02", 9.41],
          ["2026-09-13", 8.86],
          ["2026-09-08", 9.12],
        ],
      },
    ];
    const decision = countsDecision(8.44, topEntries(items));
    expect(decision).toEqual({ counts: false, third: ["2026-09-13", 8.86] });
    expect(countsDecision(8.86, topEntries(items)).counts).toBe(true);
    expect(countsDecision(1.2, [["2026-09-01", 3.1]])).toEqual({ counts: true });
  });

  it("drops the date from `collecting` when no statistics row gives one", () => {
    expect(withoutDate("Collecting statistics (since {date})")).toBe("Collecting statistics");
    expect(withoutDate("Collecting since {date}")).toBe("Collecting since");
    expect(withoutDate("Collecting")).toBe("Collecting");
  });
});
