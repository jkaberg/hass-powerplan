// `powerplan-period-summary` (D12 §5.7): the History picker's period in four
// cells drawn like HA's `statistic` card - cost, savings, grid energy, and
// the capacity step (the month in progress) or the highest hour (a day; a
// longer range: the highest daily peak). It follows the picker through
// `followPeriod`, fetches long-term statistics once per period (and once an
// hour, as the recorder compiles them), and opens an entity's more-info on tap.

import { calendarMonth, fetchStatistics, followPeriod, highest, type Period, type StatRow, totalChange } from "./energy";
import { type HomeAssistant, moreInfo, numeric, timeZone } from "./ha";
import {
  countsDecision,
  formatSummary,
  inMonthOf,
  periodKind,
  type PeriodKind,
  sumChanges,
  summaryMode,
  type SummaryMode,
  toKw,
  topEntries,
  withoutDate,
} from "./transforms";

type Key = "cost" | "savings" | "metric" | "level" | "window_used" | "advice";

interface SummaryConfig {
  entry_id: string;
  entities: Partial<Record<Key, string>>;
  grid_entities?: string[];
  labels?: Record<string, string>;
}

/** What one fetch found for one period; the cells are drawn from it and the live states. */
interface Fetched {
  period: Period;
  kind: PeriodKind;
  mode: SummaryMode;
  stats: Record<string, StatRow[]>;
  /** The highest hour's (or day's) `max` of `window_used`, in kWh. */
  peak: StatRow | undefined;
  /** For a day outside the month in progress: that month's daily peaks as `[day, kW]`. */
  ranking?: Array<[string, number]>;
  /** The earliest statistics row after the period, by id, for a cell that has none. */
  since: Record<string, number>;
}

interface Cell {
  name: string;
  icon: string;
  value: string;
  unit: string;
  sub: string;
  entity?: string;
}

const HOUR_MS = 3_600_000;

const escape = (value: string) =>
  value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

const fill = (text: string, values: Record<string, string>) =>
  text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);

/** Energy statistics in kWh, whatever unit the source entity reports. */
const toKwh = (value: number, unit: unknown) => (unit === "Wh" ? value / 1000 : unit === "MWh" ? value * 1000 : value);

export class PowerplanPeriodSummary extends HTMLElement {
  private config?: SummaryConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];
  private unfollow?: () => void;
  private period?: Period;
  private fetched?: Fetched;
  private fetchedHour = 0;
  private sequence = 0;

  public setConfig(config: SummaryConfig): void {
    if (!config?.entities?.cost) throw new Error("powerplan-period-summary needs entities.cost");
    this.config = config;
    this.key = [];
    this.fetched = undefined;
    if (this.period) void this.load(this.period);
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    const config = this.config;
    if (!config) return;
    if (!this.unfollow && this.isConnected) this.follow();
    // The recorder compiles statistics each hour: fetch the period again then.
    if (this.period && Math.floor(Date.now() / HOUR_MS) !== this.fetchedHour) void this.load(this.period);
    const key = [
      ...Object.values(config.entities).map((id) => (id ? hass.states[id] : undefined)),
      hass.language,
      hass.themes.darkMode,
      this.fetched,
    ];
    if (key.length === this.key.length && key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.render();
  }

  public connectedCallback(): void {
    if (this.hassRef && this.config) this.follow();
  }

  public disconnectedCallback(): void {
    this.unfollow?.();
    this.unfollow = undefined;
  }

  public getCardSize(): number {
    return 2;
  }

  public getGridOptions(): { columns: "full"; rows: number; min_rows: number } {
    return { columns: "full", rows: 2, min_rows: 2 };
  }

  private follow(): void {
    this.unfollow = followPeriod(this.hassRef!, (period) => {
      this.period = period;
      void this.load(period);
    });
  }

  private async load(period: Period): Promise<void> {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    const sequence = ++this.sequence;
    this.fetchedHour = Math.floor(Date.now() / HOUR_MS);
    const zone = timeZone(hass);
    const now = new Date();
    const kind = periodKind(period, zone);
    const mode = summaryMode(kind, inMonthOf(period.start, now, zone));
    const { cost, savings, window_used: used } = config.entities;
    const changes = [cost, savings, ...(config.grid_entities ?? [])].filter((id): id is string => Boolean(id));
    const safe = (promise: Promise<Record<string, StatRow[]>>) => promise.catch(() => ({}) as Record<string, StatRow[]>);

    const [changeStats, maxStats] = await Promise.all([
      safe(fetchStatistics(hass, period, changes, ["change"])),
      used && mode !== "capacity" ? safe(fetchStatistics(hass, period, [used], ["max"])) : Promise.resolve({}),
    ]);
    const stats: Record<string, StatRow[]> = { ...changeStats, ...maxStats };
    const peak = used ? highest(stats[used]) : undefined;

    // A day outside the month in progress: rank that month's days ourselves.
    let ranking: Array<[string, number]> | undefined;
    if (mode === "hour" && used && !inMonthOf(period.start, now, zone)) {
      const month = calendarMonth(period.start);
      const daily = (await safe(fetchStatistics(hass, month, [used], ["max"], "day")))[used] ?? [];
      const day = new Intl.DateTimeFormat(hass.locale.language, { day: "numeric", month: "short", timeZone: zone });
      ranking = daily
        .filter((row) => row.max !== null && row.max !== undefined)
        .map((row) => [day.format(row.start), row.max!] as [string, number]);
    }

    // A cell with no rows in the period says since when statistics exist, if they do.
    const since: Record<string, number> = {};
    const empty = [...changes, ...(used && mode !== "capacity" ? [used] : [])].filter((id) => !stats[id]?.length);
    if (empty.length && period.end < now) {
      const after = await safe(fetchStatistics(hass, { start: period.end, end: now }, empty, ["change", "max"], "day"));
      for (const id of empty) {
        const first = after[id]?.[0];
        if (first) since[id] = first.start;
      }
    }

    if (sequence !== this.sequence) return;
    this.fetched = { period, kind, mode, stats, peak, ranking, since };
    this.key = [];
    this.hass = hass;
  }

  private render(): void {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      const open = (event: Event) => {
        const cell = (event.target as HTMLElement).closest<HTMLElement>("[data-entity]");
        if (cell?.dataset.entity) moreInfo(this, cell.dataset.entity);
      };
      this.shadowRoot!.addEventListener("click", open);
      this.shadowRoot!.addEventListener("keydown", (event) => {
        if ((event as KeyboardEvent).key === "Enter") open(event);
      });
    }
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const fetched = this.fetched;
    const state = (key: Key) => {
      const id = config.entities[key];
      return id ? hass.states[id] : undefined;
    };
    const date = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: zone });
    const collecting = (ids: Array<string | undefined>) => {
      const found = ids.map((id) => (id ? fetched?.since[id] : undefined)).filter((t): t is number => t !== undefined);
      const label = labels.collecting ?? "";
      return found.length ? fill(label, { date: date.format(Math.min(...found)) }) : withoutDate(label);
    };
    const missing = (name: string, icon: string, ids: Array<string | undefined>, entity?: string): Cell => ({
      name,
      icon,
      value: "–",
      unit: "",
      sub: fetched ? collecting(ids) : "",
      entity,
    });

    const cells: Cell[] = [];
    // Cost and savings: the period's change, in the entity's currency.
    for (const [key, icon] of [
      ["cost", "mdi:cash"],
      ["savings", "mdi:piggy-bank"],
    ] as const) {
      const id = config.entities[key];
      if (!id) continue;
      const total = totalChange(fetched?.stats[id]);
      cells.push(
        total === null
          ? missing(labels[key] ?? "", icon, [id], id)
          : {
              name: labels[key] ?? "",
              icon,
              value: formatSummary(total, "money", locale),
              unit: String(hass.states[id]?.attributes.unit_of_measurement ?? ""),
              sub: "",
              entity: id,
            },
      );
    }

    // Grid energy: Σ over the Energy dashboard's grid sources, in kWh.
    const grid = config.grid_entities ?? [];
    if (grid.length) {
      const name = labels.summary_grid_energy ?? "";
      const kwh = fetched
        ? sumChanges(
            Object.fromEntries(
              grid.map((id) => [
                id,
                fetched.stats[id]?.map((row) => ({
                  change: toKwh(row.change ?? 0, hass.states[id]?.attributes.unit_of_measurement),
                })),
              ]),
            ),
            grid,
          )
        : null;
      const entity = grid.find((id) => hass.states[id]);
      cells.push(
        kwh === null
          ? missing(name, "mdi:transmission-tower", grid, entity)
          : { name, icon: "mdi:transmission-tower", value: formatSummary(kwh, "kwh", locale), unit: "kWh", sub: "", entity },
      );
    }

    // The last cell: the capacity step this month, else the highest hour or day.
    if (fetched?.mode === "capacity") {
      const name = labels.summary_capacity ?? "";
      const metricEntity = state("metric");
      const metric = numeric(metricEntity);
      cells.push(
        metric === null
          ? missing(name, "mdi:flash", [], config.entities.metric)
          : {
              name,
              icon: "mdi:flash",
              value: formatSummary(toKw(metric, metricEntity?.attributes.unit_of_measurement), "kw", locale),
              unit: "kW",
              sub: state("level")?.state ?? "",
              entity: config.entities.metric,
            },
      );
    } else if (config.entities.window_used) {
      const used = config.entities.window_used;
      const name = labels.summary_highest_hour ?? "";
      const peak = fetched?.peak?.max;
      if (peak === null || peak === undefined) {
        cells.push(missing(name, "mdi:flash", [used], used));
      } else {
        let sub = "";
        if (fetched!.mode === "hour") {
          const ranking =
            fetched!.ranking ??
            topEntries(state("advice")?.attributes.items).map(
              ([day, kw]) =>
                [new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" }).format(Date.parse(day)), kw] as [
                  string,
                  number,
                ],
            );
          const decision = countsDecision(peak, ranking);
          sub = decision.counts
            ? (labels.counts ?? "")
            : fill(labels.not_counts ?? "", {
                date: decision.third![0],
                kw: formatSummary(decision.third![1], "kw", locale),
              });
        }
        cells.push({ name, icon: "mdi:flash", value: formatSummary(peak, "kw", locale), unit: "kWh", sub, entity: used });
      }
    }

    const subtitle = fetched ? this.subtitle(fetched, locale, zone) : "";
    this.shadowRoot!.innerHTML = `
      <style>
        :host { display: block; height: 100%; }
        ha-card { height: 100%; box-sizing: border-box; padding: 12px 16px 16px; container-type: inline-size;
                  display: flex; flex-direction: column; gap: 8px; }
        .period { font-size: 14px; color: var(--secondary-text-color); min-height: 20px; }
        .grid { display: grid; grid-template-columns: repeat(${Math.max(cells.length, 1)}, minmax(0, 1fr)); gap: 16px; }
        @container (max-width: 500px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
        .cell { display: flex; flex-direction: column; min-width: 0; cursor: pointer; }
        .header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
        .name { font-size: 14px; color: var(--secondary-text-color); line-height: 20px;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .header ha-icon { color: var(--state-icon-color, var(--paper-item-icon-color, #44739e)); flex: none;
                          --mdc-icon-size: 24px; }
        .info { display: flex; align-items: baseline; gap: 4px; margin-top: 4px; }
        .value { font-size: 28px; line-height: 1.1; color: var(--primary-text-color); white-space: nowrap; }
        .unit { font-size: 14px; color: var(--primary-text-color); }
        .sub { font-size: 12px; color: var(--secondary-text-color); margin-top: 2px; }
      </style>
      <ha-card>
        <div class="period">${escape(subtitle)}</div>
        <div class="grid">${cells
          .map(
            (cell) => `
          <div class="cell" ${cell.entity ? `data-entity="${escape(cell.entity)}" role="button" tabindex="0"` : ""}>
            <div class="header"><span class="name">${escape(cell.name)}</span><ha-icon icon="${cell.icon}"></ha-icon></div>
            <div class="info"><span class="value">${escape(cell.value)}</span>${cell.unit ? `<span class="unit">${escape(cell.unit)}</span>` : ""}</div>
            ${cell.sub ? `<div class="sub">${escape(cell.sub)}</div>` : ""}
          </div>`,
          )
          .join("")}</div>
      </ha-card>`;
  }

  /** "tirsdag 22. september", "september 2026", or "1. sep. – 23. sep." for anything else. */
  private subtitle(fetched: Fetched, locale: string, zone: string | undefined): string {
    const { period, kind } = fetched;
    if (kind === "day") {
      return new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long", timeZone: zone }).format(period.start);
    }
    if (kind === "month") {
      return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric", timeZone: zone }).format(period.start);
    }
    const format = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric", timeZone: zone });
    return `${format.format(period.start)} – ${format.format(new Date(period.end.getTime() - 1))}`;
  }
}
