// The History view's period picker, as the cards that follow it read it
// (D12 §5.7). HA keeps each energy collection on the connection under
// `_<collection key>` (the 2026.9 frontend, `20260826.7`); `subscribe` hands
// every change of period to the callback. The property is HA-internal, so a
// card that cannot find it within 5 s uses the calendar month instead.

import type { HomeAssistant } from "./ha";

export const COLLECTION_KEY = "energy_powerplan";
/** How long a card waits for the picker's collection before it falls back. */
export const FALLBACK_MS = 5_000;

export interface Period {
  start: Date;
  end: Date;
}

interface EnergyCollection {
  subscribe(callback: (data: { start: Date; end?: Date }) => void): () => void;
}

/** The picker's collection, or `undefined` while HA has not made it (it does on the picker's first render). */
export function energyCollection(hass: HomeAssistant, key = COLLECTION_KEY): EnergyCollection | undefined {
  const connection = (hass as unknown as { connection?: Record<string, unknown> }).connection;
  const collection = connection?.[`_${key}`] as EnergyCollection | undefined;
  return typeof collection?.subscribe === "function" ? collection : undefined;
}

/** The calendar month around `now`, in the browser's zone: the fallback period. */
export function calendarMonth(now: Date): Period {
  return {
    start: new Date(now.getFullYear(), now.getMonth(), 1),
    end: new Date(now.getFullYear(), now.getMonth() + 1, 1),
  };
}

/** The statistics period for a range: ≤ 2 days by the hour, ≤ 35 days by the day, else by the month. */
export function statisticsPeriod(period: Period): "hour" | "day" | "month" {
  const days = (period.end.getTime() - period.start.getTime()) / 86_400_000;
  return days <= 2 ? "hour" : days <= 35 ? "day" : "month";
}

/**
 * Follow the picker: `onPeriod` gets every period it sets, or the calendar
 * month when no picker appears within `FALLBACK_MS`. Returns the unsubscribe.
 */
export function followPeriod(hass: HomeAssistant, onPeriod: (period: Period) => void): () => void {
  let unsubscribe: (() => void) | undefined;
  let stopped = false;
  const attach = (): boolean => {
    const collection = energyCollection(hass);
    if (!collection) return false;
    unsubscribe = collection.subscribe((data) => {
      const end = data.end ?? new Date(data.start.getTime() + 86_400_000);
      onPeriod({ start: data.start, end });
    });
    return true;
  };
  if (!attach()) {
    const started = Date.now();
    const poll = window.setInterval(() => {
      if (stopped || attach()) window.clearInterval(poll);
      else if (Date.now() - started >= FALLBACK_MS) {
        window.clearInterval(poll);
        onPeriod(calendarMonth(new Date()));
      }
    }, 250);
    unsubscribe = () => window.clearInterval(poll);
  }
  return () => {
    stopped = true;
    unsubscribe?.();
  };
}

/** One row of `recorder/statistics_during_period`. */
export interface StatRow {
  start: number;
  end: number;
  change?: number | null;
  max?: number | null;
  mean?: number | null;
  sum?: number | null;
}

/** Fetch long-term statistics for `ids` over `period`, keyed by statistic id. */
export async function fetchStatistics(
  hass: HomeAssistant,
  period: Period,
  ids: string[],
  types: Array<"change" | "max" | "mean" | "sum">,
  grain: "hour" | "day" | "month" = statisticsPeriod(period),
): Promise<Record<string, StatRow[]>> {
  if (!ids.length) return {};
  return hass.callWS<Record<string, StatRow[]>>({
    type: "recorder/statistics_during_period",
    start_time: period.start.toISOString(),
    end_time: period.end.toISOString(),
    statistic_ids: ids,
    period: grain,
    types,
  });
}

/** Σ `change` over the rows: a total over the period. */
export function totalChange(rows: readonly StatRow[] | undefined): number | null {
  if (!rows?.length) return null;
  return rows.reduce((sum, row) => sum + (row.change ?? 0), 0);
}

/** The highest `max` over the rows, with the row it came from. */
export function highest(rows: readonly StatRow[] | undefined): StatRow | undefined {
  return rows?.reduce<StatRow | undefined>((best, row) => ((row.max ?? -Infinity) > (best?.max ?? -Infinity) ? row : best), undefined);
}
