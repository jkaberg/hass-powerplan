// Plan card, mode "plan" (iteration 4): hourly forecast of the whole house.
//
//   Annet forbruk        slots[].baseline_kwh                 grey, not movable
//   Holder temperaturen  slots[].hold_kwh (sum of all loads)  one muted band, not moved
//   Flyttet i tid        slots[].planned_kwh[id]              appliance colour, the only coloured part
//   Kan bli opptil       baseline_p90 − baseline, as a dashed cap on each bar (not a hatched block)
//   Effektmål            ceiling_kwh per window, dashed line
//   Price track          one block per price level; dashed outline while prices are estimated
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
  base: string; hold: string; cheap: string; warn: string; priceHi: string; priceLo: string;
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
  const maxY = Math.max(ceiling ?? 0, ...b.map(capOf));
  const ymax = Math.max(6, Math.ceil((maxY * 1.08) / 3) * 3);
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
    series.push({ ...bar, id: "hold", name: L.holding, data: b.map((k, i) => [x(i), k.hold]), itemStyle: { color: o.css.hold } });
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
          label: { position: "insideEndTop", formatter: L.limit_value_h.replace("{kw}", nf0.format(ceiling)), color: o.css.text2, fontSize: o.fs } }] : []),
        { xAxis: nowX, lineStyle: { color: o.css.text, width: 1.5, type: "solid" },
          label: { position: "end", formatter: L.now, color: o.css.card, backgroundColor: o.css.text, borderRadius: 10, padding: [3, 8], fontSize: o.fs, fontWeight: 500 } },
        ...midnights.map((m) => ({ xAxis: m.x, lineStyle: { color: o.css.divider, width: 1, type: "solid" },
          label: { show: Math.abs(m.x - nowX) > 1.5 * (o.hours / 24), position: "end", formatter: m.label, color: o.css.text2, fontSize: o.fs } })),
      ],
    },
    markArea: { silent: true, itemStyle: { color: o.css.cheap }, data: cheapAreas(b, step) },
  });
  // price track (grid 1): blocks per price level
  const segs = priceSegments(b, step);
  series.push({
    type: "custom", id: "price", name: L.price_strip, xAxisIndex: 1, yAxisIndex: 1, silent: true,
    data: segs.map((s) => [s.x0, s.x1, s.p, s.est ? 1 : 0]),
    renderItem: (p: any, api: any) => {
      const [x0] = api.coord([api.value(0), 0]);
      const [x1] = api.coord([api.value(1), 0]);
      const cs = p.coordSys;
      const hi = segs.length ? Math.max(...segs.map((s) => s.p)) : 0;
      const est = api.value(3) === 1;
      const w = x1 - x0 - 2;
      const children: any[] = [{
        type: "rect", shape: { x: x0 + 1, y: cs.y, width: w, height: cs.height, r: 4 },
        style: { fill: api.value(2) >= hi - 1e-4 ? o.css.priceHi : o.css.priceLo, stroke: est ? o.css.warn : "none", lineWidth: 1, lineDash: est ? [3, 3] : undefined },
      }];
      if (w > 44) children.push({
        type: "text", x: x0 + 1 + w / 2, y: cs.y + cs.height / 2,
        style: { text: o.nf.format(api.value(2)) + (est && api.value(0) === segs.find((s) => s.est)?.x0 && w > 120 ? " · " + L.estimated_short : ""),
          fill: o.css.text, font: `500 ${o.fs}px sans-serif`, align: "center", verticalAlign: "middle" },
      });
      return { type: "group", children };
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
    grid: [
      { left, right, top: o.compact ? 40 : 76, bottom: o.compact ? 62 : 64 },
      { left, right, height: 20, bottom: o.compact ? 34 : 36 },
    ],
    xAxis: [
      { type: "value", min: 0, max: H, interval: step, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false } },
      { type: "value", gridIndex: 1, min: 0, max: H, interval: step, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel },
    ],
    yAxis: [
      { type: "value", min: 0, max: ymax, interval: o.compact ? ymax / 2 : ymax / 4, name: L.unit_kwh_h,
        nameTextStyle: { color: o.css.text2, fontSize: o.fs, align: "right", padding: [0, 6, 6, 0] },
        axisLabel: { color: o.css.text2, fontSize: o.fs, formatter: (v: number) => nf0.format(v) },
        splitLine: { lineStyle: { color: o.css.divider, type: [2, 4] } } },
      { type: "value", gridIndex: 1, min: 0, max: 1, show: false },
    ],
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: o.css.cheap } },
      backgroundColor: o.css.card, borderColor: o.css.divider, textStyle: { color: o.css.text, fontSize: o.fs + 2 },
      extraCssText: "border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.3);",
      formatter: (items: any[]) => tooltip(b, loads, items, o),
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

function priceSegments(b: Bucket[], step: number): { x0: number; x1: number; p: number; est: boolean }[] {
  const out: { x0: number; x1: number; p: number; est: boolean }[] = [];
  b.forEach((k, i) => {
    if (k.price == null) return;
    const last = out[out.length - 1];
    if (last && Math.abs(last.p - k.price) < 1e-4 && Math.abs(last.x1 - i * step) < 1e-9) { last.x1 = (i + 1) * step; last.est = last.est || k.estimated; }
    else out.push({ x0: i * step, x1: (i + 1) * step, p: k.price, est: k.estimated });
  });
  return out;
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
    ${k.hold > 0.005 ? row(o.css.hold, L.holding, k.hold) : ""}
    ${moved > 0.005 ? row(loads.find((l) => (k.moved[l.id] ?? 0) > 0)?.color ?? o.css.primary, L.moved_here, moved) + `<div style="color:${o.css.text2};font-size:${o.fs}px;line-height:16px;padding-left:18px">${per}</div>` : ""}
    <div style="border-top:1px solid ${o.css.divider};margin-top:6px;padding-top:4px;display:flex;justify-content:space-between;gap:16px;font-weight:500">
      <span>${L.total}</span><span>${nf.format(totalOf(k))} kWh${k.p90 != null ? ` · ${L.can_reach} ${o.nf1.format(capOf(k))}` : ""}</span></div></div>`;
}

/** Left rail: total, split bar, three legend groups, cap/limit keys, share of moved energy in cheap hours. */
export function railHtml(b: Bucket[], loads: LoadRef[], o: ForecastOpts): string {
  const L = o.labels, nf = o.nf, nf1 = o.nf1;
  const other = b.reduce((t, k) => t + (k.baseline ?? 0), 0);
  const hold = b.reduce((t, k) => t + k.hold, 0);
  const per = loads.map((l) => ({ l, v: b.reduce((t, k) => t + (k.moved[l.id] ?? 0), 0) })).filter((x) => x.v > 0.005);
  const moved = per.reduce((t, x) => t + x.v, 0);
  const tot = other + hold + moved;
  const cheap = cheapShare(b);
  const seg = (w: number, c: string) => (w > 0 ? `<div style="flex:${w.toFixed(4)} 1 0;min-width:2px;background:${c}"></div>` : "");
  const row = (sw: string, name: string, val: string, sub = "") =>
    `<div class="lr"><span class="sw" style="${sw}"></span><span class="n">${name}${sub ? `<small>${sub}</small>` : ""}</span><span class="v">${val}</span></div>`;
  const ceiling = b.find((k) => k.ceiling != null)?.ceiling;
  return `<div class="rail">
    <span class="k">${L.next_hours.replace("{h}", String(o.hours))}</span>
    <div class="tot"><span>${nf1.format(tot)}</span><small>${L.kwh_expected}</small></div>
    <div class="split">${seg(other, o.css.base)}${seg(hold, o.css.hold)}${per.map((x) => seg(x.v, x.l.color)).join("")}</div>
    ${other > 0 ? row(`background:${o.css.base}`, L.other_usage, nf1.format(other)) : ""}
    ${hold > 0.005 ? row(`background:${o.css.hold}`, L.holding, nf1.format(hold)) : ""}
    ${moved > 0.005 ? row(`background:${per[0].l.color}`, L.moved, nf.format(moved), per.map((x) => x.l.name.replace(/^Gulvvarme /, "")).join(", ")) : ""}
    <div class="hr"></div>
    ${b.some((k) => k.p90 != null) ? row(`height:0;border-top:2px dashed ${o.css.text2};border-radius:0`, L.can_reach_key, "p90") : ""}
    ${ceiling != null ? row(`height:0;border-top:2px dashed ${o.css.error};border-radius:0`, L.limit, new Intl.NumberFormat(o.tf.resolvedOptions().locale, { maximumFractionDigits: 1 }).format(ceiling) + " " + L.unit_kwh_h) : ""}
    <span class="grow"></span>
    ${cheap != null ? `<div class="ok"><ha-icon icon="mdi:check-circle-outline"></ha-icon><span>${L.cheap_share.replace("{pct}", String(cheap))}</span></div>` : ""}
  </div>`;
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

/** Phone (< 600 px): no rail; total, split bar and three chips above the chart. */
export function summaryHtml(b: Bucket[], o: ForecastOpts): string {
  const L = o.labels, nf1 = o.nf1, nf = o.nf;
  const other = b.reduce((t, k) => t + (k.baseline ?? 0), 0);
  const hold = b.reduce((t, k) => t + k.hold, 0);
  const moved = b.reduce((t, k) => t + sum(k.moved), 0);
  const seg = (w: number, c: string) => (w > 0 ? `<div style="flex:${w.toFixed(4)} 1 0;min-width:2px;background:${c}"></div>` : "");
  const chip = (c: string, t: string, v: string) => `<span class="chip"><i style="background:${c}"></i>${t} ${v}</span>`;
  return `<div class="sum">
    <span class="k">${L.next_hours.replace("{h}", String(o.hours))}</span>
    <div class="tot"><span>${nf1.format(other + hold + moved)}</span><small>kWh</small></div>
    <div class="split">${seg(other, o.css.base)}${seg(hold, o.css.hold)}${seg(moved, o.css.primary)}</div>
    <div class="chips">${chip(o.css.base, L.other_short, nf1.format(other))}${hold > 0.005 ? chip(o.css.hold, L.holding_short, nf1.format(hold)) : ""}${moved > 0.005 ? chip(o.css.primary, L.moved, nf.format(moved)) : ""}</div>
  </div>`;
}

export const PLAN_CSS = `
  .pp-plan .rail { position: absolute; left: 0; top: 0; bottom: 0; box-sizing: border-box; padding: 16px; border-right: 1px solid var(--pp-divider);
          display: flex; flex-direction: column; gap: 2px; font-size: var(--pp-fs-m); }
  .pp-plan .rail .k { color: var(--pp-text2); }
  .pp-plan .rail .tot { display: flex; align-items: baseline; gap: 6px; }
  .pp-plan .rail .tot span { font-size: var(--pp-fs-4xl); letter-spacing: -.4px; line-height: 40px; } .pp-plan .rail .tot small { color: var(--pp-text2); }
  .pp-plan .rail .split, .pp-plan .sum .split { display: flex; height: 8px; border-radius: 4px; overflow: hidden; gap: 1px; margin: 8px 0 10px; }
  .pp-plan .rail .lr { display: flex; align-items: center; gap: 10px; min-height: 28px; }
  .pp-plan .rail .sw { width: 12px; height: 12px; border-radius: 3px; flex: none; box-sizing: border-box; }
  .pp-plan .rail .n { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; }
  .pp-plan .rail .n small { font-size: var(--pp-fs-s); color: var(--pp-text2); line-height: 16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pp-plan .rail .v { font-variant-numeric: tabular-nums; }
  .pp-plan .rail .hr { height: 1px; background: var(--pp-divider); margin: 6px 0; }
  .pp-plan .rail .grow { flex: 1 1 auto; }
  .pp-plan .rail .ok { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: 10px; background: var(--pp-ok-bg);
              line-height: 20px; --mdc-icon-size: 18px; }
  .pp-plan .rail .ok ha-icon { color: var(--pp-ok); flex: none; }
  .pp-plan .sum { position: absolute; left: 12px; right: 12px; top: 12px; font-size: var(--pp-fs-m); }
  .pp-plan .sum .k { color: var(--pp-text2); }
  .pp-plan .sum .tot { display: flex; align-items: baseline; gap: 6px; } .pp-plan .sum .tot span { font-size: var(--pp-fs-3xl); } .pp-plan .sum .tot small { color: var(--pp-text2); }
  .pp-plan .sum .chips { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .pp-plan .sum .chip { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  .pp-plan .sum .chip i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
`;

export const FORECAST_LABELS: Record<string, Record<string, string>> = {
  nb: {
    other_usage: "Annet forbruk", other_short: "Annet", holding: "Holder temperaturen", holding_short: "Holder", moved: "Flyttet i tid",
    moved_here: "Flyttet hit", reserve: "Kan bli opptil", can_reach: "kan bli", can_reach_key: "Kan bli opptil", limit: "Effektmål",
    limit_value_h: "Effektmål {kw} kWh/t", now: "Nå", price_strip: "Pris", unit_kwh_h: "kWh/t", total: "Sum",
    estimated_short: "anslått", next_hours: "Neste {h} timer", kwh_expected: "kWh forventet",
    cheap_share: "{pct} % av flyttet forbruk ligger i billige timer",
  },
  en: {
    other_usage: "Other usage", other_short: "Other", holding: "Holding temperature", holding_short: "Holding", moved: "Moved in time",
    moved_here: "Moved here", reserve: "Could reach", can_reach: "could reach", can_reach_key: "Could reach", limit: "Limit",
    limit_value_h: "Limit {kw} kWh/h", now: "Now", price_strip: "Price", unit_kwh_h: "kWh/h", total: "Total",
    estimated_short: "estimated", next_hours: "Next {h} hours", kwh_expected: "kWh expected",
    cheap_share: "{pct} % of moved use is in cheap hours",
  },
};
