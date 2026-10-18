// D12 §5.12 R1, R6 (D-0864): the appliances card's row order and fold.
import { describe, expect, it } from "vitest";
import { applianceFolded, applianceOrder, type ApplianceRank } from "../src/transforms";

const H = 3_600_000;
const row = (name: string, kind: string, loweredH = 0, nextStart = Number.MAX_SAFE_INTEGER): ApplianceRank =>
  ({ name, kind, loweredMs: loweredH * H, nextStart });

describe("applianceOrder", () => {
  it("puts the appliance lowered longest on top, then the rest by kind", () => {
    const rows = [
      row("EV", "running"),
      row("Kjøkken", "holding", 2),
      row("Bad", "idle"),
      row("TV-stua", "planned", 14, 5),
      row("Varmtvann", "waiting", 0, 3),
    ];
    expect(applianceOrder(rows).map((r) => r.name)).toEqual(["TV-stua", "Kjøkken", "EV", "Varmtvann", "Bad"]);
  });

  it("orders equal lowering by kind, next start and name", () => {
    const rows = [row("B", "planned", 1, 9), row("A", "planned", 1, 9), row("C", "running", 1), row("D", "planned", 1, 2)];
    expect(applianceOrder(rows).map((r) => r.name)).toEqual(["C", "D", "A", "B"]);
  });
});

describe("applianceFolded", () => {
  it("folds what only holds or needs nothing, never what is lowered ahead", () => {
    expect(applianceFolded(row("Bad", "idle"))).toBe(true);
    expect(applianceFolded(row("Stue", "holding"))).toBe(true);
    expect(applianceFolded(row("Kjøkken", "holding", 2))).toBe(false);
    for (const kind of ["running", "waiting", "paused", "planned", "manual", "unavailable"]) {
      expect(applianceFolded(row("x", kind))).toBe(false);
    }
  });
});
