// D12 §9 42, D9 §9 16: every card at every sections-view width, light and dark, fits.
import "../../src/cards";
import { afterAll, beforeAll, describe, expect, test, vi } from "vitest";
import { page } from "vitest/browser";
import { SPECS } from "./cards";
import { breaches, mount, NOW, THEMES, WIDTHS } from "./harness";

beforeAll(async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  await page.viewport(1800, 1200);
});
afterAll(() => vi.useRealTimers());

describe.each(SPECS)("$name", (spec) => {
  test.each(THEMES.flatMap((theme) => WIDTHS.map((width) => [width, theme] as const)))("%i px, %s", async (width, theme) => {
    const m = await mount(spec.tag, spec.config, width, theme, spec.span, spec.rows);
    if ((import.meta as unknown as { env: Record<string, string | undefined> }).env.VITE_PP_SHOTS) await page.screenshot({ path: `__screenshots__/${spec.name.replace(/ /g, "-")}-${width}-${theme}.png`, element: m.frame });
    expect(breaches(m)).toEqual([]);
  });
});
