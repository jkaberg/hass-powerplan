// D12 §9 32: the month's results in one sentence each (§5.19).
import { describe, expect, it } from "vitest";

import { type HassEntity, RESULT_LABELS, resultLines } from "../src/r3-util";

const entity = (state: string, attributes: Record<string, unknown>): HassEntity => ({
  entity_id: "sensor.x", state, attributes, last_changed: "",
});
const L = RESULT_LABELS.nb!;
const cost = entity("432.15", { energy_cost: "35.15 NOK", capacity_fee: "397.00 NOK" });
// The house's attributes once its books are clean: the EV moved off the evening peak.
const savings = entity("190.4", {
  savings_confidence: "ok", capacity_savings: "188.00 NOK", energy_savings: "2.40 NOK",
  capacity_step: "5–10 kW", capacity_step_without: "10–15 kW", price_paid: 0.7412, price_reference: 0.8307,
});

describe("resultLines (§5.19)", () => {
  it("says R1–R3 and no R4 at zero deviations", () => {
    const lines = resultLines(savings, cost, entity("0", {}), {}, L, "nb", "kr");
    expect(lines).toEqual([
      "Spart 190 kr · effekttrinn 188 · billigere timer 2",
      "Effekttrinn 5–10 kW – uten PowerPlan 10–15 kW",
      "Apparatene betalte 0,74 mot 0,83 kr/kWh",
    ]);
  });

  it("names the appliance with the most comfort minutes in R4, beside the misses and the hours over", () => {
    const dev = entity("4", {
      comfort_min: { a: 12, b: 125 }, deadlines_missed: { ev: 1 }, over_windows: 2,
    });
    const lines = resultLines(undefined, cost, dev, { a: "Bad 1. etasje", b: "Gulvvarme stua" }, L, "nb", "kr");
    expect(lines).toEqual(["1 frist nådd ikke · 2 t 5 min under komfort: Gulvvarme stua · 2 timer over effektmålet"]);
  });

  it("says a negative saving as it is, and nothing without a reference", () => {
    const worse = entity("-3.2", { savings_confidence: "ok" });
    expect(resultLines(worse, cost, undefined, {}, L, "nb", "kr")).toEqual(["Kostet 3 kr mer enn uten PowerPlan"]);
    const none = entity("4602.1", { savings_confidence: "none", capacity_step: "5–10 kW", capacity_step_without: "5–10 kW" });
    expect(resultLines(none, cost, undefined, {}, L, "nb", "kr")).toEqual(["Effekttrinn 5–10 kW – det samme uten PowerPlan"]);
  });

  // D12 §9 33: a ledger opened after the 1st says so (D-0692).
  it("says a partial month's basis in R5, and nothing for a whole month", () => {
    const partial = entity("457.40", {
      energy_cost: "60.40 NOK", capacity_fee: "397.00 NOK", partial: true, energy_since: "2026-09-25T09:00:00+00:00",
    });
    expect(resultLines(undefined, partial, undefined, {}, L, "nb", "kr")).toEqual([
      "Siden 25. sep.: 60 kr strøm + hele månedens effektledd 397 kr",
    ]);
    expect(resultLines(undefined, cost, undefined, {}, L, "nb", "kr")).toEqual([]);
  });
});
