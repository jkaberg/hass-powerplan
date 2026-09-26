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
import { savingsView } from "./r3-util";
import { ChartTip, MARK_CSS, markSwatch, ring, type Slice, tipAttr } from "./marks";
import {
  countsDecision,
  type EnergyLoad,
  energySlices,
  formatSummary,
  inMonthOf,
  moneyFormat,
  periodKind,
  previousPeriod,
  versus,
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
  /** `view: energy`: the appliance's `energy` total (D12 §5.20 V5). */
  energy?: string;
}

interface SummaryConfig {
  entry_id: string;
  view?: "summary" | "appliances" | "table" | "energy";
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
  /** H4: the same statistics over the same time of the period before (D12 §5.21). */
  previous?: Record<string, StatRow[]>;
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
  private tip?: ChartTip;

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

  private get view(): "summary" | "appliances" | "table" | "energy" {
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
      const ids = (this.view === "energy"
        ? [...(config.loads ?? []).map((load) => load.energy), ...(config.grid_entities ?? [])]
        : (config.loads ?? []).flatMap((load) => [load.cost_month, load.savings_month])
      ).filter((id): id is string => Boolean(id));
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
    // Iteration 5: the highest hour is only this card's without `window_used` (the peaks card has it
    // otherwise); then it is the grid sources' hourly sum (D2).
    const ownPeak = !used && mode !== "capacity";
    const before = previousPeriod(period, now.getTime(), zone);
    const [stats, gridByHour, previous] = await Promise.all([
      safe(fetchStatistics(hass, period, changes, ["change"])),
      ownPeak && mode === "hour" && grid.length ? safe(fetchStatistics(hass, period, grid, ["change"], "hour")) : Promise.resolve({}),
      before.end > before.start ? safe(fetchStatistics(hass, before, [cost, ...grid].filter((id): id is string => Boolean(id)), ["change"])) : Promise.resolve({}),
    ]);
    const peak = ownPeak ? highest(gridHours(gridByHour, grid, (id) => kwhScale(hass, id))) : undefined;

    let ranking: Array<[string, number]> | undefined;
    if (ownPeak && mode === "hour") {
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
    this.fetched = { period, kind, mode, stats, peak, ranking, since, previous };
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
    if (this.view === "energy") this.renderEnergy(hass, config);
    else if (this.view === "appliances") this.renderAppliances(hass, config);
    else if (this.view === "table") this.renderTable(hass, config);
    else this.renderSummary(hass, config);
  }

  // ------------------------------------------------------------ the summary

  /** H4: "↓ 12 % mot samme tid i forrige periode", or "" without a previous figure (D-0699). */
  private against(now: number | null, before: number | null, labels: Record<string, string>, locale: string): string {
    const change = versus(now, before);
    if (change === null) return "";
    const arrow = change > 0 ? "↑" : change < 0 ? "↓" : "→";
    return fill(labels.vs_previous ?? "{arrow} {pct} %", { arrow, pct: new Intl.NumberFormat(locale).format(Math.abs(change)) });
  }

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
      // As Now's month card (§5.17 C5): no savings figure while the reference says it has none.
      if (key === "savings" && savingsView(hass.states[id], entities.cost ? hass.states[entities.cost] : undefined).missing) continue;
      const total = totalChange(fetched?.stats[id]);
      cells.push(
        total === null
          ? missing(labels[key] ?? "", icon, [id], id)
          : {
              name: labels[key] ?? "",
              icon,
              value: formatSummary(total, "money", locale),
              unit: String(hass.states[id]?.attributes.unit_of_measurement ?? ""),
              sub: key === "cost" ? this.against(total, totalChange(fetched?.previous?.[id]), labels, locale) : "",
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
          : {
              name, icon: "mdi:transmission-tower", value: formatSummary(kwh, "kwh", locale), unit: "kWh", entity,
              sub: this.against(kwh, fetched?.previous && grid.some((id) => fetched.previous![id]?.length)
                ? grid.reduce((sum, id) => sum + (totalChange(fetched.previous![id]) ?? 0) * kwhScale(hass, id), 0) : null, labels, locale),
            },
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
    } else if (!entities.window_used && grid.length) {
      // Iteration 5: with `window_used` the peaks card beside this one shows the highest hour, the top 3
      // and whether it counts; the summary no longer repeats it.
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

    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        ha-card { container-type: inline-size; }
        .pp-content { padding-bottom: 0; }
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

  /** Whether the picker shows the month in progress: month-to-date attributes only speak for it. */
  private get thisMonth(): boolean {
    const now = Date.now();
    return Boolean(this.period && +this.period.start <= now && now < +this.period.end && +this.period.end - +this.period.start > 27 * 86_400_000);
  }

  /** V5 (D12 §5.20): who used the period's energy, and - this month - how much of it PowerPlan moved. */
  private renderEnergy(hass: HomeAssistant, config: SummaryConfig): void {
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const stats = this.fetched?.stats ?? {};
    const kwh0 = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
    const kwh1 = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
    const grid = (config.grid_entities ?? []).length
      ? (config.grid_entities ?? []).reduce((sum, id) => sum + (totalChange(stats[id]) ?? 0) * kwhScale(hass, id), 0)
      : null;
    const loads: EnergyLoad[] = (config.loads ?? []).map((load) => ({
      id: load.id, name: load.name, color: load.color,
      kwh: load.energy ? (totalChange(stats[load.energy]) ?? 0) * kwhScale(hass, load.energy) : 0,
      moved: this.thisMonth && load.savings_month ? Number(hass.states[load.savings_month]?.attributes.kwh_shifted) || 0 : null,
    }));
    const slices = energySlices(loads, grid && grid > 0 ? grid : null, (n) => fill(labels.other_appliances ?? "{n}", { n: String(n) }), labels.rest_of_house ?? "");
    const total = slices.reduce((a, x) => a + x.kwh, 0);
    const appliances = slices.filter((x) => x.key !== "rest").reduce((a, x) => a + x.kwh, 0);
    const moved = slices.reduce((a, x) => a + x.moved, 0);
    const pct = (v: number) => `${kwh0.format(total > 0 ? (100 * v) / total : 0)} %`;
    const ringSlices: Slice[] = slices.map((x) => ({
      value: x.kwh, color: x.color, moved: x.moved,
      lines: [x.name, `${kwh1.format(x.kwh)} kWh · ${pct(x.kwh)}`, ...(x.moved > 0.05 ? [fill(labels.moved_of ?? "", { moved: kwh1.format(x.moved), kwh: kwh1.format(x.kwh) })] : [])],
      movedLines: ["PowerPlan · " + x.name, fill(labels.moved_of ?? "", { moved: kwh1.format(x.moved), kwh: kwh1.format(x.kwh) })],
    }));
    const hasRest = slices.some((x) => x.key === "rest");
    const center: [string, string] = hasRest ? [pct(appliances), labels.controlled ?? ""] : [kwh0.format(appliances), "kWh"];
    // Stacked under 420 px by a container query: this card draws once per period, not per resize.
    const size = 140;
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        ${MARK_CSS}
        ha-card { position: relative; container-type: inline-size; }
        .split { display: flex; gap: 16px; align-items: center; }
        @container (max-width: 419px) { .split { flex-direction: column; align-items: stretch; } .split .pp-ring { align-self: center; } }
        .rows { flex: 1; min-width: 0; display: grid; }
        .row { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto 38px; gap: 8px; align-items: center; min-height: 28px;
               font-size: 13px; border-bottom: 1px solid var(--divider-color); }
        .row:last-child { border-bottom: 0; }
        .row i { width: 10px; height: 10px; border-radius: 3px; }
        .row .n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .row .v { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
        .row .p { text-align: right; font-size: 12px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
        .note { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--secondary-text-color); margin-top: 8px; }
      </style>
      <ha-card><div class="pp-content">${
        this.fetched && total > 0
          ? `<div class="pp-sub">${escape(fill(labels.energy_total ?? "{kwh}", { kwh: kwh0.format(total) }))}</div>
            <div class="split">${ring(size, 14, ringSlices, center, `${center[0]} ${center[1]}`)}
            <div class="rows">${slices.map((x, i) => `<div class="row"${tipAttr(ringSlices[i]!.lines)}><i style="background:${x.color}"></i><span class="n">${escape(x.name)}</span><span class="v">${escape(kwh0.format(x.kwh))} kWh</span><span class="p">${escape(pct(x.kwh))}</span></div>`).join("")}</div></div>
            ${moved > 0.05 ? `<div class="note">${markSwatch}<span>${escape(fill(labels.moved_total ?? "{kwh}", { kwh: kwh0.format(moved) }))}</span></div>` : ""}`
          : this.fetched ? `<div class="pp-empty">${escape(withoutDate(labels.collecting ?? ""))}</div>` : ""
      }</div></ha-card>`;
    this.tip ??= new ChartTip(this.shadowRoot!, () => this.shadowRoot!.querySelector("ha-card"));
    this.tip.reset();
  }

  private renderTable(hass: HomeAssistant, config: SummaryConfig): void {
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const money = moneyFormat(locale, config.currency ?? "");
    const rows = this.totals(config).sort((a, b) => b.cost - a.cost);
    const cost = rows.reduce((sum, row) => sum + row.cost, 0);
    const saved = rows.reduce((sum, row) => sum + row.saved, 0);
    // D12 §5.19: the kWh each appliance moved - a month-to-date attribute, so only while the picker shows this month.
    const thisMonth = this.thisMonth;
    const kwh = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
    const moved = (row?: SummaryLoad) => {
      if (!thisMonth) return "";
      const value = row?.savings_month ? Number(hass.states[row.savings_month]?.attributes.kwh_shifted) : NaN;
      return `<td class="num moved">${row && Number.isFinite(value) ? escape(`${kwh.format(value)} kWh`) : ""}</td>`;
    };
    const line = (name: string, c: number, s: number, extra = "", entity?: string, row?: SummaryLoad) =>
      `<tr class="${extra}" ${entity ? `data-entity="${escape(entity)}"` : ""}><td>${name}</td>${moved(row)}<td class="num">${escape(money.format(c).replace("-", "−"))}</td><td class="num">${escape(this.signed(s, locale))}</td></tr>`;
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        td .pp-dot { display: inline-block; margin-right: 10px; vertical-align: 1px; }
        tr[data-entity] { cursor: pointer; }
        ha-card { container-type: inline-size; }
        @container (max-width: 499px) { .moved { display: none; } }
      </style>
      <ha-card><div class="pp-content">${
        this.fetched
          ? `<table class="pp-table">
          <tr><th>${escape(labels.appliance ?? "")}</th>${thisMonth ? `<th class="num moved">${escape(labels.moved ?? "")}</th>` : ""}<th class="num">${escape(labels.cost ?? "")}</th><th class="num">${escape(labels.saved ?? "")}</th></tr>
          ${rows.map((row) => line(`<span class="pp-dot" style="background:${escape(row.color)}"></span>${escape(row.name)}`, row.cost, row.saved, "", row.cost_month, row)).join("")}
          ${line(escape(labels.total ?? ""), cost, saved, "sum")}
        </table>
        <div class="pp-caption">${escape(labels.negative_saving ?? "")}</div>`
          : ""
      }</div></ha-card>`;
  }
}
