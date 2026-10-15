// The cheap-hours line (D-0840): the lowest quarter of the known prices, not of the min-to-max range.
import { describe, expect, it } from "vitest";

import { cheapLimit } from "../src/r3-util";

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
