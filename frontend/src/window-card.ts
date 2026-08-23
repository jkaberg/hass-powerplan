// `powerplan-window-card` (D12 §5.3): the capacity window. `mode: hour` -
// this hour's used kWh against the ceiling, the projection as a paler arc
// with a tick at its end, a status chip in the ladder stage's colour, and a
// footer of three facts. `mode: month` - the period's metric on the tariff's
// steps, the month's top three days and what separates it from the next step.
// `mode: peaks` (History) - each day's highest hour over the picker's period,
// the days that count marked; on a single day, whether that day counts.
// Drawn in SVG at the card's own pixel width, so text keeps HA's type scale
// (G2) and the whole arc and its footer fit a phone. Tapping opens the
// more-info dialog of what the gauge shows.

import {
  dailyPeaks,
  fetchStatistics,
  followPeriod,
  gridHours,
  kwhScale,
  monthRanking,
  type Period,
  type StatRow,
} from "./energy";
import { cssVar, type HomeAssistant, moreInfo, numeric, timeZone } from "./ha";
import { ppStyles } from "./styles";
import {
  adviceItem,
  arcPoint,
  countingDays,
  countsDecision,
  type DayPeak,
  dayKey,
  gauge,
  gaugeFont,
  moneyFormat,
  monthGauge,
  niceScale,
  stageTone,
  stageWord,
  type TariffStep,
  targetStep,
  toKw,
  TONE_COLOR,
  topEntries,
  withoutDate,
} from "./transforms";

type Key =
  | "window_used"
  | "window_projected"
  | "ceiling"
  | "allowance"
  | "stage"
  | "peak_warning"
  | "next_peak_warning"
  | "metric"
  | "level"
  | "projected_level"
  | "advice"
  | "target";

interface WindowConfig {
  entry_id: string;
  mode?: "hour" | "month" | "peaks";
  entities: Partial<Record<Key, string>>;
  /** `mode: peaks`: the Energy preferences' grid sources, for days before `window_used`'s statistics. */
  grid_entities?: string[];
  labels?: Record<string, string>;
}

/** The hour arc's stroke (H4) and the month arc's (D12 §5.3). */
const STROKE = 24;
const MONTH_STROKE = 16;

function arc(cx: number, cy: number, from: number, to: number, r: number): string {
  const [x0, y0] = arcPoint(from, r);
  const [x1, y1] = arcPoint(to, r);
  return `M ${(cx + x0).toFixed(2)} ${(cy + y0).toFixed(2)} A ${r} ${r} 0 0 1 ${(cx + x1).toFixed(2)} ${(cy + y1).toFixed(2)}`;
}

const escape = (value: string) =>
  value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

const fill = (text: string, values: Record<string, string>) =>
  text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);

export class PowerplanWindowCard extends HTMLElement {
  private config?: WindowConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];
  private width = 0;
  private resize?: ResizeObserver;
  private period?: Period;
  private unfollow?: () => void;
  private fetched?: {
    period: Period;
    days: DayPeak[];
    hours: StatRow[];
    ranking: Array<[string, number]>;
    ceiling: number | null;
  };

  public connectedCallback(): void {
    this.resize = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (Math.abs(width - this.width) < 1) return;
      this.width = width;
      this.render();
    });
    this.resize.observe(this);
    this.follow();
  }

  public disconnectedCallback(): void {
    this.resize?.disconnect();
    this.unfollow?.();
    this.unfollow = undefined;
  }

  /** `mode: peaks` follows the History view's picker (D12 §5.7). */
  private follow(): void {
    if (this.config?.mode !== "peaks" || !this.hassRef || this.unfollow || !this.isConnected) return;
    this.unfollow = followPeriod(this.hassRef, (period) => {
      this.period = period;
      void this.fetchPeaks(period);
    });
  }

  public setConfig(config: WindowConfig): void {
    const needs = config?.mode === "month" ? "metric" : "window_used";
    if (!config?.entities?.[needs]) {
      throw new Error(`powerplan-window-card needs entities.${needs}`);
    }
    this.config = config;
    this.key = [];
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    const config = this.config;
    if (!config) return;
    const key = [
      ...Object.values(config.entities).map((id) => (id ? hass.states[id] : undefined)),
      hass.language,
      hass.themes.darkMode,
    ];
    this.follow();
    if (key.length === this.key.length && key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.render();
  }

  public getCardSize(): number {
    return 6;
  }

  public getGridOptions(): Record<string, number | string> {
    return this.config?.mode === "peaks"
      ? { columns: 12, rows: "auto", min_columns: 6 }
      : { columns: 12, rows: 6, min_rows: 5, min_columns: 6 };
  }

  private render(): void {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.addEventListener("click", () => {
        const entities = this.config?.entities;
        const id = this.config?.mode === "month" ? entities?.metric : entities?.window_used;
        if (id) moreInfo(this, id);
      });
    }
    if (config.mode === "month") this.renderMonth(hass, config);
    else if (config.mode === "peaks") this.renderPeaks(hass, config);
    else this.renderHour(hass, config);
  }

  private state(key: Key) {
    const id = this.config?.entities[key];
    return id ? this.hassRef?.states[id] : undefined;
  }

  /** The card's width in px; a first render before layout assumes a desktop column. */
  private get cardWidth(): number {
    return this.width || 400;
  }

  // ------------------------------------------------------------- mode: hour

  private renderHour(hass: HomeAssistant, config: WindowConfig): void {
    const labels = config.labels ?? {};
    const usedEntity = this.state("window_used");
    const used = numeric(usedEntity) ?? 0;
    const projected = numeric(this.state("window_projected"));
    const ceiling = numeric(this.state("ceiling"));
    const tone = stageTone(numeric(this.state("stage")));
    const scale = gauge(used, projected, ceiling);
    const color = scale.over ? TONE_COLOR.alert : TONE_COLOR[tone];
    const locale = hass.locale.language;
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: timeZone(hass) });
    const hasCeiling = ceiling !== null && ceiling > 0;

    // The status chip: the stage in words, or the peak to come while the warning is on.
    const next = this.state("next_peak_warning")?.state;
    const chip =
      this.state("peak_warning")?.state === "on" && next && !Number.isNaN(Date.parse(next))
        ? fill(labels.peak_expected ?? "{time}", { time: clock.format(Date.parse(next)) })
        : (labels[`stage_${stageWord(scale.over ? "alert" : tone)}`] ?? "");

    // The footer's three facts; the allowance is always kW (B8).
    const remaining = Number(usedEntity?.attributes.t_rem_min);
    const allowanceEntity = this.state("allowance");
    const allowance = numeric(allowanceEntity);
    const cells: Array<[string, string, boolean]> = [];
    if (projected !== null) cells.push([labels.expected ?? "", `${two.format(projected)} kWh`, true]);
    if (Number.isFinite(remaining)) {
      cells.push([labels.time_left ?? "", fill(labels.minutes ?? "{min}", { min: String(Math.round(remaining)) }), false]);
    }
    if (allowance !== null) {
      const kw = toKw(allowance, allowanceEntity?.attributes.unit_of_measurement);
      cells.push([labels.headroom ?? "", `${one.format(kw)} kW`, false]);
    }

    // H4: the radius follows the card; the arc's top sits 40 px down, under the chip.
    const width = this.cardWidth;
    const r = Math.min(146, 0.35 * width);
    const cx = width / 2;
    const cy = 40 + r;
    const height = cy + 22 + 8;
    const value = two.format(used);
    // H1: 36 px regular, 30 px under 400 px, and never wider than the arc's inside.
    const size = gaugeFont(`${value} kWh`.length, r, STROKE, width < 400 ? 30 : 36);
    const [px, py] = scale.projected === null ? [0, 0] : arcPoint(scale.projected, r - STROKE / 2 - 4);
    const [qx, qy] = scale.projected === null ? [0, 0] : arcPoint(scale.projected, r + STROKE / 2 + 4);
    const caption = hasCeiling ? fill(labels.used_of ?? "{ceiling}", { ceiling: one.format(ceiling) }) : "";
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        :host { cursor: pointer; }
        ha-card { position: relative; display: flex; flex-direction: column; overflow: hidden; }
        .pp-status { position: absolute; top: 12px; right: 16px; color: ${color};
                     background: color-mix(in srgb, ${color} 16%, transparent); }
        svg { display: block; flex: none; }
        .track { fill: none; stroke: var(--pp-track); stroke-width: ${STROKE}; }
        .used { fill: none; stroke: ${color}; stroke-width: ${STROKE}; }
        .projected { fill: none; stroke: ${color}; stroke-opacity: 0.38; stroke-width: ${STROKE}; }
        .tick { stroke: var(--primary-text-color); stroke-width: 2; }
        .value { font-size: ${size}px; font-weight: 400; fill: var(--primary-text-color); font-variant-numeric: tabular-nums; }
        .unit { font-size: 16px; fill: var(--secondary-text-color); }
        .caption { font-size: 13px; fill: var(--secondary-text-color); }
        .end { font-size: 11px; fill: var(--secondary-text-color); }
        .footer { margin-top: auto; display: grid; grid-template-columns: repeat(${Math.max(cells.length, 1)}, minmax(0, 1fr));
                  height: 72px; box-sizing: border-box; border-top: 1px solid var(--divider-color); }
        .cell { display: flex; flex-direction: column; justify-content: center; gap: 4px; min-width: 0; padding: 0 16px; }
        .cell + .cell { border-left: 1px solid var(--divider-color); }
        .name { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); display: flex; align-items: center; gap: 6px;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .num { font-size: 16px; font-weight: 500; line-height: 20px; font-variant-numeric: tabular-nums; }
        .swatch { width: 10px; height: 10px; border-radius: 3px; background: ${color}; opacity: 0.38; flex: none; }
      </style>
      <ha-card>
        ${chip ? `<span class="pp-status">${escape(chip)}</span>` : ""}
        <svg width="${width}" height="${height.toFixed(0)}" viewBox="0 0 ${width} ${height.toFixed(0)}" role="img"
             aria-label="${escape(`${value} kWh ${caption}`)}">
          <path class="track" d="${arc(cx, cy, 0, 1, r)}"/>
          ${scale.projected !== null && scale.projected > scale.used ? `<path class="projected" d="${arc(cx, cy, scale.used, scale.projected, r)}"/>` : ""}
          ${scale.used > 0 ? `<path class="used" d="${arc(cx, cy, 0, scale.used, r)}"/>` : ""}
          ${scale.projected !== null ? `<line class="tick" x1="${cx + px}" y1="${cy + py}" x2="${cx + qx}" y2="${cy + qy}"/>` : ""}
          <text x="${cx}" y="${cy - 22}" text-anchor="middle"><tspan class="value">${escape(value)}</tspan><tspan class="unit" dx="4">kWh</tspan></text>
          ${caption ? `<text class="caption" x="${cx}" y="${cy + 2}" text-anchor="middle">${escape(caption)}</text>` : ""}
          <text class="end" x="${cx - r}" y="${cy + 22}" text-anchor="middle">0</text>
          ${hasCeiling ? `<text class="end" x="${cx + r}" y="${cy + 22}" text-anchor="middle">${escape(`${one.format(ceiling)} kWh`)}</text>` : ""}
        </svg>
        ${
          cells.length
            ? `<div class="footer">${cells
                .map(
                  ([name, cell, swatch]) =>
                    `<div class="cell"><span class="name">${swatch ? '<span class="swatch"></span>' : ""}${escape(name)}</span><span class="num">${escape(cell)}</span></div>`,
                )
                .join("")}</div>`
            : ""
        }
      </ha-card>`;
  }

  // ------------------------------------------------------------ mode: month

  private renderMonth(hass: HomeAssistant, config: WindowConfig): void {
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const kw = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
    const day = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" });
    const month = new Intl.DateTimeFormat(locale, { month: "long", timeZone: timeZone(hass) });
    const metric = numeric(this.state("metric")) ?? 0;
    const level = this.state("level");
    const steps = (level?.attributes.steps as TariffStep[] | undefined) ?? [];
    const target = this.state("target");
    const items = this.state("advice")?.attributes.items;
    const top = topEntries(items).slice(0, 3);
    const headroom = adviceItem(items, "step_headroom");
    const tips = adviceItem(items, "days_that_matter");

    const width = this.cardWidth;
    const r = Math.min(120, 0.3 * width);
    const cx = width / 2;
    const cy = 30 + r;
    let arcs = "";
    let ticks = "";
    let needle = "";
    let bars = "";
    if (steps.length) {
      // M1: coloured by position against the target step, the current step opaque.
      const g = monthGauge(metric, steps, targetStep(target?.state, target?.attributes, steps));
      const gap = 2 / (Math.PI * r);
      arcs = g.segments
        .map((seg) => {
          const from = seg.from / g.max + (seg.from > 0 ? gap / 2 : 0);
          const to = seg.to / g.max - (seg.to < g.max ? gap / 2 : 0);
          return `<path d="${arc(cx, cy, from, to, r)}" fill="none" stroke="${TONE_COLOR[seg.tone]}" stroke-width="${MONTH_STROKE}" stroke-opacity="${seg.current ? 1 : 0.28}"/>`;
        })
        .join("");
      ticks = [0, ...g.ticks, g.max]
        .map((value) => {
          const [x, y] = arcPoint(value / g.max, r + 18);
          const label = value === g.max ? `${kw.format(value)} kW` : kw.format(value);
          return `<text class="end" x="${(cx + x).toFixed(1)}" y="${(cy + y + 4).toFixed(1)}" text-anchor="middle">${escape(label)}</text>`;
        })
        .join("");
      // M2: 3 px, round cap, r + 6 long, a 6 px hub - drawn last, over the arcs and the text.
      const [nx, ny] = arcPoint(g.needle, r + 6);
      needle = `<line x1="${cx}" y1="${cy}" x2="${(cx + nx).toFixed(1)}" y2="${(cy + ny).toFixed(1)}" stroke="var(--primary-text-color)" stroke-width="3" stroke-linecap="round"/><circle cx="${cx}" cy="${cy}" r="6" fill="var(--primary-text-color)"/>`;
      if (top.length) {
        // M3: the value in kW, 8 px bars on the track, the step's bound dashed and named.
        const at = (value: number) => `${Math.min(100, (100 * value) / g.barMax).toFixed(1)}%`;
        const upper = g.upper;
        const limit = (named: boolean) =>
          upper === null
            ? ""
            : `<span class="limit" style="left:${at(upper)}">${named ? `<span>${escape(`${kw.format(upper)} kW`)}</span>` : ""}</span>`;
        bars = `<div class="top3"><div class="pp-sub">${escape(fill(labels.top3 ?? "{month}", { month: month.format(Date.now()) }))}</div>${top
          .map(
            ([date, value], i) =>
              `<div class="row"><span>${escape(day.format(Date.parse(date)))}</span><span class="bar"><span class="fill" style="width:${at(value)}"></span>${limit(i === 0)}</span><span class="num">${escape(`${two.format(value)} kW`)}</span></div>`,
          )
          .join("")}</div>`;
      }
    }
    const reading = `${two.format(metric)} kW`;
    const caption = level?.state ? fill(labels.metric_label ?? "{level}", { level: level.state }) : "";
    // M4: kW to one decimal, the fee as money.
    const footer: string[] = [];
    if (headroom) {
      const fee = moneyFormat(locale, String(headroom.currency ?? ""), 0);
      footer.push(
        fill(labels.to_next_step ?? "", {
          kw: one.format(Number(headroom.to_next_kw)),
          next: String(headroom.next_name ?? ""),
          fee: fee.format(Number(headroom.fee_delta)),
        }),
      );
    }
    if (tips) footer.push(fill(labels.day_that_tips ?? "", { kw: one.format(Number(tips.kw)) }));
    const height = steps.length ? cy + 60 : 80;
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        :host { cursor: pointer; }
        .pp-content { display: flex; flex-direction: column; gap: 12px; }
        svg { display: block; flex: none; align-self: center; }
        .value { font-size: 28px; font-weight: 400; fill: var(--primary-text-color); font-variant-numeric: tabular-nums; }
        .caption { font-size: 12px; fill: var(--secondary-text-color); }
        .end { font-size: 11px; fill: var(--secondary-text-color); }
        .top3 { display: flex; flex-direction: column; gap: 6px; }
        .row { display: grid; grid-template-columns: 56px 1fr auto; align-items: center; gap: 10px; font-size: 12px; }
        .num { font-weight: 500; text-align: right; font-variant-numeric: tabular-nums; }
        .bar { position: relative; height: 8px; border-radius: 4px; background: var(--pp-track); }
        .fill { position: absolute; inset: 0 auto 0 0; border-radius: 4px; background: var(--primary-color); }
        .limit { position: absolute; top: -4px; bottom: -4px; border-left: 1.5px dashed var(--warning-color); }
        .limit span { position: absolute; bottom: 100%; left: -12px; font-size: 10px; line-height: 12px;
                      color: var(--secondary-text-color); white-space: nowrap; }
        .top3 .row:first-of-type { margin-top: 10px; }
        .footer { display: flex; gap: 8px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--divider-color);
                  font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
        .footer ha-icon { --mdc-icon-size: 16px; color: var(--warning-color); flex: none; }
      </style>
      <ha-card><div class="pp-content">
        <svg width="${width - 32}" height="${height}" viewBox="${16} 0 ${width - 32} ${height}" role="img" aria-label="${escape(`${reading} ${caption}`)}">
          ${arcs}${ticks}
          <text class="value" x="${cx}" y="${steps.length ? cy + 36 : 40}" text-anchor="middle">${escape(reading)}</text>
          ${caption ? `<text class="caption" x="${cx}" y="${steps.length ? cy + 54 : 60}" text-anchor="middle">${escape(caption)}</text>` : ""}
          ${needle}
        </svg>
        ${bars}
        ${footer.length ? `<div class="footer"><ha-icon icon="mdi:lightbulb-outline"></ha-icon><span>${escape(footer.join(" "))}</span></div>` : ""}
      </div></ha-card>`;
  }

  // ------------------------------------------------------------ mode: peaks

  private async fetchPeaks(period: Period): Promise<void> {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    const used = config.entities.window_used!;
    const grid = config.grid_entities ?? [];
    const zone = timeZone(hass);
    const oneDay = period.end.getTime() - period.start.getTime() <= 2 * 86_400_000;
    // The month around the period's start: a day counts against its own month.
    const month = {
      start: new Date(period.start.getFullYear(), period.start.getMonth(), 1),
      end: new Date(period.start.getFullYear(), period.start.getMonth() + 1, 1),
    };
    const ids = [used, ...(config.entities.ceiling ? [config.entities.ceiling] : [])];
    const grain = oneDay ? "hour" : "day";
    const safe = (p: Promise<Record<string, StatRow[]>>) => p.catch(() => ({}) as Record<string, StatRow[]>);
    const [own, gridStats, ranking] = await Promise.all([
      safe(fetchStatistics(hass, period, ids, ["max", "mean"], grain)),
      // D4: before `window_used`'s statistics, the grid sources' hourly sum stands in.
      grid.length ? safe(fetchStatistics(hass, period, grid, ["change"], "hour")) : Promise.resolve({} as Record<string, StatRow[]>),
      monthRanking(hass, month, { used, grid, advice: this.state("advice")?.attributes.items }, zone),
    ]);
    if (this.period !== period) return;
    const hours = gridHours(gridStats, grid, (id) => kwhScale(hass, id));
    const fallback = oneDay ? hours : dailyPeaks(hours, zone).map(([day, kwh]) => ({ start: Date.parse(day), end: Date.parse(day), max: kwh }));
    const ownRows = (own[used] ?? []).filter((row) => row.max != null);
    const rows = ownRows.length ? ownRows : fallback;
    const ceilings = (own[ids[1] ?? ""] ?? []).map((row) => row.mean).filter((v): v is number => v != null);
    this.fetched = {
      period,
      hours: oneDay ? rows : [],
      days: oneDay ? [] : ownRows.length ? rows.map((row) => [dayKey(row.start, zone), row.max!] as DayPeak) : dailyPeaks(hours, zone),
      ranking,
      ceiling: ceilings.length ? Math.max(...ceilings) : numeric(this.state("ceiling")),
    };
    this.render();
  }

  private renderPeaks(hass: HomeAssistant, config: WindowConfig): void {
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const kw = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
    const date = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" });
    const monthName = new Intl.DateTimeFormat(locale, { month: "long", timeZone: zone });
    const hourFmt = new Intl.DateTimeFormat(locale, { hour: "2-digit", timeZone: zone });
    const data = this.fetched;
    const style = `
      <style>
        ${ppStyles}
        :host { cursor: pointer; }
        .pp-content { display: flex; flex-direction: column; gap: 12px; }
        .reading { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
        .reading .when { font-size: 14px; color: var(--secondary-text-color); }
        svg { display: block; width: 100%; }
        .axis { font-size: 10px; fill: var(--secondary-text-color); }
        .pp-readout ha-icon { --mdc-icon-size: 20px; flex: none; }
        .pp-readout span { font-size: 13px; line-height: 18px; }
        .rows { display: flex; flex-direction: column; }
        .rows .pp-row { min-height: 36px; padding: 6px 0; }
      </style>`;
    if (!data || (!data.days.length && !data.hours.length)) {
      const text = data ? withoutDate(labels.collecting ?? "") : "";
      this.shadowRoot!.innerHTML = `${style}<ha-card><div class="pp-content">${text ? `<div class="pp-empty">${escape(text)}</div>` : ""}</div></ha-card>`;
      return;
    }
    const primary = cssVar(this, "--primary-color", "#03a9f4");
    const warning = cssVar(this, "--warning-color", "#ffa600");
    const error = cssVar(this, "--error-color", "#db4437");
    const level = this.state("level");
    const steps = (level?.attributes.steps as TariffStep[] | undefined) ?? [];
    const target = this.state("target");
    const index = steps.length ? targetStep(target?.state, target?.attributes, steps) : null;
    const stepKw = index !== null ? (steps[index]?.to_kw ?? null) : null;
    const third = [...data.ranking].sort((a, b) => b[1] - a[1])[2];
    if (data.hours.length) {
      // One day: its highest hour against the month's third-highest day (D4).
      const best = data.hours.reduce((a, b) => ((b.max ?? 0) > (a.max ?? 0) ? b : a));
      const value = best.max ?? 0;
      const decision = countsDecision(value, data.ranking);
      const scale = niceScale(Math.max(12, value, third?.[1] ?? 0, stepKw ?? 0) * 1.05).max;
      const x = (v: number) => 8 + (284 * v) / scale;
      const marker = (v: number, color: string, text: string, y: number) =>
        `<line x1="${x(v).toFixed(1)}" x2="${x(v).toFixed(1)}" y1="18" y2="38" stroke="${color}" stroke-width="1.5" stroke-dasharray="4 3"/><text class="axis" x="${x(v).toFixed(1)}" y="${y}" text-anchor="middle">${escape(text)}</text>`;
      const verdict = decision.counts
        ? { icon: "mdi:alert-circle-outline", color: "var(--warning-color)", text: labels.counts ?? "" }
        : {
            icon: "mdi:check-circle-outline",
            color: "var(--success-color)",
            text: fill(labels.not_counts_month ?? "", {
              month: monthName.format(data.period.start),
              date: date.format(Date.parse(decision.third![0])),
              kw: two.format(decision.third![1]),
            }),
          };
      this.shadowRoot!.innerHTML = `${style}<ha-card><div class="pp-content">
        <div class="reading"><span class="pp-stat-value">${escape(two.format(value))}<span class="pp-stat-unit">kWh</span></span>
          <span class="when">· ${escape(`${hourFmt.format(best.start)}–${hourFmt.format(best.end)}`)}</span></div>
        <svg viewBox="0 0 300 64" role="img">
          <rect x="8" y="24" width="284" height="8" rx="4" fill="var(--pp-track)"/>
          <rect x="8" y="24" width="${Math.max(0, x(value) - 8).toFixed(1)}" height="8" rx="4" fill="${primary}"/>
          ${third ? marker(third[1], warning, fill(labels.third_short ?? "{kw}", { kw: two.format(third[1]) }), 12) : ""}
          ${stepKw !== null ? marker(stepKw, error, fill(labels.step_limit ?? "{kw}", { kw: kw.format(stepKw) }), 52) : ""}
        </svg>
        <div class="pp-readout"><ha-icon icon="${verdict.icon}" style="color:${verdict.color}"></ha-icon><span>${escape(verdict.text)}</span></div>
      </div></ha-card>`;
      return;
    }
    // A longer range: each day's highest hour, the days that count solid (D12 §5.7).
    const counting = countingDays([...data.ranking, ...data.days.filter(([day]) => !data.ranking.some(([d]) => d === day))]);
    const top = Math.max(...data.days.map(([, v]) => v), stepKw ?? 0);
    const scale = niceScale(top * 1.1);
    const width = 280 / data.days.length;
    const y = (v: number) => 110 - (100 * v) / scale.max;
    const bars = data.days
      .map(([day, v], i) => {
        const on = counting.has(day);
        return `<rect x="${(20 + i * width + 1).toFixed(1)}" y="${y(v).toFixed(1)}" width="${Math.max(1, width - 2).toFixed(1)}" height="${(110 - y(v)).toFixed(1)}" rx="1" fill="${primary}" fill-opacity="${on ? 1 : 0.4}"/>${on ? `<circle cx="${(20 + (i + 0.5) * width).toFixed(1)}" cy="${(y(v) - 5).toFixed(1)}" r="2.5" fill="${warning}"/>` : ""}`;
      })
      .join("");
    const limit =
      stepKw !== null
        ? `<line x1="20" x2="300" y1="${y(stepKw).toFixed(1)}" y2="${y(stepKw).toFixed(1)}" stroke="${error}" stroke-width="1.5" stroke-dasharray="4 3"/>`
        : "";
    const ticks = [0, scale.max / 2, scale.max]
      .map((v) => `<text class="axis" x="16" y="${(y(v) + 3).toFixed(1)}" text-anchor="end">${escape(kw.format(v))}</text>`)
      .join("");
    const best = data.days.filter(([day]) => counting.has(day)).sort((a, b) => b[1] - a[1]).slice(0, 3);
    this.shadowRoot!.innerHTML = `${style}<ha-card><div class="pp-content">
      <svg viewBox="0 0 300 118" preserveAspectRatio="none">${ticks}${bars}${limit}</svg>
      <div class="rows">${best.map(([day, v]) => `<div class="pp-row"><span class="pp-name">${escape(date.format(Date.parse(day)))}</span><span class="pp-num">${escape(`${two.format(v)} kWh`)}</span></div>`).join("")}</div>
    </div></ha-card>`;
  }
}
