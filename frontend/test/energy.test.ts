// D12 §9 16: the picker's collection, its fallback and the statistics grain.

import { describe, expect, it } from "vitest";

import { calendarMonth, energyCollection, highest, statisticsPeriod, totalChange } from "../src/energy";
import type { HomeAssistant } from "../src/ha";

describe("the History picker", () => {
  it("finds HA's collection under `_energy_powerplan`", () => {
    const collection = { subscribe: () => () => undefined };
    const hass = { connection: { _energy_powerplan: collection } } as unknown as HomeAssistant;
    expect(energyCollection(hass)).toBe(collection);
    expect(energyCollection({ connection: {} } as unknown as HomeAssistant)).toBeUndefined();
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
