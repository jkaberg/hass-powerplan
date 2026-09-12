// Glue for timeline-card.ts, plan mode (iteration 4).
//
//   import { renderPlanMode, observePlanHost } from "./timeline-plan-mode";
//   // in connectedCallback (once):
//   this.unobserve = observePlanHost(host, () => this.renderPlan());
//   // in renderPlan():
//   this.planChart = renderPlanMode(host, hass, config, this.hours, this.planChart, echarts);
//
// Iteration-4 fixes: never draws at a guessed width (0 px host → wait for the observer), bars and
// caps are sized in axis units (no px barWidth), the chart is resized in place on width changes
// and only re-rendered when the layout switches between phone and desktop.

import { Hass, readPlan, startOfHour, timeFmt, numFmt, pick, resolvePx } from "./r3-util";
import { bucketize, forecastOption, railHtml, summaryHtml, PLAN_CSS, FORECAST_LABELS, LoadRef, ForecastColors } from "./forecast";
import { TOKENS } from "./tokens";

export interface PlanModeCfg {
  entities: { plan: string; price_forecast?: string };
  loads: LoadRef[];
  rail_width?: number;
  bucket?: "window" | "slot";
  currency?: string;
  labels?: Record<string, string>;
}

// Tokens scoped to the plan host so nothing leaks into (or collides with) the timeline card's own CSS.
const SCOPED = TOKENS.replace(":host", ".pp-plan") + `
  .pp-plan .skel { background: var(--pp-fill); border-radius: 6px; }`;
const COMPACT_BELOW = 600;

/** Re-render only when needed; plain resize otherwise. Returns an unobserve function. */
export function observePlanHost(host: HTMLElement, rerender: () => void): () => void {
  let lastW = 0;
  const ro = new ResizeObserver((entries) => {
    const w = Math.round(entries[0].contentRect.width);
    if (!w || w === lastW) return;
    const crossed = (w < COMPACT_BELOW) !== (lastW < COMPACT_BELOW) || !lastW;
    lastW = w;
    const chart = (host as any).__ppChart;
    if (crossed || !chart || host.dataset.ppWaiting) rerender();
    else chart.resize({ width: w, height: host.clientHeight || undefined });
  });
  ro.observe(host);
  return () => ro.disconnect();
}

export function renderPlanMode(host: HTMLElement, hass: Hass, cfg: PlanModeCfg, hours: number, chart: any, echarts: any): any {
  const W = Math.round(host.clientWidth);
  if (!W) { host.dataset.ppWaiting = "1"; return chart; }          // hidden tab / first paint: wait for the observer
  delete host.dataset.ppWaiting;
  const H = host.clientHeight || 440;
  const compact = W < COMPACT_BELOW;
  const rail = compact ? 0 : cfg.rail_width ?? 256;
  host.classList.add("pp-plan");
  let side = host.querySelector(":scope > .pp-side") as HTMLElement | null;
  let ec = host.querySelector(":scope > .pp-ec") as HTMLElement | null;
  if (!side) {
    host.insertAdjacentHTML("beforeend", `<style>${SCOPED}${PLAN_CSS}</style><div class="pp-side"></div><div class="pp-ec" style="position:absolute;inset:0"></div>`);
    side = host.querySelector(":scope > .pp-side") as HTMLElement;
    ec = host.querySelector(":scope > .pp-ec") as HTMLElement;
  }
  const planEnt = hass.states[cfg.entities.plan];
  if (!planEnt || planEnt.state === "unavailable" || !Array.isArray(planEnt.attributes?.slots)) {
    side.style.cssText = "position:absolute;inset:0;padding:16px;display:flex;flex-direction:column;gap:10px";
    side.innerHTML = `<span class="skel" style="width:120px;height:14px"></span><span class="skel" style="width:180px;height:32px"></span>
      <span class="skel" style="flex:1;border-radius:8px"></span>`;
    return chart;
  }
  const labels = { ...pick(FORECAST_LABELS, hass), ...(cfg.labels ?? {}) };
  const plan = readPlan(planEnt);
  const a0 = startOfHour().getTime();
  const prices = ((cfg.entities.price_forecast && hass.states[cfg.entities.price_forecast]?.attributes?.slots) || [])
    .map((s: any) => ({ s: Date.parse(s.start), e: Date.parse(s.end), p: Number(s.total), est: s.confidence !== "known" }))
    .filter((p: any) => Number.isFinite(p.p));
  const windowMin = cfg.bucket === "slot" ? 15 : plan.windowMin;
  const b = bucketize(plan.slots, windowMin, a0, hours, prices);
  const cs = getComputedStyle(host);
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d;
  const css: ForecastColors = {
    text: v("--pp-text", "#141414"), text2: v("--pp-text2", "#5e5e5e"), text3: v("--pp-text3", "#bdbdbd"), divider: v("--pp-divider", "rgba(0,0,0,.12)"),
    card: v("--pp-card", "#ffffff"), primary: v("--pp-primary", "#009ac7"), error: v("--pp-error", "#db4437"),
    base: v("--pp-base", "rgba(33,33,33,.34)"), hold: v("--pp-hold", "rgba(33,33,33,.16)"), cheap: v("--pp-cheap", "rgba(67,160,71,.1)"),
    warn: v("--pp-warn", "#ffa600"), priceHi: v("--pp-price-hi", "rgba(0,154,199,.5)"), priceLo: v("--pp-price-lo", "rgba(0,154,199,.24)"),
  };
  // Bars must be opaque, or the cheap-hours band shows through the translucent greys.
  css.base = solid(css.base, css.card);
  css.hold = solid(css.hold, css.card);
  const l = hass.locale?.language || hass.language || "en";
  const o = {
    now: Date.now(), a0, hours, windowMin, rail, compact, fs: resolvePx(host, "--ha-font-size-s", 12),
    tf: timeFmt(hass), nf: numFmt(hass, 2), nf1: numFmt(hass, 1), labels, css,
    currency: cfg.currency === "NOK" || !cfg.currency ? (l.startsWith("en") ? "NOK" : "kr") : cfg.currency,
  };
  side.style.cssText = compact ? "position:absolute;left:0;right:0;top:0;height:0" : `position:absolute;left:0;top:0;bottom:0;width:${rail}px`;
  side.innerHTML = compact ? summaryHtml(b, o) : railHtml(b, cfg.loads, o);
  const opt = forecastOption(b, cfg.loads, o);
  if (compact) opt.grid[0].top = 150;
  chart ??= echarts.init(ec!, undefined, { renderer: "svg" });
  chart.setOption(opt, { notMerge: true });
  chart.resize({ width: W, height: H });
  (host as any).__ppChart = chart;
  return chart;
}

/** Flattens an rgba() colour onto a background colour (hex or rgb()), for opaque bars. */
function solid(fg: string, bg: string): string {
  const parse = (c: string): number[] | null => {
    const h = c.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (h) {
      const x = h[1].length === 3 ? h[1].split("").map((d) => d + d).join("") : h[1];
      return [0, 2, 4].map((i) => parseInt(x.slice(i, i + 2), 16)).concat(1);
    }
    const m = c.match(/rgba?\(([^)]+)\)/i);
    if (!m) return null;
    const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1];
  };
  const f = parse(fg), b = parse(bg);
  if (!f || !b || f.some((v) => !Number.isFinite(v))) return fg;
  const a = f[3];
  const mix = [0, 1, 2].map((i) => Math.round(f[i] * a + b[i] * (1 - a)));
  return `rgb(${mix.join(",")})`;
}
