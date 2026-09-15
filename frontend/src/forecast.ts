// Plan card, mode "plan" (iteration 5): hourly forecast of the whole house.
//
//   Annet forbruk        slots[].baseline_kwh                 grey, not movable
//   Holder temperaturen  slots[].hold_kwh (sum of all loads)  one muted band, not moved
//   Flyttet i tid        slots[].planned_kwh[id]              appliance colour, the only coloured part
//   Kan bli opptil       baseline_p90 − baseline, as a dashed cap on each bar (not a hatched block)
//   Effektmål            ceiling_kwh per window, dashed line, labelled in the chart (not again in the rail)
//
// Iteration 5 ("one metric once"): no price track (Strømpris is right above; the green band marks the cheap
// hours and the tooltip gives the price), no split bar (the rail rows carry the same three numbers), no
// per-load list under "Flyttet i tid" (the bar colours + Apparater show it; it also widened the rail over
// the chart), no Effektmål row, no "p90". The cheap-hours share moved from a chip into the Flyttet row.
// Y axis uses 1/2/5 steps (no 3,75).
//
// Everything that depends on width is expressed in axis units (bar width 72 % of the slot,
// caps via api.size), so a resize never needs a re-render and bars can't turn into needles.

import type { PlanSlot } from "./r3-util";

export interface Bucket {
  start: number; end: number;
  baseline: number | null; p90: number | null;
  hold: number;                        // kWh, all loads
  moved: Record<string, number>;       // kWh per load id
  ceiling: number | null;
  price: number | null; estimated: boolean;
}

export interface LoadRef { id: string; name: string; color: string }

export function bucketize(
  slots: PlanSlot[], windowMin: number, a0: number, hours: number,
  prices: { s: number; e: number; p: number; est: boolean }[],
): Bucket[] {
  const win = windowMin * 60e3;
  const n = Math.round((hours * 3600e3) / win);
  const out: Bucket[] = Array.from({ length: n }, (_, i) => ({
    start: a0 + i * win, end: a0 + (i + 1) * win, baseline: null, p90: null, hold: 0, moved: {}, ceiling: null, price: null, estimated: false,
  }));
  for (const s of slots) {
    const i = Math.floor((s.start.getTime() - a0) / win);
    if (i < 0 || i >= n) continue;
    const b = out[i];
    if (s.baseline != null) b.baseline = (b.baseline ?? 0) + s.baseline;
    if (s.baselineP90 != null) b.p90 = (b.p90 ?? 0) + s.baselineP90;
    for (const v of Object.values(s.hold)) if (v > 0) b.hold += v;
    for (const [k, v] of Object.entries(s.planned)) if (v > 0) b.moved[k] = (b.moved[k] ?? 0) + v;
    if (s.ceiling != null) b.ceiling = s.ceiling;
  }
  for (const b of out) {
    const ps = prices.filter((p) => p.e > b.start && p.s < b.end);
    if (ps.length) {
      b.price = ps.reduce((t, p) => t + p.p, 0) / ps.length;
      b.estimated = ps.some((p) => p.est);
    }
  }
  return out;
}

const sum = (o: Record<string, number>) => Object.values(o).reduce((t, v) => t + v, 0);
export const capOf = (k: Bucket) => (k.p90 ?? k.baseline ?? 0) + k.hold + sum(k.moved);
export const totalOf = (k: Bucket) => (k.baseline ?? 0) + k.hold + sum(k.moved);

export interface ForecastColors {
  text: string; text2: string; text3: string; divider: string; card: string; primary: string; error: string;
  base: string; hold: string; holdLine: string; cheap: string; cheapLine: string; warn: string; priceHi: string; priceLo: string;
}

export interface ForecastOpts {
  now: number; a0: number; hours: number; windowMin: number;
  rail: number; compact: boolean; fs: number;           // fs = --ha-font-size-s in px
  tf: Intl.DateTimeFormat; nf: Intl.NumberFormat; nf1: Intl.NumberFormat;
  labels: Record<string, string>;
  css: ForecastColors;
  currency: string;
}

export function forecastOption(b: Bucket[], loads: LoadRef[], o: ForecastOpts): any {
  const L = o.labels;
  const step = o.windowMin / 60;
  const H = b.length * step;
  const x = (i: number) => (i + 0.5) * step;
  const left = o.compact ? 44 : o.rail + 52;
  const right = o.compact ? 12 : 16;
  const used = loads.filter((l) => b.some((k) => (k.moved[l.id] ?? 0) > 0));
  const ceiling = b.find((k) => k.ceiling != null)?.ceiling ?? null;
  const maxY = Math.max(ceiling ?? 0, ...b.map(capOf), 4);
  const yStep = niceStep((maxY * 1.08) / (o.compact ? 2 : 4));
  const ymax = Math.ceil((maxY * 1.08) / yStep) * yStep;
  const nf0 = new Intl.NumberFormat(o.tf.resolvedOptions().locale, { maximumFractionDigits: 1 });
  const nowX = (o.now - o.a0) / 3600e3;
  const tz = o.tf.resolvedOptions().timeZone;
  const locale = o.tf.resolvedOptions().locale;
  const hourOf = (t: number) => Number(new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone: tz }).format(t));
  const dayFmt = new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", timeZone: tz });
  const midnights = b.map((k, i) => ({ i, t: k.start })).filter(({ i, t }) => i > 0 && hourOf(t) === 0)
    .map(({ i, t }) => ({ x: i * step, label: dayFmt.format(t) }));
  const bar = { type: "bar", stack: "t", barWidth: "72%", xAxisIndex: 0, yAxisIndex: 0, z: 2, emphasis: { disabled: true } };
  const series: any[] = [];
  if (b.some((k) => k.baseline != null)) {
    series.push({ ...bar, id: "baseline", name: L.other_usage, data: b.map((k, i) => [x(i), k.baseline ?? 0]), itemStyle: { color: o.css.base } });
  }
  if (b.some((k) => k.hold > 0.005)) {
    // hollow: a faint fill with a clear outline - visible on dark and light cards, and reads as "held", not "moved"
    series.push({ ...bar, id: "hold", name: L.holding, data: b.map((k, i) => [x(i), k.hold]),
      itemStyle: { color: o.css.hold, borderColor: o.css.holdLine, borderWidth: 1.25 } });
  }
  for (const l of used) {
    series.push({ ...bar, id: l.id, name: l.name, data: b.map((k, i) => [x(i), k.moved[l.id] ?? 0]), itemStyle: { color: l.color } });
  }
  // "Kan bli opptil": dashed cap + dotted whisker, sized in axis units so resize keeps them right.
  if (b.some((k) => k.p90 != null)) {
    series.push({
      type: "custom", id: "cap", name: L.reserve, xAxisIndex: 0, yAxisIndex: 0, silent: true, z: 3,
      data: b.map((k, i) => [x(i), capOf(k), totalOf(k)]),
      renderItem: (_p: any, api: any) => {
        const [cx, cy] = api.coord([api.value(0), api.value(1)]);
        const [, ty] = api.coord([api.value(0), api.value(2)]);
        const w = api.size([step, 0])[0] * 0.72;
        if (!Number.isFinite(cx) || cy >= ty - 1) return null;
        return {
          type: "group", children: [
            { type: "line", shape: { x1: cx - w / 2, y1: cy, x2: cx + w / 2, y2: cy }, style: { stroke: o.css.text2, lineWidth: 1.5, lineDash: [3, 2] } },
            { type: "line", shape: { x1: cx, y1: cy, x2: cx, y2: ty }, style: { stroke: o.css.text3, lineWidth: 1, lineDash: [1, 3] } },
          ],
        };
      },
    });
  }
  // limit, now, midnights, cheap hours
  series.push({
    type: "line", id: "marks", xAxisIndex: 0, yAxisIndex: 0, data: [], silent: true, z: 1,   // under the bars (z 2)
    markLine: {
      symbol: "none", silent: true, animation: false,
      data: [
        ...(ceiling != null ? [{ yAxis: ceiling, lineStyle: { color: o.css.error, width: 1.5, type: [6, 4] },
          label: { position: "insideEndTop", formatter: L.limit_value_h.replace("{kw}", nf0.format(ceiling)), color: o.css.text2, fontSize: o.fs,
            backgroundColor: o.css.card, padding: [1, 4], borderRadius: 4 } }] : []),   // stays readable where a tall bar reaches the end
        { xAxis: nowX, lineStyle: { color: o.css.text, width: 1.5, type: "solid" },
          label: { position: "end", formatter: L.now, color: o.css.card, backgroundColor: o.css.text, borderRadius: 10, padding: [3, 8], fontSize: o.fs, fontWeight: 500 } },
        ...midnights.map((m) => ({ xAxis: m.x, lineStyle: { color: o.css.divider, width: 1, type: "solid" },
          label: { show: Math.abs(m.x - nowX) > 1.5 * (o.hours / 24), position: "end", formatter: m.label, color: o.css.text2, fontSize: o.fs } })),
      ],
    },
    markArea: { silent: true, itemStyle: { color: o.css.cheap }, data: cheapAreas(b, step) },
  });
  // Cheap hours also get a 3 px line along the top of the plot: the tinted column alone is too faint on dark.
  const cheapRuns = cheapAreas(b, step).map((a: any) => [a[0].xAxis, a[1].xAxis]);
  if (cheapRuns.length) series.push({
    type: "custom", id: "cheapline", xAxisIndex: 0, yAxisIndex: 0, silent: true, z: 1, data: cheapRuns,
    renderItem: (p: any, api: any) => {
      const [x0] = api.coord([api.value(0), 0]), [x1] = api.coord([api.value(1), 0]);
      return { type: "rect", shape: { x: x0, y: p.coordSys.y, width: x1 - x0, height: 3, r: 1.5 }, style: { fill: o.css.cheapLine } };
    },
  });

  const tickEvery = o.hours >= 48 || o.compact ? 6 : 3;
  const axisLabel = {
    color: o.css.text2, fontSize: o.fs, interval: 0,
    formatter: (v: number) => {
      const t = o.a0 + v * 3600e3;
      return hourOf(t) % tickEvery === 0 && Math.abs(v - nowX) > 0.6 && v > 0.3 && v < H - 0.3 ? o.tf.format(t) : "";
    },
  };
  return {
    animation: false,
    textStyle: { fontFamily: "Roboto, Noto, sans-serif" },
    grid: [{ left, right, top: o.compact ? 40 : 60, bottom: 28 }],
    xAxis: [
      { type: "value", min: 0, max: H, interval: step, axisLine: { lineStyle: { color: o.css.divider } }, axisTick: { show: false }, splitLine: { show: false }, axisLabel },
    ],
    yAxis: [
      { type: "value", min: 0, max: ymax, interval: yStep, name: o.compact ? "" : L.unit_kwh_h,   // phone: the unit is in the Effektmål label
        nameTextStyle: { color: o.css.text2, fontSize: o.fs, align: "right", padding: [0, 6, 6, 0] },
        axisLabel: { color: o.css.text2, fontSize: o.fs, formatter: (v: number) => nf0.format(v) },
        splitLine: { lineStyle: { color: o.css.divider, type: [2, 4] } } },
    ],
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: o.css.cheap } },
      backgroundColor: o.css.card, borderColor: o.css.divider, textStyle: { color: o.css.text, fontSize: o.fs + 2 },
      extraCssText: "border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.3);",
      formatter: (items: any[]) => tooltip(b, loads, items, o),
      // never over the rail: flip to the left of the pointer only while that stays inside the plot
      position: (pt: number[], _p: any, _d: any, _r: any, size: { contentSize: number[]; viewSize: number[] }) => {
        const [w, h] = size.contentSize, [vw, vh] = size.viewSize;
        let x = pt[0] + 16;
        if (x + w > vw - 4) x = Math.max(left, pt[0] - w - 16);
        return [x, Math.max(4, Math.min(pt[1] - h / 2, vh - h - 4))];
      },
    },
    series,
  };
}

function cheapAreas(b: Bucket[], step: number): any[] {
  const ps = b.map((k) => k.price).filter((p): p is number => p != null);
  if (!ps.length) return [];
  const lo = Math.min(...ps), hi = Math.max(...ps);
  if (hi - lo < 1e-4) return [];
  const thr = lo + (hi - lo) * 0.25;
  const out: any[] = [];
  let s: number | null = null;
  b.forEach((k, i) => {
    const on = k.price != null && k.price <= thr;
    if (on && s === null) s = i;
    if ((!on || i === b.length - 1) && s !== null) {
      out.push([{ xAxis: s * step }, { xAxis: (on ? i + 1 : i) * step }]);
      s = null;
    }
  });
  return out;
}

/** 1, 2 or 5 × 10^n: the smallest of those ≥ raw. Keeps the ticks whole (5 · 10 · 15 · 20, never 3,75). */
export function niceStep(raw: number): number {
  const e = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-6))));
  const f = raw / e;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
}

function tooltip(b: Bucket[], loads: LoadRef[], items: any[], o: ForecastOpts): string {
  const it = items.find((x) => x.seriesId === "baseline") ?? items[0];
  if (!it) return "";
  const k = b[Math.floor(it.value[0] / (o.windowMin / 60))];
  if (!k) return "";
  const L = o.labels, nf = o.nf;
  const moved = sum(k.moved);
  const row = (c: string, name: string, v: number) =>
    `<div style="display:flex;gap:8px;align-items:center"><span style="width:10px;height:10px;border-radius:2px;background:${c}"></span><span style="flex:1">${name}</span><span>${nf.format(v)}</span></div>`;
  const per = loads.filter((l) => (k.moved[l.id] ?? 0) > 0.005).map((l) => `${l.name.replace(/^Gulvvarme /, "")} ${nf.format(k.moved[l.id])}`).join(" · ");
  const head = `${o.tf.format(k.start)}–${o.tf.format(k.end)}${k.price != null ? ` · ${nf.format(k.price)} ${o.currency}/kWh${k.estimated ? " · " + L.estimated_short : ""}` : ""}`;
  return `<div style="min-width:240px;line-height:24px"><div style="font-weight:500">${head}</div>
    ${k.baseline != null ? row(o.css.base, L.other_usage, k.baseline) : ""}
    ${k.hold > 0.005 ? row(`${o.css.hold};box-shadow:inset 0 0 0 1.5px ${o.css.holdLine}`, L.holding, k.hold) : ""}
    ${moved > 0.005 ? row(loads.find((l) => (k.moved[l.id] ?? 0) > 0)?.color ?? o.css.primary, L.moved_here, moved) + `<div style="color:${o.css.text2};font-size:${o.fs}px;line-height:16px;padding-left:18px">${per}</div>` : ""}
    <div style="border-top:1px solid ${o.css.divider};margin-top:6px;padding-top:4px;display:flex;justify-content:space-between;gap:16px;font-weight:500">
      <span>${L.total}</span><span>${nf.format(totalOf(k))} kWh${k.p90 != null ? ` · ${L.can_reach} ${o.nf1.format(capOf(k))}` : ""}</span></div></div>`;
}

/** Left rail: total, then one row per part (these rows ARE the legend), the cap key. Nothing else. */
export function railHtml(b: Bucket[], loads: LoadRef[], o: ForecastOpts): string {
  const L = o.labels, nf1 = o.nf1;
  const other = b.reduce((t, k) => t + (k.baseline ?? 0), 0);
  const hold = b.reduce((t, k) => t + k.hold, 0);
  const per = loads.map((l) => ({ l, v: b.reduce((t, k) => t + (k.moved[l.id] ?? 0), 0) })).filter((x) => x.v > 0.005);
  const moved = per.reduce((t, x) => t + x.v, 0);
  const cheap = cheapShare(b);
  const row = (sw: string, name: string, val: string, sub = "") =>
    `<div class="lr"><span class="sw" style="${sw}"></span><span class="n">${name}${sub ? `<small>${sub}</small>` : ""}</span><span class="v">${val}</span></div>`;
  return `<div class="rail">
    <span class="k">${L.next_hours.replace("{h}", String(o.hours))}</span>
    <div class="tot"><span>${nf1.format(other + hold + moved)}</span><small>${L.kwh_expected}</small></div>
    ${other > 0 ? row(`background:${o.css.base}`, L.other_usage, nf1.format(other)) : ""}
    ${hold > 0.005 ? row(`background:${o.css.hold};box-shadow:inset 0 0 0 1.5px ${o.css.holdLine}`, L.holding, nf1.format(hold)) : ""}
    ${moved > 0.005 ? row(`background:${stripes(per.map((x) => x.l.color))}`, L.moved, nf1.format(moved),
      cheap != null ? L.cheap_share.replace("{pct}", String(cheap)) : "") : ""}
    ${b.some((k) => k.p90 != null) ? `<div class="hr"></div>${row(`height:0;border-top:2px dashed ${o.css.text2};border-radius:0`, L.can_reach_key, "")}` : ""}
  </div>`;
}

/** A swatch that shows every appliance colour in "Flyttet i tid" instead of pretending it is one colour. */
function stripes(colors: string[]): string {
  if (colors.length <= 1) return colors[0] ?? "var(--pp-primary)";
  const w = 100 / colors.length;
  return `linear-gradient(90deg,${colors.map((c, i) => `${c} ${(i * w).toFixed(1)}% ${((i + 1) * w).toFixed(1)}%`).join(",")})`;
}

/** Share of MOVED energy (not holding) that lands in the cheapest quarter of the window. */
export function cheapShare(b: Bucket[]): number | null {
  const ps = b.map((k) => k.price).filter((p): p is number => p != null);
  const moved = b.reduce((t, k) => t + sum(k.moved), 0);
  if (!ps.length || moved < 0.01) return null;
  const lo = Math.min(...ps), hi = Math.max(...ps);
  if (hi - lo < 1e-4) return null;
  const thr = lo + (hi - lo) * 0.25;
  const inCheap = b.reduce((t, k) => t + ((k.price ?? Infinity) <= thr ? sum(k.moved) : 0), 0);
  return Math.round((inCheap / moved) * 100);
}

/** Phone (< 600 px): no rail; total and one line of keys above the chart. */
export function summaryHtml(b: Bucket[], o: ForecastOpts): string {
  const L = o.labels, nf1 = o.nf1;
  const other = b.reduce((t, k) => t + (k.baseline ?? 0), 0);
  const hold = b.reduce((t, k) => t + k.hold, 0);
  const moved = b.reduce((t, k) => t + sum(k.moved), 0);
  const chip = (c: string, t: string, v: string) => `<span class="chip"><i style="background:${c}"></i>${t} ${v}</span>`;
  return `<div class="sum">
    <span class="k">${L.next_hours.replace("{h}", String(o.hours))}</span>
    <div class="tot"><span>${nf1.format(other + hold + moved)}</span><small>kWh</small></div>
    <div class="chips">${chip(o.css.base, L.other_short, nf1.format(other))}${hold > 0.005 ? chip(`${o.css.hold};box-shadow:inset 0 0 0 1.5px ${o.css.holdLine}`, L.holding_short, nf1.format(hold)) : ""}${moved > 0.005 ? chip(o.css.primary, L.moved, nf1.format(moved)) : ""}</div>
  </div>`;
}

export const PLAN_CSS = `
  .pp-plan .rail { position: absolute; inset: 0; box-sizing: border-box; padding: 16px; border-right: 1px solid var(--pp-divider);
          display: flex; flex-direction: column; gap: 2px; font-size: var(--pp-fs-m); overflow: hidden; }   /* inset: the rail can never outgrow its column */
  .pp-plan .rail .k { color: var(--pp-text2); }
  .pp-plan .rail .tot { display: flex; align-items: baseline; gap: 6px; }
  .pp-plan .rail .tot span { font-size: var(--pp-fs-4xl); letter-spacing: -.4px; line-height: 40px; } .pp-plan .rail .tot small { color: var(--pp-text2); }
  .pp-plan .rail .tot { margin-bottom: 8px; }
  .pp-plan .rail .lr { display: flex; align-items: center; gap: 10px; min-height: 28px; }
  .pp-plan .rail .sw { width: 12px; height: 12px; border-radius: 3px; flex: none; box-sizing: border-box; }
  .pp-plan .rail .n { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pp-plan .rail .n small { font-size: var(--pp-fs-s); color: var(--pp-ok-text); line-height: 16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pp-plan .rail .v { font-variant-numeric: tabular-nums; }
  .pp-plan .rail .hr { height: 1px; background: var(--pp-divider); margin: 6px 0; }
  .pp-plan .sum { position: absolute; left: 12px; right: 12px; top: 12px; font-size: var(--pp-fs-m); }
  .pp-plan .sum .k { color: var(--pp-text2); }
  .pp-plan .sum .tot { display: flex; align-items: baseline; gap: 6px; } .pp-plan .sum .tot span { font-size: var(--pp-fs-3xl); } .pp-plan .sum .tot small { color: var(--pp-text2); }
  .pp-plan .sum .chips { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 4px; font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .pp-plan .sum .chip { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  .pp-plan .sum .chip i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
`;

export const FORECAST_LABELS: Record<string, Record<string, string>> = {
  nb: {
    other_usage: "Annet forbruk", other_short: "Annet", holding: "Holder temperaturen", holding_short: "Holder", moved: "Flyttet i tid",
    moved_here: "Flyttet hit", reserve: "Kan bli opptil", can_reach: "kan bli", can_reach_key: "Kan bli opptil",
    limit_value_h: "Effektmål {kw} kWh/t", now: "Nå", unit_kwh_h: "kWh/t", total: "Sum",
    estimated_short: "anslått", next_hours: "Neste {h} timer", kwh_expected: "kWh forventet",
    cheap_share: "{pct} % i billige timer",
  },
  en: {
    other_usage: "Other usage", other_short: "Other", holding: "Holding temperature", holding_short: "Holding", moved: "Moved in time",
    moved_here: "Moved here", reserve: "Could reach", can_reach: "could reach", can_reach_key: "Could reach",
    limit_value_h: "Limit {kw} kWh/h", now: "Now", unit_kwh_h: "kWh/h", total: "Total",
    estimated_short: "estimated", next_hours: "Next {h} hours", kwh_expected: "kWh expected",
    cheap_share: "{pct} % in cheap hours",
  },
};
