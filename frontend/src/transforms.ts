// The two cards' pure halves (D12 §5.2, §5.3, §9 7): entity attributes in,
// what to draw out. No DOM, no ECharts - vitest covers these.

/** One row of `sensor.<site>_price_forecast`'s `slots` (D8 §5.5). */
export interface PriceSlot {
  start: string;
  end: string;
  total: string;
  confidence: string;
}

/** One row of `sensor.<site>_plan`'s `slots` (D12 §5.6). */
export interface PlanSlot {
  start: string;
  end: string;
  ceiling_kwh: number | null;
  baseline_kwh: number | null;
  production_kwh?: number | null;
  planned_kwh: Record<string, number>;
}

/** One slot as the timeline draws it: power in kW, so slots of any length compare. */
export interface TimelineSlot {
  start: number;
  end: number;
  hours: number;
  price: number | null;
  /** The price is not yet published: `estimated` or `synthesised` (D1), drawn as provisional. */
  estimated: boolean;
  ceilingKw: number | null;
  baselineKw: number | null;
  productionKw: number | null;
  loadKw: Record<string, number>;
  loadKwh: Record<string, number>;
}

const HOUR_MS = 3_600_000;

/**
 * Join the plan's slots to the price curve, from the slot in progress to
 * `hours` ahead (D12 §5.2).
 *
 * The grid is the plan's own - the import curve's slots, at their native
 * length (D1 §5.8) - or the curve's when nothing is planned yet. Times are
 * absolute instants, so a DST day's 92 or 100 quarter slots follow one
 * another with no gap and no overlap. Energy becomes average power: a
 * window's ceiling over the window, a slot's kWh over the slot.
 */
export function timelineSlots(
  prices: readonly PriceSlot[],
  plan: readonly PlanSlot[],
  windowMin: number,
  loadIds: readonly string[],
  now: number,
  hours: number,
): TimelineSlot[] {
  const priced = prices
    .map((slot) => ({
      start: Date.parse(slot.start),
      end: Date.parse(slot.end),
      price: Number(slot.total),
      estimated: slot.confidence !== "known",
    }))
    .sort((a, b) => a.start - b.start);
  const grid: Array<{ start: number; end: number; row?: PlanSlot }> = plan.length
    ? plan.map((row) => ({ start: Date.parse(row.start), end: Date.parse(row.end), row }))
    : priced.map(({ start, end }) => ({ start, end }));
  grid.sort((a, b) => a.start - b.start);
  const until = now + hours * HOUR_MS;
  const windowHours = windowMin / 60;
  const out: TimelineSlot[] = [];
  let cursor = 0;
  for (const { start, end, row } of grid) {
    if (end <= now || start >= until) continue;
    while (cursor < priced.length && priced[cursor]!.end <= start) cursor++;
    const price = priced[cursor];
    const covers = price !== undefined && price.start <= start;
    const slotHours = (end - start) / HOUR_MS;
    const loadKwh: Record<string, number> = {};
    const loadKw: Record<string, number> = {};
    for (const id of loadIds) {
      const kwh = row?.planned_kwh[id] ?? 0;
      loadKwh[id] = kwh;
      loadKw[id] = kwh / slotHours;
    }
    out.push({
      start,
      end,
      hours: slotHours,
      price: covers ? price.price : null,
      estimated: covers ? price.estimated : false,
      ceilingKw: row?.ceiling_kwh == null ? null : row.ceiling_kwh / windowHours,
      baselineKw: row?.baseline_kwh == null ? null : row.baseline_kwh / slotHours,
      productionKw: row?.production_kwh == null ? null : row.production_kwh / slotHours,
      loadKw,
      loadKwh,
    });
  }
  return out;
}

/** The contiguous runs of slots whose price is provisional, as `[start, end)` pairs. */
export function estimatedRanges(slots: readonly TimelineSlot[]): Array<[number, number]> {
  const ranges: Array<[number, number]> = [];
  for (const slot of slots) {
    if (!slot.estimated) continue;
    const last = ranges[ranges.length - 1];
    if (last && last[1] === slot.start) last[1] = slot.end;
    else ranges.push([slot.start, slot.end]);
  }
  return ranges;
}

/** Step-line points: each slot's value held to its end, a gap where it is `null`. */
export function steps(
  slots: readonly TimelineSlot[],
  value: (slot: TimelineSlot) => number | null,
): Array<[number, number | null]> {
  const points: Array<[number, number | null]> = [];
  slots.forEach((slot, index) => {
    const previous = slots[index - 1];
    if (previous && previous.end !== slot.start) points.push([previous.end, null]);
    points.push([slot.start, value(slot)]);
  });
  const last = slots[slots.length - 1];
  if (last) points.push([last.end, value(last)]);
  return points;
}

// --------------------------------------------------------------------------- //
// The timeline's window, stack, scale, price strip, legend and readout (D12 §5.2)
// --------------------------------------------------------------------------- //

export interface WindowConfig {
  hours?: number;
  narrow_hours?: number;
  narrow_width?: number;
}

/** Rule 1: the hours to draw - the household's toggle, else `narrow_hours` below `narrow_width`. */
export function windowHours(width: number, cfg: WindowConfig, chosen?: number): number {
  if (chosen !== undefined) return chosen;
  const narrow = width > 0 && width < (cfg.narrow_width ?? 500);
  return narrow ? (cfg.narrow_hours ?? 12) : (cfg.hours ?? 24);
}

/** The slots from the one in progress to `hours` ahead of `start`. */
export function sliceWindow(slots: readonly TimelineSlot[], start: number, hours: number): TimelineSlot[] {
  const until = start + hours * HOUR_MS;
  return slots.filter((slot) => slot.end > start && slot.start < until);
}

export interface Stack {
  /** The transparent bar under the loads: the rest of the house, in kW. */
  offset: number;
  /** Each load's kW, in priority order. */
  loads: number[];
  /** The bar's top: the rest of the house plus every load. */
  total: number;
  /** The index in `loads` of the topmost segment with power, `-1` for none (rule 2's rounded top). */
  top: number;
}

/** Rule 2: the loads stacked on the rest of the house, so the bar's top is the total. */
export function stackOffsets(slot: TimelineSlot, loadIds: readonly string[]): Stack {
  const offset = slot.baselineKw ?? 0;
  const loads = loadIds.map((id) => slot.loadKw[id] ?? 0);
  let top = -1;
  loads.forEach((kw, index) => {
    if (kw > 0) top = index;
  });
  return { offset, loads, total: loads.reduce((sum, kw) => sum + kw, offset), top };
}

/** A round axis maximum and its tick step, in 4–5 ticks: 10,8 → 12 by 3, 7,3 → 8 by 2. */
export function niceScale(max: number): { max: number; step: number } {
  if (!(max > 0) || !Number.isFinite(max)) return { max: 1, step: 0.25 };
  const magnitude = 10 ** Math.floor(Math.log10(max / 5));
  for (const factor of [1, 2, 3, 4, 5, 10, 20, 30, 40, 50]) {
    const step = factor * magnitude;
    const ticks = Math.ceil(max / step - 1e-9);
    if (ticks <= 5) return { max: Number((ticks * step).toPrecision(12)), step };
  }
  return { max, step: max / 5 };
}

/** Rule 3: the y-axis maximum over every candidate top (`ceiling × 1,2`, `peak × 1,1`, …). */
export function niceMax(values: readonly number[]): number {
  return niceScale(Math.max(0, ...values.filter(Number.isFinite))).max;
}

export interface PriceRun {
  start: number;
  end: number;
  price: number;
  estimated: boolean;
  /** Rule 5: the fill's alpha, 0,28 at the window's cheapest to 0,62 at its dearest. */
  alpha: number;
}

/** Rule 5: one rectangle per run of equal price and confidence. */
export function priceRuns(slots: readonly TimelineSlot[]): PriceRun[] {
  const runs: Array<Omit<PriceRun, "alpha">> = [];
  for (const slot of slots) {
    if (slot.price === null) continue;
    const last = runs[runs.length - 1];
    if (last && last.end === slot.start && last.price === slot.price && last.estimated === slot.estimated) {
      last.end = slot.end;
    } else {
      runs.push({ start: slot.start, end: slot.end, price: slot.price, estimated: slot.estimated });
    }
  }
  const prices = runs.map((run) => run.price);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  return runs.map((run) => ({
    ...run,
    alpha: high > low ? 0.28 + (0.34 * (run.price - low)) / (high - low) : 0.45,
  }));
}

export interface LegendLoad {
  id: string;
  name: string;
  kwh: number;
}

/** Rule 9: the loads with planned energy inside the window, in priority order, with their kWh. */
export function legendItems(
  loads: ReadonlyArray<{ id: string; name: string }>,
  slots: readonly TimelineSlot[],
): LegendLoad[] {
  return loads
    .map((load) => ({
      ...load,
      kwh: slots.reduce((sum, slot) => sum + (slot.loadKwh[load.id] ?? 0), 0),
    }))
    .filter((load) => load.kwh > 0);
}

export interface Readout {
  start: number;
  end: number;
  /** The bar's top in kW (rule 2). */
  sumKw: number;
  limitKw: number | null;
  /** How many loads have power in the slot. */
  count: number;
  price: number | null;
  estimated: boolean;
  /** The slot's planned kWh at the slot's price, `null` without a price. */
  cost: number | null;
}

/** Rules 10–11: what the tooltip and the touch readout say about one slot. */
export function slotReadout(slot: TimelineSlot, loadIds: readonly string[]): Readout {
  const stack = stackOffsets(slot, loadIds);
  const kwh = loadIds.reduce((sum, id) => sum + (slot.loadKwh[id] ?? 0), 0);
  return {
    start: slot.start,
    end: slot.end,
    sumKw: stack.total,
    limitKw: slot.ceilingKw,
    count: stack.loads.filter((kw) => kw > 0).length,
    price: slot.price,
    estimated: slot.estimated,
    cost: slot.price === null ? null : kwh * slot.price,
  };
}

/** The first slot at or after `now` with planned power: the readout before any tap (rule 11). */
export function nextPlanned(slots: readonly TimelineSlot[], loadIds: readonly string[]): TimelineSlot | undefined {
  return slots.find((slot) => loadIds.some((id) => (slot.loadKwh[id] ?? 0) > 0));
}

/** One load's contiguous runs of planned energy, as `[start, end)` pairs (rule 12). */
export function runsForLoad(slots: readonly TimelineSlot[], id: string): Array<[number, number]> {
  const runs: Array<[number, number]> = [];
  for (const slot of slots) {
    if ((slot.loadKwh[id] ?? 0) <= 0) continue;
    const last = runs[runs.length - 1];
    if (last && last[1] === slot.start) last[1] = slot.end;
    else runs.push([slot.start, slot.end]);
  }
  return runs;
}

/** Rule 7: the instants of local midnight in `[start, end)`, in HA's time zone. */
export function midnights(start: number, end: number, timeZone?: string): number[] {
  const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone });
  const out: number[] = [];
  // Every quarter hour: a zone's midnight falls on one, DST days included.
  for (let t = Math.ceil(start / 900_000) * 900_000; t < end; t += 900_000) {
    if (t > start && parts.format(t) === "00:00") out.push(t);
  }
  return out;
}

/** A CSS colour at `alpha`: `#rgb`, `#rrggbb` and `rgb()` are mixed, anything else is left as it is. */
export function withAlpha(color: string, alpha: number): string {
  const value = color.trim();
  let rgb: number[] | null = null;
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const digits = hex[1]!.length === 3 ? [...hex[1]!].map((d) => d + d) : hex[1]!.match(/../g)!;
    rgb = digits.map((d) => parseInt(d, 16));
  } else {
    const fn = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(value);
    if (fn) rgb = [Number(fn[1]), Number(fn[2]), Number(fn[3])];
  }
  return rgb ? `rgba(${rgb.join(", ")}, ${alpha})` : value;
}

// --------------------------------------------------------------------------- //
// The window gauge (D12 §5.3)
// --------------------------------------------------------------------------- //

export type Tone = "ok" | "warn" | "alert";

/** The ladder stage's colour: 0 green, 1–2 amber, 3–4 red. */
export function stageTone(stage: number | null): Tone {
  if (stage === null || !Number.isFinite(stage) || stage <= 0) return "ok";
  return stage <= 2 ? "warn" : "alert";
}

export const TONE_COLOR: Record<Tone, string> = {
  ok: "var(--success-color, #43a047)",
  warn: "var(--warning-color, #ffa600)",
  alert: "var(--error-color, #db4437)",
};

export interface Gauge {
  /** The arc's full scale in kWh: the ceiling, or 1,2 × what the window reaches without one. */
  max: number;
  used: number;
  /** The projection's place on the arc: the end of the projected arc and its tick. */
  projected: number | null;
  over: boolean;
}

/** Scale used and projected kWh against the ceiling; each fraction is clamped to the arc. */
export function gauge(used: number, projected: number | null, ceiling: number | null): Gauge {
  const hasCeiling = ceiling !== null && Number.isFinite(ceiling) && ceiling > 0;
  const max = hasCeiling ? ceiling : Math.max(used, projected ?? 0, 0.1) * 1.2;
  const fraction = (value: number) => Math.min(Math.max(value / max, 0), 1);
  return {
    max,
    used: fraction(used),
    projected: projected === null ? null : fraction(Math.max(projected, used)),
    over: hasCeiling && Math.max(used, projected ?? 0) > ceiling,
  };
}

export type StageWord = "normal" | "tight" | "critical";

/** The status chip's word for a ladder stage: 0 normal, 1–2 tight, 3–4 critical. */
export function stageWord(tone: Tone): StageWord {
  return tone === "ok" ? "normal" : tone === "warn" ? "tight" : "critical";
}

/** The allowance in kW, whatever unit the sensor shows (B8: "11000.0 W" read as 11 kW). */
export function toKw(value: number, unit: unknown): number {
  return unit === "W" ? value / 1000 : unit === "MW" ? value * 1000 : value;
}

/** A point on the gauge's semicircle: fraction 0 at the left, 1 at the right. */
export function arcPoint(fraction: number, radius: number): [number, number] {
  const angle = Math.PI * (1 - fraction);
  return [radius * Math.cos(angle), -radius * Math.sin(angle)];
}

// --------------------------------------------------------------------------- //
// The strategy's paths (D12 §5.1)
// --------------------------------------------------------------------------- //

/** The placeholder `layout.py` writes for the dashboard's own URL path (D12 §5.1). */
export const DASHBOARD = "{dashboard}";

/** The dashboard's URL path: the first segment of the page's path ("dashboard-powerplan"). */
export function dashboardPath(pathname: string): string {
  return pathname.split("/").filter(Boolean)[0] ?? "";
}

/** Replace a leading `{dashboard}/` in every string of `config` with `/<urlPath>/`. */
export function resolvePaths<T>(config: T, urlPath: string): T {
  if (typeof config === "string") {
    return (config.startsWith(`${DASHBOARD}/`) ? `/${urlPath}${config.slice(DASHBOARD.length)}` : config) as T;
  }
  if (Array.isArray(config)) return config.map((item) => resolvePaths(item, urlPath)) as T;
  if (config && typeof config === "object") {
    return Object.fromEntries(
      Object.entries(config).map(([key, value]) => [key, resolvePaths(value, urlPath)]),
    ) as T;
  }
  return config;
}

// --------------------------------------------------------------------------- //
// The month gauge (D12 §5.3, `mode: month`)
// --------------------------------------------------------------------------- //

/** One tariff step, as `sensor.<site>_level` → `steps` publishes it (D12 §5.6, B6). */
export interface TariffStep {
  name: string;
  from_kw: number;
  to_kw: number | null;
  fee?: string | null;
}

export interface MonthSegment {
  from: number;
  to: number;
  tone: Tone;
  current: boolean;
}

export interface MonthGauge {
  /** The arc's full scale in kW. */
  max: number;
  /** The step the metric is in; a threshold belongs to the step above (D2 §5.3). */
  index: number;
  segments: MonthSegment[];
  /** Step boundaries inside the scale, for the tick labels. */
  ticks: number[];
  needle: number;
  /** The step above's upper bound: the top-3 bars' scale. */
  barMax: number;
  /** The current step's upper bound: the top-3 bars' dashed line. */
  upper: number | null;
}

/**
 * Lay the tariff's steps on the arc: 0 to the upper bound two steps above the
 * current one (the highest finite bound where there are fewer), so the next
 * step is shown whole with room beyond it (D-0463). Steps whose lower bound is
 * under the target are green, the next amber, the rest red (D-0452).
 */
export function monthGauge(metric: number, steps: readonly TariffStep[], targetKw: number | null): MonthGauge {
  let index = steps.findIndex((step) => step.to_kw === null || metric < step.to_kw);
  if (index < 0) index = steps.length - 1;
  const finite = steps.map((step) => step.to_kw).filter((kw): kw is number => kw !== null);
  const top = finite.length ? Math.max(...finite) : Math.max(metric * 1.2, 1);
  const upperOf = (i: number) => steps[Math.min(i, steps.length - 1)]?.to_kw ?? top;
  const max = Math.max(Math.min(upperOf(index + 2), top), metric, upperOf(index));
  const target = targetKw ?? upperOf(index);
  let amber = false;
  const segments = steps
    .filter((step) => step.from_kw < max)
    .map((step, i) => {
      let tone: Tone = "alert";
      if (step.from_kw < target - 1e-9) tone = "ok";
      else if (!amber) {
        amber = true;
        tone = "warn";
      }
      return { from: step.from_kw, to: Math.min(step.to_kw ?? max, max), tone, current: i === index };
    });
  return {
    max,
    index,
    segments,
    ticks: steps.map((step) => step.to_kw).filter((kw): kw is number => kw !== null && kw < max),
    needle: Math.min(Math.max(metric / max, 0), 1),
    barMax: upperOf(index + 1),
    upper: steps[index]?.to_kw ?? null,
  };
}

/** An `advice` item's params by key (`sensor.<site>_advice` → `items`, D2 §5.11). */
export function adviceItem(items: unknown, key: string): Record<string, unknown> | undefined {
  if (!Array.isArray(items)) return undefined;
  return items.find((item) => (item as { key?: string })?.key === key) as Record<string, unknown> | undefined;
}

/** The top entries as `[day, kW]`, highest first: the month's days that count. */
export function topEntries(items: unknown): Array<[string, number]> {
  const entries = adviceItem(items, "top_entries")?.entries;
  if (!Array.isArray(entries)) return [];
  return entries
    .map((entry) => [String((entry as unknown[])[0]), Number((entry as unknown[])[1])] as [string, number])
    .filter(([, kw]) => Number.isFinite(kw))
    .sort((a, b) => b[1] - a[1]);
}
