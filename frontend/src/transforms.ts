// The two cards' pure halves (D12 §5.2, §5.3, §9 7): entity attributes in,
// what to draw out. No DOM, no ECharts - vitest covers these.

/** One row of `sensor.<site>_price_forecast`'s `slots` (D8 §5.5). */
export interface PriceSlot {
  start: string;
  end: string;
  total: string;
  confidence: string;
  /** The energy part incl. its VAT (D12 §5.12 P3); absent before WP6.4i. */
  energy?: string;
  /** The same slot without the fixed-price modifier (Norgespris), where one is configured (P3). */
  reference?: string;
}

/** One row of `sensor.<site>_plan`'s `slots` (D12 §5.6). */
export interface PlanSlot {
  start: string;
  end: string;
  ceiling_kwh: number | null;
  baseline_kwh: number | null;
  /** The rest of the house's high estimate (P90), for the forecast's reserve (D12 §5.12 F2). */
  baseline_p90_kwh?: number | null;
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
 * The ladder index of the household's step target (M1): `step_2` → 2; else
 * the step whose lower bound the select names (`lower_kw`); else the step
 * that holds `target_kw`. `null` when the select says none of these (`auto`
 * before it has resolved a step), and the gauge then takes the current step.
 * `target_kw` may be `null` on the live select, so it is never read through
 * `Number()`, which turns `null` into 0.
 */
export function targetStep(
  option: string | undefined,
  attributes: Record<string, unknown> | undefined,
  steps: readonly TariffStep[],
): number | null {
  const named = /^step_(\d+)$/.exec(option ?? "");
  if (named && Number(named[1]) < steps.length) return Number(named[1]);
  const lower = attributes?.lower_kw;
  if (typeof lower === "number") {
    const index = steps.findIndex((step) => Math.abs(step.from_kw - lower) < 1e-9);
    if (index >= 0) return index;
  }
  const kw = attributes?.target_kw;
  if (typeof kw === "number" && Number.isFinite(kw)) {
    const index = steps.findIndex((step) => step.to_kw === null || kw <= step.to_kw + 1e-9);
    if (index >= 0) return index;
  }
  return null;
}

/**
 * Lay the tariff's steps on the arc: 0 to the upper bound two steps above the
 * current one (the highest finite bound where there are fewer), so the next
 * step is shown whole with room beyond it (D-0463). Colour by position against
 * the target step (M1): up to it green, the next amber, above that red; the
 * current step opaque, the rest at 28 %.
 */
export function monthGauge(metric: number, steps: readonly TariffStep[], target: number | null): MonthGauge {
  let index = steps.findIndex((step) => step.to_kw === null || metric < step.to_kw);
  if (index < 0) index = steps.length - 1;
  const finite = steps.map((step) => step.to_kw).filter((kw): kw is number => kw !== null);
  const top = finite.length ? Math.max(...finite) : Math.max(metric * 1.2, 1);
  const upperOf = (i: number) => steps[Math.min(i, steps.length - 1)]?.to_kw ?? top;
  const max = Math.max(Math.min(upperOf(index + 2), top), metric, upperOf(index));
  const goal = target ?? index;
  const segments = steps
    .filter((step) => step.from_kw < max)
    .map((step, i) => {
      const tone: Tone = i <= goal ? "ok" : i === goal + 1 ? "warn" : "alert";
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

// --------------------------------------------------------------------------- //
// The period summary (D12 §5.7)
// --------------------------------------------------------------------------- //

/** A period as the History picker sets it: `end` exclusive, or the picker's 23:59:59.999. */
export interface SummaryPeriod {
  start: Date;
  end: Date;
}

/** A single day, a whole calendar month, or any other range. */
export type PeriodKind = "day" | "month" | "range";

/** The last cell: this month's capacity step, a day's highest hour, or a range's highest daily peak. */
export type SummaryMode = "capacity" | "hour" | "peak";

/** Year, month (1–12) and day of `date` on the wall clock of `zone` (the browser's when undefined). */
export function zonedDay(date: Date, zone?: string): [number, number, number] {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    timeZone: zone,
  }).formatToParts(date);
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return [part("year"), part("month"), part("day")];
}

/** What the picker chose, from its first and last day read in HA's zone. */
export function periodKind(period: SummaryPeriod, zone?: string): PeriodKind {
  const [y0, m0, d0] = zonedDay(period.start, zone);
  const [y1, m1, d1] = zonedDay(new Date(period.end.getTime() - 1), zone);
  if (y0 === y1 && m0 === m1 && d0 === d1) return "day";
  const lastDay = new Date(Date.UTC(y0, m0, 0)).getUTCDate();
  return d0 === 1 && y1 === y0 && m1 === m0 && d1 === lastDay ? "month" : "range";
}

/** Whether `date` falls in the calendar month of `now`, in HA's zone. */
export function inMonthOf(date: Date, now: Date, zone?: string): boolean {
  const [y0, m0] = zonedDay(date, zone);
  const [y1, m1] = zonedDay(now, zone);
  return y0 === y1 && m0 === m1;
}

/** The last cell's mode: the capacity step only for the month in progress. */
export function summaryMode(kind: PeriodKind, currentMonth: boolean): SummaryMode {
  if (kind === "day") return "hour";
  return kind === "month" && currentMonth ? "capacity" : "peak";
}

/**
 * Whether a day's highest hour counts toward the month's capacity step: it
 * does when it reaches the month's third-highest day, or when the month has
 * fewer than three days. `ranking` is `[day, kW]` per day, in any order.
 */
export function countsDecision(
  peak: number,
  ranking: ReadonlyArray<readonly [string, number]>,
): { counts: boolean; third?: [string, number] } {
  const third = [...ranking].sort((a, b) => b[1] - a[1])[2];
  if (!third) return { counts: true };
  return { counts: peak >= third[1], third: [third[0], third[1]] };
}

/** Σ change over several statistics, `null` when none of them has a row. */
export function sumChanges(
  stats: Record<string, ReadonlyArray<{ change?: number | null }> | undefined>,
  ids: readonly string[],
): number | null {
  let total: number | null = null;
  for (const id of ids) {
    const rows = stats[id];
    if (!rows?.length) continue;
    total = (total ?? 0) + rows.reduce((sum, row) => sum + (row.change ?? 0), 0);
  }
  return total;
}

/** A summary figure: money and kW to 2 decimals; kWh to 0 from 100 up, to 1 below. */
export function formatSummary(value: number, kind: "money" | "kw" | "kwh", locale: string): string {
  const digits = kind === "kwh" ? (Math.abs(value) >= 100 ? 0 : 1) : 2;
  return new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

/**
 * `card_collecting` when no statistics row gives a date: the bracketed part
 * holding `{date}` goes, or the placeholder alone when there are no brackets.
 */
export function withoutDate(label: string): string {
  return label
    .replace(/\s*\([^)]*\{date\}[^)]*\)/, "")
    .replace(/\s*\{date\}/, "")
    .trim();
}

// --------------------------------------------------------------------------- //
// History: the days that count (D12 §5.7)
// --------------------------------------------------------------------------- //

/** A day's highest window, keyed by its local date `YYYY-MM-DD`. */
export type DayPeak = [day: string, kw: number];

/**
 * The days whose peak counts toward the capacity step: each month's `n`
 * highest (the Norwegian mean of the top three, D2 §5.2). Ties keep the earlier day.
 */
export function countingDays(peaks: readonly DayPeak[], n = 3): Set<string> {
  const byMonth = new Map<string, DayPeak[]>();
  for (const peak of peaks) {
    const month = peak[0].slice(0, 7);
    byMonth.set(month, [...(byMonth.get(month) ?? []), peak]);
  }
  const out = new Set<string>();
  for (const days of byMonth.values()) {
    [...days]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, n)
      .forEach(([day]) => out.add(day));
  }
  return out;
}

/** The `n`-th highest peak of a month: the bar a day must pass to count. */
export function nthHighest(peaks: readonly DayPeak[], n = 3): DayPeak | undefined {
  return [...peaks].sort((a, b) => b[1] - a[1])[n - 1];
}

/** A local date key `YYYY-MM-DD` for an instant in `timeZone`. */
export function dayKey(instant: number, timeZone?: string): string {
  return new Intl.DateTimeFormat("sv-SE", { year: "numeric", month: "2-digit", day: "2-digit", timeZone }).format(instant);
}

// --------------------------------------------------------------------------- //
// Money, runs and type (D12 §5.9, §5.11)
// --------------------------------------------------------------------------- //

/** Money in the viewer's language and the site's currency (G7): nb → "2,78 kr", en → "NOK 2.78". */
export function moneyFormat(locale: string, currency: string, digits = 2): Intl.NumberFormat {
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  } catch {
    return new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
}

/** The currency as the viewer's language writes it beside a number: "kr" in nb, "NOK" in en (G7). */
export function currencyWord(locale: string, currency: string): string {
  const part = moneyFormat(locale, currency)
    .formatToParts(1)
    .find((p) => p.type === "currency")?.value;
  return part ?? currency;
}

/** `sensor.<site>_plan` → `by_load`, one load's row (D8 §5.5). */
export interface ByLoad {
  planned_kwh?: number | null;
  next_start?: string | null;
  cost?: string | null;
}

export interface RunRow {
  id: string;
  name: string;
  color: string;
  /** The slot start, or `null` for a run already going ("nå"). */
  start: number | null;
  kwh: number;
  cost: number;
}

/** The runs list (N8): each load with planned energy, by start, "now" first, and the sum. */
export function runRows(
  byLoad: Record<string, ByLoad> | undefined,
  loads: ReadonlyArray<{ id: string; name: string; color: string }>,
  now: number,
  minKwh = 0.05,
): { rows: RunRow[]; kwh: number; cost: number } {
  const rows: RunRow[] = [];
  for (const load of loads) {
    const row = byLoad?.[load.id];
    const kwh = Number(row?.planned_kwh ?? 0);
    const at = row?.next_start ? Date.parse(row.next_start) : Number.NaN;
    if (!(kwh >= minKwh) || Number.isNaN(at)) continue;
    const cost = Number(String(row?.cost ?? "0").split(" ")[0]);
    rows.push({ ...load, start: at <= now ? null : at, kwh, cost: Number.isFinite(cost) ? cost : 0 });
  }
  rows.sort((a, b) => (a.start ?? -Infinity) - (b.start ?? -Infinity));
  return {
    rows,
    kwh: rows.reduce((sum, row) => sum + row.kwh, 0),
    cost: rows.reduce((sum, row) => sum + row.cost, 0),
  };
}

/**
 * The hour gauge's value size (H1): 36 px, or less so that the text stays
 * inside the arc - at most `2 × (r − stroke/2 − 12)` wide, a digit ≈ 0,56 em.
 */
export function gaugeFont(chars: number, radius: number, stroke: number, max = 36): number {
  const room = 2 * (radius - stroke / 2 - 12);
  return Math.max(20, Math.min(max, Math.floor(room / (0.56 * Math.max(chars, 1)))));
}

// --------------------------------------------------------------------------- //
// Iteration 3: the appliances card, the price card and the whole-house forecast (D12 §5.12)
// --------------------------------------------------------------------------- //

export interface Run {
  start: number;
  end: number;
  kwh: number;
}

/** One load's planned runs: consecutive slots with energy merged, in order (R3). */
export function planRuns(slots: readonly PlanSlot[], id: string): Run[] {
  const out: Run[] = [];
  for (const slot of [...slots].sort((a, b) => Date.parse(a.start) - Date.parse(b.start))) {
    const kwh = Number(slot.planned_kwh[id] ?? 0);
    if (!(kwh > 0.0005)) continue;
    const start = Date.parse(slot.start);
    const end = Date.parse(slot.end);
    const last = out[out.length - 1];
    if (last && Math.abs(last.end - start) < 1000) {
      last.end = end;
      last.kwh += kwh;
    } else out.push({ start, end, kwh });
  }
  return out;
}

/** The cheap threshold: the lowest quarter of the prices' range, `null` for a flat curve (R4, P2, F4). */
export function cheapThreshold(prices: readonly number[]): number | null {
  const finite = prices.filter(Number.isFinite);
  if (!finite.length) return null;
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  return hi - lo > 1e-4 ? lo + (hi - lo) * 0.25 : null;
}

/** The cheap hours inside `[from, to)` as merged `[start, end)` bands (R4, P2). */
export function cheapBands(prices: readonly PriceSlot[], from: number, to: number): Array<[number, number]> {
  const inside = prices
    .map((slot) => ({ start: Date.parse(slot.start), end: Date.parse(slot.end), price: Number(slot.total) }))
    .filter((slot) => slot.end > from && slot.start < to && Number.isFinite(slot.price))
    .sort((a, b) => a.start - b.start);
  const threshold = cheapThreshold(inside.map((slot) => slot.price));
  if (threshold === null) return [];
  const bands: Array<[number, number]> = [];
  for (const slot of inside) {
    if (slot.price > threshold) continue;
    const last = bands[bands.length - 1];
    if (last && Math.abs(last[1] - slot.start) < 1000) last[1] = slot.end;
    else bands.push([slot.start, slot.end]);
  }
  return bands;
}

/** One bar of the whole-house forecast: a window's energy, so bars and the limit share kWh per window (F1). */
export interface Bucket {
  start: number;
  end: number;
  baseline: number | null;
  p90: number | null;
  loads: Record<string, number>;
  ceiling: number | null;
  price: number | null;
  estimated: boolean;
}

/** The plan's slots summed into `windowMin` buckets from `from`, each with its mean price (F1). */
export function bucketize(
  plan: readonly PlanSlot[],
  prices: readonly PriceSlot[],
  windowMin: number,
  from: number,
  hours: number,
): Bucket[] {
  const width = windowMin * 60_000;
  const n = Math.round((hours * HOUR_MS) / width);
  const out: Bucket[] = Array.from({ length: n }, (_, i) => ({
    start: from + i * width,
    end: from + (i + 1) * width,
    baseline: null,
    p90: null,
    loads: {},
    ceiling: null,
    price: null,
    estimated: false,
  }));
  for (const slot of plan) {
    const bucket = out[Math.floor((Date.parse(slot.start) - from) / width)];
    if (!bucket) continue;
    if (slot.baseline_kwh != null) bucket.baseline = (bucket.baseline ?? 0) + slot.baseline_kwh;
    if (slot.baseline_p90_kwh != null) bucket.p90 = (bucket.p90 ?? 0) + slot.baseline_p90_kwh;
    for (const [id, kwh] of Object.entries(slot.planned_kwh)) {
      if (kwh > 0) bucket.loads[id] = (bucket.loads[id] ?? 0) + kwh;
    }
    // `ceiling_kwh` is the window's already, whichever slot of it carries it.
    if (slot.ceiling_kwh != null) bucket.ceiling = slot.ceiling_kwh;
  }
  const priced = prices.map((slot) => ({
    start: Date.parse(slot.start),
    end: Date.parse(slot.end),
    price: Number(slot.total),
    estimated: slot.confidence !== "known",
  }));
  for (const bucket of out) {
    const inside = priced.filter((p) => p.end > bucket.start && p.start < bucket.end && Number.isFinite(p.price));
    if (!inside.length) continue;
    bucket.price = inside.reduce((sum, p) => sum + p.price, 0) / inside.length;
    bucket.estimated = inside.some((p) => p.estimated);
  }
  return out;
}

/** The forecast rail's figures: the rest of the house, each load, and the managed share in cheap hours (F3). */
export function forecastTotals(
  buckets: readonly Bucket[],
  loadIds: readonly string[],
): { other: number; loads: Record<string, number>; managed: number; cheapPct: number | null } {
  const threshold = cheapThreshold(buckets.map((b) => b.price).filter((p): p is number => p !== null));
  const loads: Record<string, number> = {};
  let other = 0;
  let cheap = 0;
  for (const bucket of buckets) {
    other += bucket.baseline ?? 0;
    let managed = 0;
    for (const id of loadIds) {
      const kwh = bucket.loads[id] ?? 0;
      loads[id] = (loads[id] ?? 0) + kwh;
      managed += kwh;
    }
    if (threshold !== null && bucket.price !== null && bucket.price <= threshold) cheap += managed;
  }
  const managed = Object.values(loads).reduce((sum, kwh) => sum + kwh, 0);
  return {
    other,
    loads,
    managed,
    cheapPct: threshold !== null && managed > 0 ? Math.round((cheap / managed) * 100) : null,
  };
}

/** Hours since local midnight in `timeZone` (the browser's when undefined). */
export function localHour(instant: number, timeZone?: string): number {
  const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone }).formatToParts(instant);
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  return part("hour") + part("minute") / 60;
}

/** The next time the wall clock reads `hh:mm[:ss]` after `now`, in `timeZone` (R3's deadline). */
export function nextClock(clock: string, now: number, timeZone?: string): number | null {
  const match = /^(\d{1,2}):(\d{2})/.exec(clock);
  if (!match) return null;
  let hours = Number(match[1]) + Number(match[2]) / 60 - localHour(now, timeZone);
  if (hours <= 0) hours += 24;
  return Math.floor((now + hours * HOUR_MS) / 60_000) * 60_000;
}

/** The start of the local day holding `now`, in `timeZone` (P2's 48 h axis). */
export function localMidnight(now: number, timeZone?: string): number {
  return Math.floor((now - localHour(now, timeZone) * HOUR_MS) / 60_000) * 60_000;
}

function luminance(rgb: readonly number[]): number {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

/** An appliance's icon colour on a dark card: its hue, lifted until it reads ≥ 3 : 1 on #1c1c1c (R1). */
export function readable(hex: string, dark: boolean): string {
  if (!dark || !/^#[0-9a-f]{6}$/i.test(hex)) return hex;
  const card = luminance([28, 28, 28]);
  let rgb = hex.slice(1).match(/../g)!.map((d) => parseInt(d, 16));
  for (let i = 0; i < 12 && (luminance(rgb) + 0.05) / (card + 0.05) < 3; i++) {
    rgb = rgb.map((v) => Math.round(v + (255 - v) * 0.18));
  }
  return `#${rgb.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}
