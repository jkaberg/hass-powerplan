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
  /** The arc's full scale in kWh: the ceiling, or what the window will reach without one. */
  max: number;
  used: number;
  needle: number | null;
  over: boolean;
}

/** Scale used and projected kWh against the ceiling; each fraction is clamped to the arc. */
export function gauge(used: number, projected: number | null, ceiling: number | null): Gauge {
  const hasCeiling = ceiling !== null && Number.isFinite(ceiling) && ceiling > 0;
  const max = hasCeiling ? ceiling : Math.max(used, projected ?? 0, 1);
  const fraction = (value: number) => Math.min(Math.max(value / max, 0), 1);
  return {
    max,
    used: fraction(used),
    needle: projected === null ? null : fraction(projected),
    over: hasCeiling && Math.max(used, projected ?? 0) > ceiling,
  };
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
