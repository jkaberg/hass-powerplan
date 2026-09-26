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
import { ChartTip, mark, MARK_CSS, markDot, markSwatch, tipAttr } from "./marks";
import { type Hass, localMidnight } from "./r3-util";
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
  carpetAlpha,
  type CarpetDay,
  carpetGrid,
  headroomStrip,
  runHours,
  moneyFormat,
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
  | "target"
  | "savings"
  | "plan";

interface WindowConfig {
  entry_id: string;
  mode?: "hour" | "month" | "peaks";
  entities: Partial<Record<Key, string>>;
  /** `mode: peaks`: the Energy preferences' grid sources, for days before `window_used`'s statistics. */
  grid_entities?: string[];
  /** `mode: peaks`: the run-type appliances whose runs the carpet marks (D12 §5.20 V4). */
  loads?: Array<{ id: string; name: string; status: string }>;
  labels?: Record<string, string>;
}

/** The hour arc's stroke (H4). */
const STROKE = 24;

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
  /** The card's height from the section grid (`rows: 6`), so the hour gauge fills it rather than leaving a band. */
  private height = 0;
  private resize?: ResizeObserver;
  private tip?: ChartTip;
  /** V1: today's highest hour so far (kWh per window), and the hour it was fetched in. */
  private todayKwh: number | null = null;
  private todayHour = -1;
  private period?: Period;
  private unfollow?: () => void;
  private fetched?: {
    period: Period;
    days: DayPeak[];
    hours: StatRow[];
    ranking: Array<[string, number]>;
    ceiling: number | null;
    /** V4: 3–35 days as local days × hours, and the hours an appliance ran on its plan. */
    carpet?: CarpetDay[];
    runs?: Map<string, Set<string>>;
  };

  public connectedCallback(): void {
    this.resize = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      const height = entries[0]?.contentRect.height ?? 0;
      const hour = (this.config?.mode ?? "hour") === "hour";
      if (Math.abs(width - this.width) < 1 && (!hour || Math.abs(height - this.height) < 1)) return;
      this.width = width;
      this.height = height;
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
    return this.config?.mode === "peaks" || this.config?.mode === "month"
      ? { columns: 12, rows: "auto", min_columns: 6 }
      : { columns: 12, rows: 6, min_rows: 5, min_columns: 6 };
  }

  private render(): void {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.addEventListener("click", (event) => {
        if (event.composedPath().some((node) => node instanceof Element && node.hasAttribute("data-tip"))) return;
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
    // The chip in HA's semantic quiet colours (`--ha-color-fill-*` / `--ha-color-on-*`), as HA's own chips.
    const chipRole = scale.over || tone === "alert" ? "danger" : tone === "warn" ? "warning" : "success";
    const locale = hass.locale.language;
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: timeZone(hass) });
    const hasCeiling = ceiling !== null && ceiling > 0;

    // The status chip: the stage in words, or the peak to come while the warning is on. Iteration 5:
    // only when the hour needs you; a normal hour is the gauge's green.
    const next = this.state("next_peak_warning")?.state;
    const chip =
      this.state("peak_warning")?.state === "on" && next && !Number.isNaN(Date.parse(next))
        ? fill(labels.peak_expected ?? "{time}", { time: clock.format(Date.parse(next)) })
        : scale.over || tone !== "ok"
          ? (labels[`stage_${stageWord(scale.over ? "alert" : tone)}`] ?? "")
          : "";

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

    // H4: the radius follows the card; the arc's top sits 40 px down, under the chip. The arc fills the
    // height the section grid gives the card (above the 72 px footer), and never pushes its end labels out.
    const width = this.cardWidth;
    const room = (this.height || 376) - (cells.length ? 72 : 0) - 40 - 30 - 16;
    const r = Math.max(60, Math.min(170, width / 2 - 34, room));
    const cx = width / 2;
    const cy = 40 + r;
    const height = cy + 22 + 8;
    const value = two.format(used);
    // H1: 36 px regular, 30 px under 400 px, and never wider than the arc's inside.
    const size = gaugeFont(`${value} kWh`.length, r, STROKE, width < 400 ? 30 : 36);
    const [px, py] = scale.projected === null ? [0, 0] : arcPoint(scale.projected, r - STROKE / 2 - 4);
    const [qx, qy] = scale.projected === null ? [0, 0] : arcPoint(scale.projected, r + STROKE / 2 + 4);
    // Iteration 5: the ceiling is the end label and "this hour" the heading; the words stay for a screen reader.
    const caption = hasCeiling ? fill(labels.used_of ?? "{ceiling}", { ceiling: one.format(ceiling) }) : "";
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        :host { cursor: pointer; }
        ha-card { position: relative; display: flex; flex-direction: column; overflow: hidden; }
        .pp-status { position: absolute; top: 12px; right: 16px; color: var(--ha-color-on-${chipRole}-quiet, ${color});
                     background: var(--ha-color-fill-${chipRole}-quiet-resting, color-mix(in srgb, ${color} 16%, transparent)); }
        svg { display: block; flex: none; margin: auto 0; }
        .track { fill: none; stroke: var(--pp-track); stroke-width: ${STROKE}; }
        .used { fill: none; stroke: ${color}; stroke-width: ${STROKE}; }
        .projected { fill: none; stroke: ${color}; stroke-opacity: 0.38; stroke-width: ${STROKE}; }
        .tick { stroke: var(--primary-text-color); stroke-width: 2; }
        .value { font-size: ${size}px; font-weight: 400; fill: var(--primary-text-color); font-variant-numeric: tabular-nums; }
        .unit { font-size: 16px; fill: var(--secondary-text-color); }
        .end { font-size: 11px; fill: var(--secondary-text-color); }
        .footer { display: grid; grid-template-columns: repeat(${Math.max(cells.length, 1)}, minmax(0, 1fr));
                  height: 72px; box-sizing: border-box; border-top: 1px solid var(--divider-color); }
        .cell { display: flex; flex-direction: column; justify-content: center; gap: 4px; min-width: 0; padding: 0 12px; }
        .cell + .cell { border-left: 1px solid var(--divider-color); }
        .name { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); display: flex; align-items: center; gap: 6px;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .num { font-size: 16px; font-weight: 500; line-height: 20px; font-variant-numeric: tabular-nums; }
        .swatch { width: 10px; height: 10px; border-radius: 3px; background: ${color}; flex: none; }
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

  /** V1: today's highest hour so far in kW, from `window_used`'s hourly statistics and the hour in progress. */
  private async fetchToday(): Promise<void> {
    const hass = this.hassRef, config = this.config;
    const used = config?.entities.window_used;
    if (!hass || !used) return;
    this.todayHour = Math.floor(Date.now() / 3_600_000);
    const start = localMidnight(hass as unknown as Hass);
    const rows = await fetchStatistics(hass, { start, end: new Date() }, [used], ["max"], "hour").catch(() => ({}) as Record<string, StatRow[]>);
    const peak = Math.max(0, ...(rows[used] ?? []).map((row) => row.max ?? 0));
    this.todayKwh = peak;
    this.key = [];
    this.render();
  }

  private renderMonth(hass: HomeAssistant, config: WindowConfig): void {
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const day = new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
    const metric = numeric(this.state("metric")) ?? 0;
    const level = this.state("level");
    const steps = (level?.attributes.steps as TariffStep[] | undefined) ?? [];
    const target = this.state("target");
    const items = this.state("advice")?.attributes.items;
    const top = topEntries(items).slice(0, 3);
    const headroom = adviceItem(items, "step_headroom");
    const tips = adviceItem(items, "days_that_matter");
    const without = Number(this.state("savings")?.attributes.metric_kw_without);
    if (config.entities.window_used && Math.floor(Date.now() / 3_600_000) !== this.todayHour) void this.fetchToday();
    // Today's highest hour so far, the hour in progress included, as kW (a window of `window_min`).
    const windowMin = Number(this.state("plan")?.attributes.window_min) || 60;
    const now = numeric(this.state("window_used"));
    const todayKw = this.todayKwh === null && now === null ? null : (Math.max(this.todayKwh ?? 0, now ?? 0) * 60) / windowMin;

    const width = this.cardWidth - 32;
    const strip = steps.length ? headroomStrip(metric, steps, targetStep(target?.state, target?.attributes, steps)) : null;
    let svg = "";
    if (strip) {
      const pad = 12, ty = 46, th = 10, H = 98;
      const x = (kw: number) => pad + strip.at(kw) * (width - 2 * pad);
      const money = (fee: string | null) => {
        const [amount, currency] = String(fee ?? "").split(" ");
        return amount && Number.isFinite(Number(amount)) ? moneyFormat(locale, currency ?? "", 0).format(Number(amount)) : "";
      };
      const g: string[] = [];
      strip.segments.forEach((seg, i) => {
        const x0 = x(seg.from) + (i ? 1 : 0), x1 = x(seg.to) - (i < strip.segments.length - 1 ? 1 : 0);
        const text = [seg.name, money(seg.fee)].filter(Boolean).join(" · ");
        g.push(`<rect x="${x0.toFixed(1)}" y="${ty}" width="${Math.max(0, x1 - x0).toFixed(1)}" height="${th}" rx="5" fill="${TONE_COLOR[seg.tone]}" fill-opacity="${seg.current ? 1 : 0.45}"${tipAttr([text])}/>`);
        if (x1 - x0 > 90) g.push(`<text class="end" x="${((x0 + x1) / 2).toFixed(1)}" y="${ty + th + 32}" text-anchor="middle">${escape(text)}</text>`);
      });
      const tipKw = Number(tips?.kw);
      if (Number.isFinite(tipKw) && tipKw > strip.lo && tipKw < strip.hi) {
        const fee = headroom ? moneyFormat(locale, String(headroom.currency ?? ""), 0).format(Number(headroom.fee_delta)) : "";
        const text = fill(labels.tips_at ?? "{kw} kW: +{fee}", { kw: one.format(tipKw), fee });
        g.push(`<g${tipAttr([text, fill(labels.day_that_tips ?? "", { kw: one.format(tipKw) })])}><rect x="${(x(tipKw) - 12).toFixed(1)}" y="4" width="24" height="${ty - 4}" fill="transparent"/>`
          + `<line x1="${x(tipKw).toFixed(1)}" y1="${ty - 3}" x2="${x(tipKw).toFixed(1)}" y2="16" stroke="var(--warning-color)" stroke-width="2" stroke-linecap="round"/></g>`);
        const half = text.length * 3.2;
        const cx = Math.min(Math.max(x(tipKw), half), width - half);
        g.push(`<text class="end" x="${cx.toFixed(1)}" y="10" text-anchor="middle">${escape(text)}</text>`);
      }
      if (todayKw !== null && todayKw >= strip.lo) {
        const text = fill(labels.today ?? "Today", {});
        g.push(`<g${tipAttr([text, `${two.format(todayKw)} kW`])}><circle cx="${x(todayKw).toFixed(1)}" cy="${ty - 9}" r="12" fill="transparent"/>`
          + `<circle cx="${x(todayKw).toFixed(1)}" cy="${ty - 9}" r="4.5" fill="var(--card-background-color, #fff)" stroke="var(--primary-text-color)" stroke-width="2"/></g>`);
        g.push(`<text class="end" x="${x(todayKw).toFixed(1)}" y="${ty - 20}" text-anchor="middle">${escape(text)}</text>`);
      }
      [...top].sort((a, b) => a[1] - b[1]).forEach(([date, kw], i) => {
        const cy = ty - 9 - i * 9;
        g.push(`<g${tipAttr([day.format(Date.parse(date)), `${two.format(kw)} kW`, labels.counts ?? ""])}><circle cx="${x(kw).toFixed(1)}" cy="${cy}" r="5.75" fill="var(--card-background-color, #fff)"/>`
          + `<circle cx="${x(kw).toFixed(1)}" cy="${cy}" r="3.75" fill="var(--primary-text-color)"/></g>`);
      });
      g.push(`<rect x="${(x(metric) - 1.5).toFixed(1)}" y="${ty - 4}" width="3" height="${th + 9}" rx="1.5" fill="var(--primary-text-color)"/>`);
      if (Number.isFinite(without) && without - metric > 0.05) {
        const yb = ty + th + 9;
        g.push(`<line x1="${x(metric).toFixed(1)}" y1="${yb}" x2="${x(without).toFixed(1)}" y2="${yb}" stroke="var(--secondary-text-color)" stroke-width="1.5" stroke-linecap="round"/>`);
        g.push(mark(x(without), yb, [
          fill(labels.mark_metric ?? "{kw} kW", { kw: two.format(without) }),
          fill(labels.mark_metric_held ?? "{kw} kW", { kw: two.format(without - metric) }),
        ]));
      }
      svg = `<svg width="${width}" height="${H}" viewBox="0 0 ${width} ${H}" role="img" aria-label="${escape(`${two.format(metric)} kW`)}">${g.join("")}</svg>`;
    }
    const reading = two.format(metric);
    // Iteration 5: the step's fee rides on the subtitle (it left Strømpris), "397.00 NOK" → "397 kr/mnd".
    const [feeAmount, feeCurrency] = String(level?.attributes.fee ?? "").split(" ");
    const stepFee = feeAmount && Number.isFinite(Number(feeAmount))
      ? fill(labels.step_fee ?? "{fee}", { fee: moneyFormat(locale, feeCurrency ?? "", 0).format(Number(feeAmount)) })
      : "";
    const caption = level?.state
      ? [fill(labels.metric_label ?? "{level}", { level: level.state }), stepFee].filter(Boolean).join(" · ")
      : "";
    // M4: kW to one decimal, the fee as money. Iteration 5: one sentence when both are known.
    const footer: string[] = [];
    const nextFee = headroom ? moneyFormat(locale, String(headroom.currency ?? ""), 0).format(Number(headroom.fee_delta)) : "";
    if (headroom && tips) {
      footer.push(fill(labels.day_that_tips_step ?? "", { kw: one.format(Number(tips.kw)), next: String(headroom.next_name ?? ""), fee: nextFee }));
    } else if (headroom) {
      footer.push(fill(labels.to_next_step ?? "", { kw: one.format(Number(headroom.to_next_kw)), next: String(headroom.next_name ?? ""), fee: nextFee }));
    } else if (tips) footer.push(fill(labels.day_that_tips ?? "", { kw: one.format(Number(tips.kw)) }));
    const legend = strip
      ? [
          top.length ? `<span><i class="dot"></i>${escape(labels.top_days ?? "")}</span>` : "",
          todayKw !== null && todayKw >= strip.lo ? `<span><i class="ring"></i>${escape(labels.today ?? "")}</span>` : "",
          Number.isFinite(without) && without - metric > 0.05 ? `<span>${markSwatch}${escape(labels.without_powerplan ?? "")}</span>` : "",
        ].join("")
      : "";
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        ${MARK_CSS}
        :host { cursor: pointer; }
        ha-card { position: relative; }
        .pp-content { display: flex; flex-direction: column; gap: 10px; }
        svg { display: block; overflow: visible; }
        .end { font-size: 11px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
        .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); }
        .legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
        .legend .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--primary-text-color); }
        .legend .ring { width: 6px; height: 6px; border-radius: 50%; border: 2px solid var(--primary-text-color); }
        .footer { display: flex; gap: 8px; padding-top: 10px; border-top: 1px solid var(--divider-color);
                  font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
        .footer ha-icon { --mdc-icon-size: 16px; color: var(--warning-color); flex: none; }
      </style>
      <ha-card><div class="pp-content">
        <div><span class="pp-stat-value">${escape(reading)}<span class="pp-stat-unit">kW</span></span>
          ${caption ? `<div class="pp-sub">${escape(caption)}</div>` : ""}</div>
        ${svg}
        ${legend ? `<div class="legend">${legend}</div>` : ""}
        ${footer.length ? `<div class="footer"><ha-icon icon="mdi:lightbulb-outline"></ha-icon><span>${escape(footer.join(" "))}</span></div>` : ""}
      </div></ha-card>`;
    this.tip ??= new ChartTip(this.shadowRoot!, () => this.shadowRoot!.querySelector("ha-card"));
    this.tip.reset();
  }

  // ------------------------------------------------------------ mode: peaks

  private async fetchPeaks(period: Period): Promise<void> {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    const used = config.entities.window_used!;
    const grid = config.grid_entities ?? [];
    const zone = timeZone(hass);
    const span = period.end.getTime() - period.start.getTime();
    const oneDay = span <= 2 * 86_400_000;
    const carpet = !oneDay && span <= 35 * 86_400_000;
    // The month around the period's start: a day counts against its own month.
    // Noon of the period's first day, so a browser in another zone than HA's still names HA's month.
    const first = new Date(period.start.getTime() + 12 * 3_600_000);
    const month = {
      start: new Date(first.getFullYear(), first.getMonth(), 1),
      end: new Date(first.getFullYear(), first.getMonth() + 1, 1),
    };
    const ids = [used, ...(config.entities.ceiling ? [config.entities.ceiling] : [])];
    const grain = oneDay || carpet ? "hour" : "day";
    const statuses = carpet ? (config.loads ?? []) : [];
    const end = Math.min(period.end.getTime(), Date.now());
    const safe = (p: Promise<Record<string, StatRow[]>>) => p.catch(() => ({}) as Record<string, StatRow[]>);
    const [own, gridStats, ranking] = await Promise.all([
      safe(fetchStatistics(hass, period, ids, ["max", "mean"], grain)),
      // D4: before `window_used`'s statistics, the grid sources' hourly sum stands in.
      grid.length ? safe(fetchStatistics(hass, period, grid, ["change"], "hour")) : Promise.resolve({} as Record<string, StatRow[]>),
      monthRanking(hass, month, { used, grid, advice: this.state("advice")?.attributes.items }, zone),
    ]);
    // V4: HA's own history of the run-type appliances' `plan_status`, states only.
    const history = statuses.length
      ? await hass.callWS<Record<string, Array<{ s: string; lu: number }>>>({
          type: "history/history_during_period", start_time: period.start.toISOString(), end_time: new Date(end).toISOString(),
          entity_ids: statuses.map((load) => load.status), minimal_response: true, no_attributes: true, significant_changes_only: false,
        }).catch(() => ({}))
      : {};
    if (this.period !== period) return;
    const hours = gridHours(gridStats, grid, (id) => kwhScale(hass, id));
    const fallback = oneDay ? hours : dailyPeaks(hours, zone).map(([day, kwh]) => ({ start: Date.parse(day), end: Date.parse(day), max: kwh }));
    const ownRows = (own[used] ?? []).filter((row) => row.max != null);
    const rows = ownRows.length ? ownRows : fallback;
    const ceilings = (own[ids[1] ?? ""] ?? []).map((row) => row.mean).filter((v): v is number => v != null);
    if (carpet) {
      // Before `window_used`'s statistics begin, the grid sources' hourly sum stands in, hour by hour (D4).
      const own = new Map(ownRows.map((row) => [row.start, row]));
      const merged = [...hours.filter((row) => !own.has(row.start)), ...ownRows];
      this.fetched = {
        period, hours: [], days: [], ranking,
        ceiling: ceilings.length ? Math.max(...ceilings) : numeric(this.state("ceiling")),
        carpet: carpetGrid(merged, period.start, new Date(Math.min(period.end.getTime(), end)), zone),
        runs: runHours(history, Object.fromEntries(statuses.map((load) => [load.status, load.id])), end, zone),
      };
      this.render();
      return;
    }
    this.fetched = {
      period,
      hours: oneDay ? rows : [],
      days: oneDay ? [] : ownRows.length ? rows.map((row) => [dayKey(row.start, zone), row.max!] as DayPeak) : dailyPeaks(hours, zone),
      ranking,
      ceiling: ceilings.length ? Math.max(...ceilings) : numeric(this.state("ceiling")),
    };
    this.render();
  }

  /** V4 (D12 §5.20): days × hours, each day's highest hour ringed, the counting days' in bold, runs marked. */
  private renderCarpet(hass: HomeAssistant, config: WindowConfig, carpet: CarpetDay[], data: NonNullable<typeof this.fetched>, style: string): void {
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const dayFmt = new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
    const counting = countingDays(data.ranking);
    const ceiling = data.ceiling && data.ceiling > 0 ? data.ceiling : Math.max(1, ...carpet.flatMap((d) => d.cells.map((v) => v ?? 0)));
    const names = new Map((config.loads ?? []).map((load) => [load.id, load.name]));
    const width = this.cardWidth - 32, lw = 30, cw = (width - lw) / 24, ch = Math.max(10, Math.min(14, cw * 0.85)), top = 16, gap = 1.5;
    const height = top + carpet.length * ch + 2;
    const g: string[] = [];
    for (const h of [0, 6, 12, 18]) g.push(`<text class="axis" x="${(lw + h * cw + 1).toFixed(1)}" y="11">${String(h).padStart(2, "0")}</text>`);
    let over = false;
    carpet.forEach((day, row) => {
      const y = top + row * ch;
      const isTop = counting.has(day.day);
      if (isTop || day.date === 1 || day.date % 5 === 0) {
        g.push(`<text class="axis${isTop ? " on" : ""}" x="${lw - 6}" y="${(y + ch / 2 + 3.5).toFixed(1)}" text-anchor="end">${day.date}</text>`);
      }
      let mh = -1;
      day.cells.forEach((v, h) => { if (v != null && (mh < 0 || v > day.cells[mh]!)) mh = h; });
      day.cells.forEach((v, h) => {
        const x = lw + h * cw;
        const rect = `x="${(x + gap / 2).toFixed(1)}" y="${(y + gap / 2).toFixed(1)}" width="${(cw - gap).toFixed(1)}" height="${(ch - gap).toFixed(1)}" rx="2"`;
        if (v == null) { g.push(`<rect ${rect} fill="rgba(var(--rgb-primary-text-color, 20, 20, 20), .045)"/>`); return; }
        const hot = v > ceiling;
        over ||= hot;
        const key = `${day.day} ${String(h).padStart(2, "0")}`;
        const ran = data.runs?.get(key);
        const lines = [`${dayFmt.format(Date.parse(day.day))} ${String(h).padStart(2, "0")}–${String((h + 1) % 24).padStart(2, "0")}`, `${two.format(v)} kWh`];
        if (h === mh) lines.push(isTop ? `${labels.highest_hour ?? ""} · ${labels.counting ?? ""}` : labels.highest_hour ?? "");
        if (hot) lines.push(labels.over_limit ?? "");
        for (const id of ran ?? []) lines.push(fill(labels.ran_load ?? "{load}", { load: names.get(id) ?? id }));
        g.push(`<rect ${rect} fill="${hot ? "var(--error-color)" : `rgba(var(--rgb-primary-color, 0, 154, 199), ${carpetAlpha(v, ceiling).toFixed(3)})`}"${tipAttr(lines, false)}/>`);
      });
      if (mh >= 0) {
        const x = lw + mh * cw;
        g.push(`<rect x="${(x + gap / 2 + 0.5).toFixed(1)}" y="${(y + gap / 2 + 0.5).toFixed(1)}" width="${(cw - gap - 1).toFixed(1)}" height="${(ch - gap - 1).toFixed(1)}" rx="2" fill="none" stroke="${isTop ? "var(--primary-text-color)" : "rgba(var(--rgb-primary-text-color, 20, 20, 20), .55)"}" stroke-width="${isTop ? 2 : 1}" pointer-events="none"/>`);
      }
      day.cells.forEach((v, h) => {
        if (v == null || !data.runs?.has(`${day.day} ${String(h).padStart(2, "0")}`)) return;
        const r = Math.max(1.6, Math.min(2.4, ch * 0.17));
        g.push(`<g pointer-events="none">${markDot(lw + h * cw + cw / 2, y + ch / 2, r)}</g>`);
      });
    });
    const legend = [
      `<span>0<i class="grad"></i>${escape(`${two.format(ceiling)} kWh`)}</span>`,
      `<span><i class="sq"></i>${escape(labels.highest_hour ?? "")}</span>`,
      counting.size ? `<span><i class="sq on"></i>${escape(labels.counting ?? "")}</span>` : "",
      over ? `<span><i class="sq hot"></i>${escape(labels.over_limit ?? "")}</span>` : "",
      data.runs?.size ? `<span>${markSwatch}${escape(labels.ran_here ?? "")}</span>` : "",
    ].join("");
    this.shadowRoot!.innerHTML = `${style}<style>${MARK_CSS}
        ha-card { position: relative; }
        .carpet { display: block; overflow: visible; }
        .axis.on { fill: var(--primary-text-color); font-weight: 500; }
        .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); }
        .legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
        .grad { width: 40px; height: 8px; border-radius: 3px; background: linear-gradient(90deg, rgba(var(--rgb-primary-color, 0, 154, 199), .07), rgba(var(--rgb-primary-color, 0, 154, 199), .95)); }
        .sq { width: 9px; height: 9px; border-radius: 2px; border: 1px solid rgba(var(--rgb-primary-text-color, 20, 20, 20), .55); }
        .sq.on { border: 2px solid var(--primary-text-color); width: 7px; height: 7px; }
        .sq.hot { background: var(--error-color); border-color: var(--error-color); }
      </style><ha-card><div class="pp-content">
        <div class="pp-sub">${escape(labels.carpet_hint ?? "")}</div>
        <svg class="carpet" width="${width}" height="${height.toFixed(0)}" viewBox="0 0 ${width} ${height.toFixed(0)}" role="img" aria-label="${escape(labels.carpet_hint ?? "")}">${g.join("")}</svg>
        <div class="legend">${legend}</div>
      </div></ha-card>`;
    this.tip ??= new ChartTip(this.shadowRoot!, () => this.shadowRoot!.querySelector("ha-card"));
    this.tip.reset();
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
    if (data?.carpet?.length) {
      this.renderCarpet(hass, config, data.carpet, data, style);
      return;
    }
    if (!data || (!data.days.length && !data.hours.length)) {
      const text = data ? withoutDate(labels.collecting ?? "") : "";
      this.shadowRoot!.innerHTML = `${style}<ha-card><div class="pp-content">${text ? `<div class="pp-empty">${escape(text)}</div>` : ""}</div></ha-card>`;
      return;
    }
    const primary = cssVar(this, "--primary-color", "#009ac7");
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
            // Iteration 5: the third day is the marker on the bar right above.
            text: fill(labels.not_counts_month ?? "", { month: monthName.format(data.period.start) }),
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
