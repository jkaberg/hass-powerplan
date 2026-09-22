import { describe, expect, it } from "vitest";
import { outlierCap } from "../src/r3-util";

describe("outlierCap", () => {
  it("caps the axis under a booked lump (the house, 22–24 Sep: 14,46 · 424,37 · 15,21 kr)", () => {
    expect(outlierCap([14.46, 424.37, 15.21])).toBeCloseTo(15.21 * 1.25, 6);
  });
  it("leaves ordinary days alone", () => {
    expect(outlierCap([14.46, 30, 15.21])).toBeNull();
    expect(outlierCap([424.37])).toBeNull();
    expect(outlierCap([])).toBeNull();
    expect(outlierCap([5, 0])).toBeNull();
  });
});
