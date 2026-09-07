// `powerplan-period-summary` (D12 §5.7): the History picker's period. The
// default view is four cells drawn like HA's `statistic` card - cost,
// savings, grid energy, and the capacity step (the month in progress) or the
// highest hour (a day; a longer range: the highest daily peak). `view:
// appliances` draws each appliance's saving as a diverging bar, `view: table`
// cost and saving by appliance with a sum (D5, D6). Every view follows the
// picker through `followPeriod`, fetches long-term statistics once per period
// (and once an hour, as the recorder compiles them), and opens an entity's
// more-info on tap.

import {
  calendarMonth,
  fetchStatistics,
  followPeriod,
  gridHours,
  highest,
  kwhScale,
  monthRanking,
  type Period,
  type StatRow,
  totalChange,
} from "./energy";
import { type HomeAssistant, moreInfo, numeric, timeZone } from "./ha";
import { ppStyles } from "./styles";
import {
  countsDecision,
  formatSummary,
  inMonthOf,
  moneyFormat,
  partyLine,
  periodKind,
  type PeriodKind,
  sumChanges,
  summaryMode,
  type SummaryMode,
  toKw,
  withoutDate,
} from "./transforms";

type Key = "cost" | "savings" | "metric" | "level" | "window_used" | "advice";

interface SummaryLoad {
  id: string;
  name: string;
  color: string;
  cost_month?: string;
  savings_month?: string;
}

interface SummaryConfig {
  entry_id: string;
  view?: "summary" | "appliances" | "table";
  entities?: Partial<Record<Key, string>>;
  grid_entities?: string[];
  loads?: SummaryLoad[];
  currency?: string;
  labels?: Record<string, string>;
}

/** What one fetch found for one period; the cells are drawn from it and the live states. */
interface Fetched {
  period: Period;
  kind: PeriodKind;
  mode: SummaryMode;
  stats: Record<string, StatRow[]>;
  /** The highest hour's (or day's) energy: `window_used`'s `max`, else the grid sources' hourly sum. */
  peak: StatRow | undefined;
  /** For a day: the month's days ranked, `[YYYY-MM-DD, kWh]`. */
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
    const view = config?.view ?? "summary";
    if (view === "summary" && !config?.entities?.cost) throw new Error("powerplan-period-summary needs entities.cost");
    if (view !== "summary" && !config?.loads?.length) throw new Error(`powerplan-period-summary view: ${view} needs loads`);
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
      ...Object.values(config.entities ?? {}).map((id) => (id ? hass.states[id] : undefined)),
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
    return this.config?.view && this.config.view !== "summary" ? 4 : 2;
  }

  public getGridOptions(): Record<string, number | string> {
    return { columns: "full", rows: "auto" };
  }

  private follow(): void {
    this.unfollow = followPeriod(this.hassRef!, (period) => {
      this.period = period;
      void this.load(period);
    });
  }

  private get view(): "summary" | "appliances" | "table" {
    return this.config?.view ?? "summary";
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
    const safe = (promise: Promise<Record<string, StatRow[]>>) => promise.catch(() => ({}) as Record<string, StatRow[]>);

    if (this.view !== "summary") {
      // D5, D6: each appliance's cost and saving over the picked range.
      const ids = (config.loads ?? []).flatMap((load) => [load.cost_month, load.savings_month]).filter((id): id is string => Boolean(id));
      const stats = await safe(fetchStatistics(hass, period, ids, ["change"]));
      if (sequence !== this.sequence) return;
      this.fetched = { period, kind, mode, stats, peak: undefined, since: {} };
      this.key = [];
      this.hass = hass;
      return;
    }

    const entities = config.entities ?? {};
    const { cost, savings, window_used: used } = entities;
    const grid = config.grid_entities ?? [];
    const changes = [cost, savings, ...grid].filter((id): id is string => Boolean(id));
    const [changeStats, maxStats, gridByHour] = await Promise.all([
      safe(fetchStatistics(hass, period, changes, ["change"])),
      used && mode !== "capacity" ? safe(fetchStatistics(hass, period, [used], ["max"])) : Promise.resolve({}),
      mode === "hour" && grid.length ? safe(fetchStatistics(hass, period, grid, ["change"], "hour")) : Promise.resolve({}),
    ]);
    const stats: Record<string, StatRow[]> = { ...changeStats, ...maxStats };
    // D2: before `window_used`'s statistics began, the grid sources' hourly sum is the highest hour.
    const peak =
      (used ? highest(stats[used]) : undefined) ?? highest(gridHours(gridByHour, grid, (id) => kwhScale(hass, id)));

    let ranking: Array<[string, number]> | undefined;
    if (mode === "hour") {
      ranking = await monthRanking(
        hass,
        calendarMonth(period.start),
        { used, grid, advice: entities.advice ? hass.states[entities.advice]?.attributes.items : undefined },
        zone,
        now,
      );
    }

    // A cell with no rows in the period says since when statistics exist, if they do.
    const since: Record<string, number> = {};
    const empty = changes.filter((id) => !stats[id]?.length);
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
    if (this.view === "appliances") this.renderAppliances(hass, config);
    else if (this.view === "table") this.renderTable(hass, config);
    else this.renderSummary(hass, config);
  }

  // ------------------------------------------------------------ the summary

  private renderSummary(hass: HomeAssistant, config: SummaryConfig): void {
    const labels = config.labels ?? {};
    const entities = config.entities ?? {};
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const fetched = this.fetched;
    const state = (key: Key) => {
      const id = entities[key];
      return id ? hass.states[id] : undefined;
    };
    const date = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: zone });
    const isoDate = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" });
    const hour = new Intl.DateTimeFormat(locale, { hour: "2-digit", timeZone: zone });
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
      const id = entities[key];
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
              // D12 §5.13: the month in progress, by party, from the sensor's own split.
              sub:
                fetched?.mode === "capacity"
                  ? partyLine(
                      hass.states[id]?.attributes.by_party as Record<string, string> | undefined,
                      { grid: labels.party_grid, supplier: labels.party_supplier, state: labels.party_state },
                      (v) => formatSummary(v, "money", locale),
                    )
                  : "",
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
              grid.map((id) => [id, fetched.stats[id]?.map((row) => ({ change: (row.change ?? 0) * kwhScale(hass, id) }))]),
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
          ? missing(name, "mdi:flash", [], entities.metric)
          : {
              name,
              icon: "mdi:flash",
              value: formatSummary(toKw(metric, metricEntity?.attributes.unit_of_measurement), "kw", locale),
              unit: "kW",
              sub: state("level")?.state ?? "",
              entity: entities.metric,
            },
      );
    } else if (entities.window_used || grid.length) {
      const used = entities.window_used;
      const name = labels.summary_highest_hour ?? "";
      const peak = fetched?.peak;
      if (peak?.max === null || peak?.max === undefined) {
        cells.push(missing(name, "mdi:flash", [used], used));
      } else {
        let sub = "";
        if (fetched!.mode === "hour") {
          const decision = countsDecision(peak.max, fetched!.ranking ?? []);
          const verdict = decision.counts
            ? (labels.counts ?? "")
            : fill(labels.not_counts ?? "", {
                date: isoDate.format(Date.parse(decision.third![0])),
                kw: formatSummary(decision.third![1], "kw", locale),
              });
          sub = `${hour.format(peak.start)}–${hour.format(peak.end)} · ${verdict}`;
        }
        cells.push({ name, icon: "mdi:flash", value: formatSummary(peak.max, "kw", locale), unit: "kWh", sub, entity: used });
      }
    }

    const subtitle = fetched ? this.subtitle(fetched, locale, zone) : "";
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        ha-card { container-type: inline-size; }
        .pp-content { padding-bottom: 0; }
        .period { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); min-height: 16px; }
        .grid { display: grid; grid-template-columns: repeat(${Math.max(cells.length, 1)}, minmax(0, 1fr)); margin: 8px -16px 0; }
        .cell { display: flex; flex-direction: column; gap: 4px; min-width: 0; padding: 16px; cursor: pointer;
                border-left: 1px solid var(--divider-color); }
        .cell:first-child { border-left: 0; }
        .sub { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
        @container (max-width: 600px) {
          .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
          .cell:nth-child(odd) { border-left: 0; }
          .cell:nth-child(n + 3) { border-top: 1px solid var(--divider-color); }
        }
      </style>
      <ha-card><div class="pp-content">
        <div class="period">${escape(subtitle)}</div>
        <div class="grid">${cells
          .map(
            (cell) => `
          <div class="cell" ${cell.entity ? `data-entity="${escape(cell.entity)}" role="button" tabindex="0"` : ""}>
            <div class="pp-stat-head"><span class="pp-stat-name">${escape(cell.name)}</span><ha-icon icon="${cell.icon}"></ha-icon></div>
            <div class="pp-stat-value">${escape(cell.value)}${cell.unit ? `<span class="pp-stat-unit">${escape(cell.unit)}</span>` : ""}</div>
            ${cell.sub ? `<div class="sub">${escape(cell.sub)}</div>` : ""}
          </div>`,
          )
          .join("")}</div>
      </div></ha-card>`;
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

  // ------------------------------------------------- per appliance (D5, D6)

  private totals(config: SummaryConfig): Array<SummaryLoad & { cost: number; saved: number }> {
    const stats = this.fetched?.stats ?? {};
    return (config.loads ?? []).map((load) => ({
      ...load,
      cost: load.cost_month ? (totalChange(stats[load.cost_month]) ?? 0) : 0,
      saved: load.savings_month ? (totalChange(stats[load.savings_month]) ?? 0) : 0,
    }));
  }

  private signed(value: number, locale: string): string {
    const text = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(value);
    return text.replace("-", "−");
  }

  private renderAppliances(hass: HomeAssistant, config: SummaryConfig): void {
    const locale = hass.locale.language;
    const rows = this.totals(config)
      .filter((load) => load.savings_month)
      .sort((a, b) => b.saved - a.saved);
    const most = Math.max(...rows.map((row) => Math.abs(row.saved)), 0.01);
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        .bar { position: relative; flex: 0 0 38%; height: 8px; }
        .bar::before { content: ""; position: absolute; left: 50%; top: -4px; bottom: -4px;
                       border-left: 1px solid var(--divider-color); }
        .bar span { position: absolute; top: 0; height: 8px; border-radius: 4px; opacity: 0.8; }
        .pp-num { min-width: 56px; }
      </style>
      <ha-card><div class="pp-content"><div class="pp-list">${
        this.fetched
          ? rows
              .map((row) => {
                const share = (50 * Math.abs(row.saved)) / most;
                const side =
                  row.saved >= 0
                    ? `left:50%;width:${share.toFixed(1)}%;background:var(--success-color)`
                    : `right:50%;width:${share.toFixed(1)}%;background:var(--error-color)`;
                return `<div class="pp-row" data-entity="${escape(row.savings_month!)}" role="button" tabindex="0">
                  <span class="pp-dot" style="background:${escape(row.color)}"></span>
                  <span class="pp-name">${escape(row.name)}</span>
                  <span class="bar"><span style="${side}"></span></span>
                  <span class="pp-num">${escape(this.signed(row.saved, locale))}</span></div>`;
              })
              .join("")
          : ""
      }</div></div></ha-card>`;
  }

  private renderTable(hass: HomeAssistant, config: SummaryConfig): void {
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const money = moneyFormat(locale, config.currency ?? "");
    const rows = this.totals(config).sort((a, b) => b.cost - a.cost);
    const cost = rows.reduce((sum, row) => sum + row.cost, 0);
    const saved = rows.reduce((sum, row) => sum + row.saved, 0);
    const line = (name: string, c: number, s: number, extra = "", entity?: string) =>
      `<tr class="${extra}" ${entity ? `data-entity="${escape(entity)}"` : ""}><td>${name}</td><td class="num">${escape(money.format(c).replace("-", "−"))}</td><td class="num">${escape(this.signed(s, locale))}</td></tr>`;
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        td .pp-dot { display: inline-block; margin-right: 10px; vertical-align: 1px; }
        tr[data-entity] { cursor: pointer; }
      </style>
      <ha-card><div class="pp-content">${
        this.fetched
          ? `<table class="pp-table">
          <tr><th>${escape(labels.appliance ?? "")}</th><th class="num">${escape(labels.cost ?? "")}</th><th class="num">${escape(labels.saved ?? "")}</th></tr>
          ${rows.map((row) => line(`<span class="pp-dot" style="background:${escape(row.color)}"></span>${escape(row.name)}`, row.cost, row.saved, "", row.cost_month)).join("")}
          ${line(escape(labels.total ?? ""), cost, saved, "sum")}
        </table>
        <div class="pp-caption">${escape(labels.negative_saving ?? "")}</div>`
          : ""
      }</div></ha-card>`;
  }
}
