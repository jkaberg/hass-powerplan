// D12 §5.1: the strategy points `{dashboard}` paths at the dashboard it is on.

import { describe, expect, it } from "vitest";

import { dashboardPath, resolvePaths } from "../src/transforms";

describe("resolvePaths", () => {
  it("rewrites back paths and navigation paths, and nothing else", () => {
    const config = {
      views: [
        { path: "overview", title: "{dashboard} is a word here" },
        {
          path: "appliance-01m37",
          back_path: "{dashboard}/overview",
          sections: [{ cards: [{ tap_action: { action: "navigate", navigation_path: "{dashboard}/history" } }] }],
        },
      ],
    };
    const out = resolvePaths(config, dashboardPath("/dashboard-powerplan/appliance-01m37"));
    expect(out.views[1]!.back_path).toBe("/dashboard-powerplan/overview");
    expect(out.views[1]!.sections![0]!.cards[0]!.tap_action.navigation_path).toBe("/dashboard-powerplan/history");
    expect(out.views[0]!.title).toBe("{dashboard} is a word here");
    expect(config.views[1]!.back_path).toBe("{dashboard}/overview");
  });
});
