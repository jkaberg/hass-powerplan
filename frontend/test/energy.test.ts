// D12 §9 16: the picker's collection, its fallback and the statistics grain.

import { describe, expect, it } from "vitest";

import {
  calendarMonth,
  dailyPeaks,
  energyCollection,
  followPeriod,
  gridHours,
  highest,
  monthRanking,
  statisticsPeriod,
  totalChange,
} from "../src/energy";
import type { HomeAssistant } from "../src/ha";

describe("the History picker", () => {
  it("finds HA's collection under `_energy_powerplan`", () => {
    const collection = { subscribe: () => () => undefined };
    const hass = { connection: { _energy_powerplan: collection } } as unknown as HomeAssistant;
    expect(energyCollection(hass)).toBe(collection);
    expect(energyCollection({ connection: {} } as unknown as HomeAssistant)).toBeUndefined();
  });

  it("hands over the picked period at once, without waiting for the energy data (F8)", () => {
    let later: ((data: { start: Date; end?: Date }) => void) | undefined;
    const start = new Date(2026, 8, 1);
    const end = new Date(2026, 9, 1);
    const collection = { start, end, subscribe: (cb: typeof later) => ((later = cb), () => undefined) };
    const hass = { connection: { _energy_powerplan: collection } } as unknown as HomeAssistant;
    const seen: Array<{ start: Date; end: Date }> = [];
    const stop = followPeriod(hass, (period) => seen.push(period));
    expect(seen).toEqual([{ start, end }]);
    later!({ start, end }); // the first emit, ≈ 25 s later on the house: the same period, not again
    later!({ start: new Date(2026, 7, 1), end: start });
    expect(seen).toHaveLength(2);
    stop();
  });

  it("falls back to the calendar month", () => {
    const { start, end } = calendarMonth(new Date(2026, 8, 23, 20, 25));
    expect([start.getMonth(), start.getDate(), end.getMonth(), end.getDate()]).toEqual([8, 1, 9, 1]);
  });

  it("asks by the hour for a day, by the day for a month, by the month for a year", () => {
    const day = { start: new Date(2026, 8, 22), end: new Date(2026, 8, 23) };
    expect(statisticsPeriod(day)).toBe("hour");
    expect(statisticsPeriod(calendarMonth(new Date(2026, 8, 23)))).toBe("day");
    expect(statisticsPeriod({ start: new Date(2026, 0, 1), end: new Date(2027, 0, 1) })).toBe("month");
  });

  it("sums a period's change and finds its highest hour", () => {
    const rows = [
      { start: 0, end: 1, change: 8.44, max: 8.44 },
      { start: 1, end: 2, change: 7.3, max: 7.3 },
    ];
    expect(totalChange(rows)).toBeCloseTo(15.74);
    expect(highest(rows)?.start).toBe(0);
    expect(totalChange([])).toBeNull();
  });
});

describe("the grid sources stand in before window_used (D2, D4)", () => {
  const h = (iso: string) => Date.parse(iso);
  // One day in Oslo: two Energy-preferences sources (day and night meters), hourly change in kWh.
  const stats = {
    "sensor.day": [
      { start: h("2026-09-21T22:00:00Z"), end: h("2026-09-21T23:00:00Z"), change: 0 },
      { start: h("2026-09-21T23:00:00Z"), end: h("2026-09-22T00:00:00Z"), change: 0 },
      { start: h("2026-09-22T04:00:00Z"), end: h("2026-09-22T05:00:00Z"), change: 4.9 },
    ],
    "sensor.night": [
      { start: h("2026-09-21T22:00:00Z"), end: h("2026-09-21T23:00:00Z"), change: 8.44 },
      { start: h("2026-09-21T23:00:00Z"), end: h("2026-09-22T00:00:00Z"), change: 7.3 },
    ],
  };

  it("sums the sources per hour and finds 22 Sep's highest hour, 8,44 kWh at 00–01", () => {
    const rows = gridHours(stats, ["sensor.day", "sensor.night"]);
    expect(rows.map((row) => row.max)).toEqual([8.44, 7.3, 4.9]);
    expect(highest(rows)?.start).toBe(h("2026-09-21T22:00:00Z"));
    expect(dailyPeaks(rows, "Europe/Oslo")).toEqual([["2026-09-22", 8.44]]);
    expect(gridHours(stats, ["sensor.day"], () => 1 / 1000)[2]?.max).toBeCloseTo(0.0049, 9);
  });

  it("ranks the month in progress by advice, another month from the statistics", async () => {
    const calls: unknown[] = [];
    const hass = {
      states: {},
      callWS: async (message: Record<string, unknown>) => {
        calls.push(message);
        return message.statistic_ids && (message.statistic_ids as string[]).includes("sensor.used")
          ? { "sensor.used": [{ start: h("2026-08-13T10:00:00Z"), end: h("2026-08-13T11:00:00Z"), max: 8.86 }] }
          : { "sensor.night": stats["sensor.night"].map((row) => ({ ...row, start: row.start - 30 * 86_400_000 })) };
      },
    } as unknown as HomeAssistant;
    const advice = [{ key: "top_entries", entries: [["2026-09-13", 8.86], ["2026-09-17", 9.12], ["2026-09-21", 8.94]] }];
    const september = { start: new Date("2026-09-01T00:00:00+02:00"), end: new Date("2026-10-01T00:00:00+02:00") };
    const now = new Date("2026-09-23T20:00:00Z");
    expect(await monthRanking(hass, september, { used: "sensor.used", grid: ["sensor.night"], advice }, "Europe/Oslo", now)).toEqual([
      ["2026-09-17", 9.12],
      ["2026-09-21", 8.94],
      ["2026-09-13", 8.86],
    ]);
    expect(calls).toHaveLength(0);
    const august = { start: new Date("2026-08-01T00:00:00+02:00"), end: new Date("2026-09-01T00:00:00+02:00") };
    const ranked = await monthRanking(hass, august, { used: "sensor.used", grid: ["sensor.night"], advice }, "Europe/Oslo", now);
    expect(ranked[0]).toEqual(["2026-08-13", 8.86]);
    expect(ranked).toContainEqual(["2026-08-23", 8.44]);
  });
});
