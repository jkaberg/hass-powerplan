// The Plan card's whole-house forecast (D12 §5.12 F1–F6): the next 24 or 48 h
// per capacity window, so bars and the limit share one unit (kWh per window).
//   grey    the rest of the house (`slots[].baseline_kwh`) - not movable
//   colour  each managed load (`slots[].planned_kwh[id]`) - movable
//   hatch   the reserve, `baseline_p90_kwh − baseline_kwh`
//   dashed  the limit (`ceiling_kwh`, per window already)
//   below   the price, one step line with each level's price printed once
// A rail on the left names the totals (its width equals the appliances card's,
// so the two time axes line up); under 600 px a summary sits above instead.

import { type Bucket, forecastTotals } from "./transforms";

export interface ForecastLoad {
  id: string;
  name: string;
  color: string;
}

export interface ForecastOptions {
  now: number;
  from: number;
  hours: number;
  windowMin: number;
  width: number;
  height: number;
  rail: number;
  compact: boolean;
  zone?: string;
  locale: string;
  currency: string;
  labels: Record<string, string>;
  css: { text: string; text2: string; divider: string; primary: string; error: string; card: string };
  tooltip: Record<string, unknown>;
}

const escape = (value: unknown) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

const fill = (text: string | undefined, values: Record<string, string | number>) =>
  (text ?? "").replace(/\{(\w+)\}/g, (whole, key: string) => (values[key] === undefined ? whole : String(values[key])));

/** The ECharts option: the stack on one axis, the limit, "Nå", midnights, the price strip under it (F1, F2, F4, F5). */
export function forecastOption(b: readonly Bucket[], loads: readonly ForecastLoad[], o: ForecastOptions): Record<string, unknown> {
  const labels = o.labels;
  const clock = new Intl.DateTimeFormat(o.locale, { hour: "2-digit", minute: "2-digit", timeZone: o.zone });
  const two = new Intl.NumberFormat(o.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const upToOne = new Intl.NumberFormat(o.locale, { maximumFractionDigits: 1 });
  const step = o.windowMin / 60;
  const H = b.length * step;
  const x = (i: number) => (i + 0.5) * step;
  const left = o.compact ? 34 : o.rail + 8;
  const right = o.compact ? 12 : 16;
  const plotW = o.width - left - right;
  const barW = Math.max(plotW / b.length - (plotW / b.length > 14 ? 3 : 1.5), 2);
  const used = loads.filter((l) => b.some((k) => (k.loads[l.id] ?? 0) > 0));
  const ceiling = b.find((k) => k.ceiling !== null)?.ceiling ?? null;
  const maxY = Math.max(ceiling ?? 0, ...b.map((k) => (k.p90 ?? k.baseline ?? 0) + Object.values(k.loads).reduce((s, v) => s + v, 0)));
  const ymax = Math.max(4, Math.ceil((maxY * 1.1) / 3) * 3);
  const nowX = (o.now - o.from) / 3_600_000;
  const prices = b.map((k) => k.price).filter((p): p is number => p !== null);
  const pmin = prices.length ? Math.min(...prices) : 0;
  const pmax = prices.length ? Math.max(...prices) : 1;
  const span = Math.max(pmax - pmin, 0.05);
  const estIdx = b.findIndex((k) => k.estimated);
  const hourOf = (t: number) => Number(new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone: o.zone }).format(t));
  const dayFmt = new Intl.DateTimeFormat(o.locale, { weekday: "short", day: "numeric", timeZone: o.zone });
  const midnights = b
    .map((k, i) => ({ i, t: k.start }))
    .filter(({ i, t }) => i > 0 && hourOf(t) === 0 && new Date(t).getUTCMinutes() === new Date(o.from).getUTCMinutes())
    .map(({ i, t }) => ({ x: i * step, label: dayFmt.format(t) }));
  const base = { type: "bar", stack: "t", barWidth: barW, xAxisIndex: 0, yAxisIndex: 0, emphasis: { disabled: true } };
  const series: Record<string, unknown>[] = [];
  if (b.some((k) => k.baseline !== null)) {
    series.push({ ...base, name: labels.other_usage, id: "baseline", data: b.map((k, i) => [x(i), k.baseline ?? 0]), itemStyle: { color: "rgba(155,155,155,0.34)" } });
  }
  for (const load of used) {
    series.push({ ...base, name: load.name, id: load.id, data: b.map((k, i) => [x(i), k.loads[load.id] ?? 0]), itemStyle: { color: load.color } });
  }
  if (b.some((k) => k.p90 !== null)) {
    series.push({
      ...base,
      name: labels.reserve,
      id: "reserve",
      data: b.map((k, i) => [x(i), Math.max((k.p90 ?? 0) - (k.baseline ?? 0), 0)]),
      itemStyle: {
        color: "rgba(0,0,0,0)",
        borderColor: "rgba(225,225,225,0.28)",
        borderWidth: 1,
        borderType: [2, 2],
        borderRadius: 2,
        decal: { symbol: "rect", symbolSize: 1, dashArrayX: [1, 0], dashArrayY: [2, 4], rotation: -Math.PI / 4, color: "rgba(225,225,225,0.28)" },
      },
    });
  }
  series.push({
    type: "line",
    name: labels.limit,
    id: "limit",
    xAxisIndex: 0,
    yAxisIndex: 0,
    data: [],
    silent: true,
    markLine: {
      symbol: "none",
      silent: true,
      animation: false,
      data: [
        ...(ceiling !== null
          ? [
              {
                yAxis: ceiling,
                lineStyle: { color: o.css.error, width: 1.5, type: [6, 4] },
                label: { position: "insideEndTop", formatter: fill(labels.limit_value_kwh, { kw: upToOne.format(ceiling) }), color: o.css.text2, fontSize: 11 },
              },
            ]
          : []),
        {
          xAxis: nowX,
          lineStyle: { color: o.css.text, width: 1.5, type: "solid" },
          label: { position: "end", formatter: labels.now ?? "", color: o.css.card, backgroundColor: o.css.text, borderRadius: 8, padding: [2, 7], fontSize: 10, fontWeight: 500 },
        },
        ...midnights.map((m) => ({
          xAxis: m.x,
          lineStyle: { color: "rgba(225,225,225,0.14)", width: 1, type: "solid" },
          label: { show: Math.abs(m.x - nowX) > 1.5 * (o.hours / 24), position: "end", formatter: m.label, color: o.css.text2, fontSize: 11 },
        })),
      ],
    },
    markArea:
      estIdx >= 0
        ? {
            silent: true,
            itemStyle: { color: "rgba(225,225,225,0.025)" },
            label: { position: "insideTopLeft", color: o.css.text2, fontSize: 11, offset: [6, 4] },
            data: [[{ name: labels.estimated_prices ?? "", xAxis: estIdx * step }, { xAxis: H }]],
          }
        : undefined,
  });
  series.push({
    type: "line",
    name: labels.price,
    id: "price",
    xAxisIndex: 1,
    yAxisIndex: 1,
    step: "end",
    symbol: "none",
    silent: true,
    lineStyle: { color: o.css.primary, width: 2 },
    areaStyle: { color: o.css.primary, opacity: 0.16 },
    data: [...b.map((k, i) => [i * step, k.price]), [H, b[b.length - 1]?.price ?? null]],
  });
  const trackH = o.compact ? 28 : 34;
  const trackBottom = o.compact ? 40 : 34;
  const lo = pmin - span * 0.35;
  const hi = pmax + span * 0.9;
  const priceY = (p: number) => o.height - trackBottom - ((p - lo) / (hi - lo)) * trackH;
  const graphic: Record<string, unknown>[] = [
    { type: "text", left: left - 20, top: o.height - trackBottom - trackH / 2 - 6, style: { text: o.currency, fill: o.css.text2, fontSize: 10 } },
  ];
  for (let i = 0; i < b.length; ) {
    let n = 1;
    while (i + n < b.length && Math.abs((b[i + n]!.price ?? -1) - (b[i]!.price ?? -1)) < 1e-4) n++;
    const p = b[i]!.price;
    if (p !== null && n * (plotW / b.length) > 40) {
      graphic.push({
        type: "text",
        x: left + (i + n / 2) * (plotW / b.length),
        y: priceY(p) - 14,
        style: { text: two.format(p), fill: o.css.text, fontSize: 10, fontWeight: 500, align: "center" },
      });
    }
    i += n;
  }
  const tickEvery = o.hours >= 48 || o.compact ? 6 : 3;
  return {
    animation: false,
    textStyle: { fontFamily: "Roboto, sans-serif" },
    grid: [
      { left, right, top: o.compact ? 132 : 56, bottom: o.compact ? 82 : 76 },
      { left, right, height: trackH, bottom: trackBottom },
    ],
    xAxis: [
      { type: "value", min: 0, max: H, interval: 1, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false } },
      {
        type: "value",
        gridIndex: 1,
        min: 0,
        max: H,
        interval: 1,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          color: o.css.text2,
          fontSize: o.compact ? 10 : 11,
          interval: 0,
          formatter: (v: number) => {
            const t = o.from + v * 3_600_000;
            return hourOf(t) % tickEvery === 0 && new Date(t).getUTCMinutes() === 0 && Math.abs(v - nowX) > 0.6 ? clock.format(t) : "";
          },
        },
      },
    ],
    yAxis: [
      {
        type: "value",
        min: 0,
        max: ymax,
        interval: ymax / 4,
        name: labels.unit_kwh_h,
        nameTextStyle: { color: o.css.text2, fontSize: 10, align: "right" },
        axisLabel: { color: o.css.text2, fontSize: 10 },
        splitLine: { lineStyle: { color: "rgba(225,225,225,0.07)", type: [2, 4] } },
      },
      { type: "value", gridIndex: 1, min: lo, max: hi, show: false },
    ],
    tooltip: {
      ...o.tooltip,
      trigger: "axis",
      axisPointer: { type: "shadow", shadowStyle: { color: "rgba(225,225,225,0.06)" } },
      formatter: (items: Array<{ axisIndex: number; seriesId: string; value: number[] }>) => tooltip(b, loads, items, o, ceiling),
    },
    series,
    graphic,
  };
}

function tooltip(
  b: readonly Bucket[],
  loads: readonly ForecastLoad[],
  items: Array<{ axisIndex: number; seriesId: string; value: number[] }>,
  o: ForecastOptions,
  ceiling: number | null,
): string {
  const item = items.find((x) => x.axisIndex === 0 || x.seriesId === "baseline") ?? items[0];
  if (!item) return "";
  const k = b[Math.floor(item.value[0]! / (o.windowMin / 60))];
  if (!k) return "";
  const labels = o.labels;
  const two = new Intl.NumberFormat(o.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const clock = new Intl.DateTimeFormat(o.locale, { hour: "2-digit", minute: "2-digit", timeZone: o.zone });
  const day = new Intl.DateTimeFormat(o.locale, { weekday: "short", timeZone: o.zone }).format(k.start);
  const rows = loads
    .filter((l) => (k.loads[l.id] ?? 0) > 0)
    .map(
      (l) =>
        `<div style="display:flex;gap:8px;align-items:center"><span style="width:8px;height:8px;border-radius:50%;background:${l.color}"></span><span style="flex:1">${escape(l.name)}</span><b style="font-weight:500">${two.format(k.loads[l.id]!)}</b></div>`,
    )
    .join("");
  const managed = Object.values(k.loads).reduce((s, v) => s + v, 0);
  const total = managed + (k.baseline ?? 0);
  const base =
    k.baseline !== null
      ? `<div style="display:flex;gap:8px;align-items:center;color:${o.css.text2}"><span style="width:8px;height:8px;border-radius:2px;background:rgba(155,155,155,.6)"></span><span style="flex:1">${escape(labels.other_usage_estimate)}</span><span>${two.format(k.baseline)}</span></div>`
      : "";
  const reserve =
    k.p90 !== null && k.baseline !== null
      ? `<div style="display:flex;justify-content:space-between;color:${o.css.text2}"><span>${escape(labels.reserve)}</span><span>+${two.format(k.p90 - k.baseline)} kWh</span></div>`
      : "";
  const price =
    k.price !== null
      ? `<div style="display:flex;justify-content:space-between;gap:16px;color:${o.css.text2}"><span>${escape(labels.price)} ${two.format(k.price)} ${escape(o.currency)}/kWh${k.estimated ? ` · ${escape(labels.estimated_short)}` : ""}</span><span>≈ ${two.format(managed * k.price)} ${escape(o.currency)} ${escape(labels.managed_short)}</span></div>`
      : "";
  return `<div style="min-width:230px;line-height:20px"><div style="font-weight:500;margin-bottom:2px">${escape(day)} ${clock.format(k.start)}–${clock.format(k.end)}</div>${rows}${base}
    <div style="border-top:1px solid ${o.css.divider};margin-top:6px;padding-top:6px;display:flex;justify-content:space-between"><span>${escape(labels.total)}</span><b style="font-weight:500">${two.format(total)}${ceiling !== null ? ` ${escape(labels.of)} ${two.format(ceiling)}` : ""} kWh</b></div>${reserve}${price}</div>`;
}

/** The rail: the total, the split bar, each load's kWh, the reserve and limit keys, the cheap share (F3). */
export function railHtml(b: readonly Bucket[], loads: readonly ForecastLoad[], o: ForecastOptions): string {
  const labels = o.labels;
  const two = new Intl.NumberFormat(o.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const one = new Intl.NumberFormat(o.locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const upToOne = new Intl.NumberFormat(o.locale, { maximumFractionDigits: 1 });
  const totals = forecastTotals(b, loads.map((l) => l.id));
  const per = loads.map((l) => ({ l, v: totals.loads[l.id] ?? 0 })).filter((x) => x.v > 0.005);
  const total = totals.other + totals.managed;
  const seg = (w: number, c: string) => `<div style="width:${((w / Math.max(total, 1e-6)) * 100).toFixed(2)}%;background:${c}"></div>`;
  const row = (swatch: string, name: string | undefined, value: string) =>
    `<div class="lr"><span class="sw" style="${swatch}"></span><span class="n">${escape(name)}</span><span class="v">${escape(value)}</span></div>`;
  const ceiling = b.find((k) => k.ceiling !== null)?.ceiling ?? null;
  return `<div class="rail">
    <span class="k">${escape(fill(labels.next_hours, { h: o.hours }))}</span>
    <div class="tot"><span>${one.format(total)}</span><small>${escape(labels.kwh_expected)}</small></div>
    <div class="split">${totals.other > 0 ? seg(totals.other, "rgba(155,155,155,.5)") : ""}${per.map((x) => seg(x.v, x.l.color)).join("")}</div>
    <div class="splitl"><span>${escape(labels.unmanaged)} ${one.format(totals.other)}</span><span>${escape(labels.managed)} ${one.format(totals.managed)} kWh</span></div>
    ${totals.other > 0 ? row("background:rgba(155,155,155,.5)", labels.other_usage, one.format(totals.other)) : ""}
    ${per.map((x) => row(`background:${x.l.color}`, x.l.name, two.format(x.v))).join("")}
    <div class="hr"></div>
    ${b.some((k) => k.p90 !== null) ? row("border:1px dashed rgba(225,225,225,.45);background:repeating-linear-gradient(45deg,rgba(225,225,225,.25) 0 2px,transparent 2px 5px)", labels.reserve, "p90") : ""}
    ${ceiling !== null ? row(`height:0;border-top:2px dashed ${o.css.error};border-radius:0`, labels.limit, `${upToOne.format(ceiling)} ${labels.unit_kwh_h ?? ""}`) : ""}
    <span class="grow"></span>
    ${totals.cheapPct !== null ? `<div class="ok"><ha-icon icon="mdi:check-circle-outline"></ha-icon><span>${escape(fill(labels.cheap_share, { pct: totals.cheapPct }))}</span></div>` : ""}
  </div>`;
}

/** Under 600 px: no rail, a summary above the chart (F6). */
export function summaryHtml(b: readonly Bucket[], loads: readonly ForecastLoad[], o: ForecastOptions): string {
  const labels = o.labels;
  const one = new Intl.NumberFormat(o.locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const totals = forecastTotals(b, loads.map((l) => l.id));
  const per = loads.map((l) => ({ l, v: totals.loads[l.id] ?? 0 })).filter((x) => x.v > 0.005);
  const total = Math.max(totals.other + totals.managed, 1e-6);
  const seg = (w: number, c: string) => `<div style="width:${((w / total) * 100).toFixed(2)}%;background:${c}"></div>`;
  return `<div class="sum">
    <span class="k">${escape(fill(labels.next_hours, { h: o.hours }))}</span>
    <div class="tot"><span>${one.format(totals.other + totals.managed)}</span><small>kWh</small></div>
    <div class="split">${totals.other > 0 ? seg(totals.other, "rgba(155,155,155,.5)") : ""}${per.map((x) => seg(x.v, x.l.color)).join("")}</div>
    <div class="splitl"><span>${escape(labels.unmanaged)} ${one.format(totals.other)}</span><span>${escape(labels.managed)} ${one.format(totals.managed)} kWh</span></div>
  </div>`;
}

export const FORECAST_CSS = `
  .sum { position: absolute; left: 12px; right: 12px; top: 12px; font-size: 12px; }
  .sum .k { color: var(--secondary-text-color); }
  .sum .tot { display: flex; align-items: baseline; gap: 5px; } .sum .tot span { font-size: 22px; } .sum .tot small { color: var(--secondary-text-color); }
  .sum .split { display: flex; height: 8px; border-radius: 4px; overflow: hidden; gap: 1px; margin-top: 8px; }
  .sum .splitl { display: flex; justify-content: space-between; color: var(--secondary-text-color); font-size: 11px; margin-top: 5px; }
  .rail { position: absolute; left: 0; top: 0; bottom: 0; box-sizing: border-box; padding: 16px 16px 14px; border-right: 1px solid var(--divider-color);
          display: flex; flex-direction: column; font-size: 12px; }
  .rail .k { color: var(--secondary-text-color); }
  .rail .tot { display: flex; align-items: baseline; gap: 6px; margin-top: 2px; }
  .rail .tot span { font-size: 28px; letter-spacing: -.4px; } .rail .tot small { font-size: 13px; color: var(--secondary-text-color); }
  .rail .split { display: flex; height: 8px; border-radius: 4px; overflow: hidden; gap: 1px; margin: 10px 0 4px; }
  .rail .splitl { display: flex; justify-content: space-between; color: var(--secondary-text-color); font-size: 11px; margin-bottom: 10px; }
  .rail .lr { display: flex; align-items: center; gap: 8px; height: 22px; }
  .rail .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; box-sizing: border-box; }
  .rail .n { flex: 1 1 auto; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .rail .v { color: var(--secondary-text-color); }
  .rail .hr { height: 1px; background: var(--divider-color); margin: 8px 0; }
  .rail .grow { flex: 1 1 auto; }
  .rail .ok { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 10px; background: rgba(67,160,71,.12);
              color: #9ad69d; line-height: 16px; --mdc-icon-size: 16px; }
  .rail .ok ha-icon { color: #7ccf80; flex: none; }
`;
