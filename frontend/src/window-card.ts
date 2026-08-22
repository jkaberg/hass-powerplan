// `powerplan-window-card` (D12 §5.3): the capacity window. `mode: hour` -
// this hour's used kWh against the ceiling, the projection as a paler arc
// with a tick at its end, a status chip in the ladder stage's colour, and a
// footer of three facts. `mode: month` - the period's metric on the tariff's
// steps, the month's top three days and what separates it from the next step.
// Drawn in an SVG viewBox, so the whole arc and its footer fit a phone.
// Tapping opens the more-info dialog of what the gauge shows.

import { cssVar, type HomeAssistant, moreInfo, numeric, timeZone } from "./ha";
import {
  adviceItem,
  arcPoint,
  gauge,
  monthGauge,
  stageTone,
  stageWord,
  type TariffStep,
  toKw,
  TONE_COLOR,
  topEntries,
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
  mode?: "hour" | "month";
  entities: Partial<Record<Key, string>>;
  labels?: Record<string, string>;
}

/** The arc's radius and stroke in viewBox units: the box is 300 wide (D12 §5.3). */
const R = 105;
const STROKE = 22;
/** The month gauge's radius: its step labels sit outside the arc. */
const RM = 100;

function arc(from: number, to: number, r = R): string {
  const [x0, y0] = arcPoint(from, r);
  const [x1, y1] = arcPoint(to, r);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

const escape = (value: string) =>
  value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

const fill = (text: string, values: Record<string, string>) =>
  text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);

export class PowerplanWindowCard extends HTMLElement {
  private config?: WindowConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];

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
    if (key.length === this.key.length && key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.render();
  }

  public getCardSize(): number {
    return 6;
  }

  public getGridOptions(): Record<string, number> {
    return { columns: 12, rows: 6, min_rows: 5, min_columns: 6 };
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
    if (config.mode === "month") {
      this.renderMonth(hass, config);
      return;
    }
    const labels = config.labels ?? {};
    const state = (key: Key) => {
      const id = config.entities[key];
      return id ? hass.states[id] : undefined;
    };
    const usedEntity = state("window_used");
    const used = numeric(usedEntity) ?? 0;
    const projected = numeric(state("window_projected"));
    const ceiling = numeric(state("ceiling"));
    const tone = stageTone(numeric(state("stage")));
    const scale = gauge(used, projected, ceiling);
    const color = scale.over ? TONE_COLOR.alert : TONE_COLOR[tone];
    const locale = hass.locale.language;
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: timeZone(hass) });
    const hasCeiling = ceiling !== null && ceiling > 0;

    // The status chip: the stage in words, or the peak to come while the warning is on.
    const next = state("next_peak_warning")?.state;
    const chip =
      state("peak_warning")?.state === "on" && next && !Number.isNaN(Date.parse(next))
        ? fill(labels.peak_expected ?? "{time}", { time: clock.format(Date.parse(next)) })
        : (labels[`stage_${stageWord(scale.over ? "alert" : tone)}`] ?? "");

    // The footer's three facts; the allowance is always kW (B8).
    const remaining = Number(usedEntity?.attributes.t_rem_min);
    const allowanceEntity = state("allowance");
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

    const track = cssVar(this, "--primary-text-color", "#212121");
    const [px, py] = scale.projected === null ? [0, 0] : arcPoint(scale.projected, R - STROKE / 2 - 4);
    const [qx, qy] = scale.projected === null ? [0, 0] : arcPoint(scale.projected, R + STROKE / 2 + 4);
    const reading = `${two.format(used)} kWh`;
    const caption = hasCeiling ? fill(labels.used_of ?? "{ceiling}", { ceiling: one.format(ceiling) }) : "";
    this.shadowRoot!.innerHTML = `
      <style>
        :host { display: block; height: 100%; cursor: pointer; }
        ha-card { height: 100%; box-sizing: border-box; padding: 12px 16px 12px;
                  display: flex; flex-direction: column; }
        .top { display: flex; justify-content: flex-end; min-height: 24px; }
        .chip { font-size: 12px; font-weight: 500; padding: 2px 10px; border-radius: 12px;
                color: ${color}; background: color-mix(in srgb, ${color} 18%, transparent); }
        .gauge { flex: 1; display: flex; align-items: center; justify-content: center; min-height: 0; }
        svg { width: 100%; max-width: 430px; max-height: 100%; }
        .track { fill: none; stroke: ${track}; stroke-opacity: 0.08; stroke-width: ${STROKE}; }
        .used { fill: none; stroke: ${color}; stroke-width: ${STROKE}; }
        .projected { fill: none; stroke: ${color}; stroke-opacity: 0.38; stroke-width: ${STROKE}; }
        .tick { stroke: var(--primary-text-color); stroke-width: 2; }
        .big { font-size: 40px; font-weight: 500; fill: var(--primary-text-color); }
        .caption, .end { font-size: 13px; fill: var(--secondary-text-color); }
        .footer { display: grid; grid-template-columns: repeat(${Math.max(cells.length, 1)}, 1fr);
                  gap: 8px; border-top: 1px solid var(--divider-color); padding-top: 8px; }
        .cell { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
        .name { font-size: 12px; color: var(--secondary-text-color); display: flex; align-items: center; gap: 6px; }
        .value { font-size: 15px; font-weight: 500; color: var(--primary-text-color); }
        .swatch { width: 10px; height: 10px; border-radius: 2px; background: ${color}; opacity: 0.38; flex: none; }
      </style>
      <ha-card>
        <div class="top">${chip ? `<span class="chip">${escape(chip)}</span>` : ""}</div>
        <div class="gauge">
          <svg viewBox="-150 -125 300 160" role="img" aria-label="${escape(`${reading} ${caption}`)}">
            <path class="track" d="${arc(0, 1)}"/>
            ${scale.projected !== null && scale.projected > scale.used ? `<path class="projected" d="${arc(scale.used, scale.projected)}"/>` : ""}
            ${scale.used > 0 ? `<path class="used" d="${arc(0, scale.used)}"/>` : ""}
            ${scale.projected !== null ? `<line class="tick" x1="${px}" y1="${py}" x2="${qx}" y2="${qy}"/>` : ""}
            <text class="big" x="0" y="-18" text-anchor="middle">${escape(reading)}</text>
            ${caption ? `<text class="caption" x="0" y="6" text-anchor="middle">${escape(caption)}</text>` : ""}
            <text class="end" x="${-R}" y="${STROKE + 8}" text-anchor="middle">0</text>
            ${hasCeiling ? `<text class="end" x="${R}" y="${STROKE + 8}" text-anchor="middle">${escape(`${one.format(ceiling)} kWh`)}</text>` : ""}
          </svg>
        </div>
        ${
          cells.length
            ? `<div class="footer">${cells
                .map(
                  ([name, value, swatch]) =>
                    `<div class="cell"><span class="name">${swatch ? '<span class="swatch"></span>' : ""}${escape(name)}</span><span class="value">${escape(value)}</span></div>`,
                )
                .join("")}</div>`
            : ""
        }
      </ha-card>`;
  }

  private renderMonth(hass: HomeAssistant, config: WindowConfig): void {
    const labels = config.labels ?? {};
    const state = (key: Key) => {
      const id = config.entities[key];
      return id ? hass.states[id] : undefined;
    };
    const locale = hass.locale.language;
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const kw = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
    const day = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" });
    const month = new Intl.DateTimeFormat(locale, { month: "long", timeZone: timeZone(hass) });
    const metric = numeric(state("metric")) ?? 0;
    const level = state("level");
    const steps = (level?.attributes.steps as TariffStep[] | undefined) ?? [];
    const targetKw = Number(state("target")?.attributes.target_kw);
    const items = state("advice")?.attributes.items;
    const top = topEntries(items).slice(0, 3);
    const headroom = adviceItem(items, "step_headroom");
    const tips = adviceItem(items, "days_that_matter");
    const text = cssVar(this, "--primary-text-color", "#212121");

    let arcs = "";
    let ticks = "";
    let bars = "";
    let needle = "";
    if (steps.length) {
      const g = monthGauge(metric, steps, Number.isFinite(targetKw) ? targetKw : null);
      const gap = 2 / (Math.PI * RM);
      arcs = g.segments
        .map((seg) => {
          const from = seg.from / g.max + (seg.from > 0 ? gap / 2 : 0);
          const to = seg.to / g.max - (seg.to < g.max ? gap / 2 : 0);
          return `<path d="${arc(from, to, RM)}" fill="none" stroke="${TONE_COLOR[seg.tone]}" stroke-width="16" stroke-opacity="${seg.current ? 1 : 0.28}"/>`;
        })
        .join("");
      ticks = [0, ...g.ticks, g.max]
        .map((value) => {
          const [x, y] = arcPoint(value / g.max, RM + 18);
          const label = value === g.max ? `${kw.format(value)} kW` : kw.format(value);
          return `<text class="end" x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="middle">${escape(label)}</text>`;
        })
        .join("");
      const [nx, ny] = arcPoint(g.needle, RM - 14);
      needle = `<line x1="0" y1="0" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="${text}" stroke-width="3" stroke-linecap="round"/><circle cx="0" cy="0" r="6" fill="${text}"/>`;
      if (top.length) {
        const width = (value: number) => `${Math.min(100, (100 * value) / g.barMax).toFixed(1)}%`;
        const line = g.upper === null ? "" : `<span class="limit" style="left:${width(g.upper)}"></span>`;
        bars = `<div class="top3"><div class="name">${escape(fill(labels.top3 ?? "{month}", { month: month.format(Date.now()) }))}</div>${top
          .map(
            ([date, value]) =>
              `<div class="row"><span>${escape(day.format(Date.parse(date)))}</span><span class="bar"><span style="width:${width(value)}"></span>${line}</span><span>${escape(two.format(value))}</span></div>`,
          )
          .join("")}</div>`;
      }
    }
    const reading = `${two.format(metric)} kW`;
    const caption = level?.state ? fill(labels.metric_label ?? "{level}", { level: level.state }) : "";
    const footer: string[] = [];
    if (headroom) {
      footer.push(
        fill(labels.to_next_step ?? "", {
          kw: two.format(Number(headroom.to_next_kw)),
          next: String(headroom.next_name ?? ""),
          fee: kw.format(Number(headroom.fee_delta)),
          currency: String(headroom.currency ?? ""),
        }),
      );
    }
    if (tips) footer.push(fill(labels.day_that_tips ?? "", { kw: two.format(Number(tips.kw)) }));
    this.shadowRoot!.innerHTML = `
      <style>
        :host { display: block; height: 100%; cursor: pointer; }
        ha-card { height: 100%; box-sizing: border-box; padding: 12px 16px; display: flex;
                  flex-direction: column; gap: 8px; }
        svg { width: 100%; max-width: 430px; align-self: center; }
        .big { font-size: 28px; font-weight: 500; fill: var(--primary-text-color); }
        .caption, .end { font-size: 12px; fill: var(--secondary-text-color); }
        .top3 { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
        .name { color: var(--secondary-text-color); font-size: 12px; }
        .row { display: grid; grid-template-columns: 56px 1fr 44px; align-items: center; gap: 8px; }
        .row span:last-child { text-align: right; }
        .bar { position: relative; height: 8px; border-radius: 4px; background: var(--divider-color); }
        .bar > span:first-child { position: absolute; inset: 0 auto 0 0; border-radius: 4px; background: var(--primary-color); }
        .limit { position: absolute; top: -3px; bottom: -3px; border-left: 2px dashed var(--warning-color); }
        .footer { display: flex; gap: 8px; border-top: 1px solid var(--divider-color); padding-top: 8px;
                  font-size: 13px; color: var(--secondary-text-color); }
        .footer ha-icon { --mdc-icon-size: 18px; color: var(--warning-color); flex: none; }
      </style>
      <ha-card>
        <svg viewBox="-150 -135 300 ${steps.length ? 200 : 150}" role="img" aria-label="${escape(`${reading} ${caption}`)}">
          ${arcs}${ticks}${needle}
          <text class="big" x="0" y="${steps.length ? 36 : -20}" text-anchor="middle">${escape(reading)}</text>
          ${caption ? `<text class="caption" x="0" y="${steps.length ? 56 : 0}" text-anchor="middle">${escape(caption)}</text>` : ""}
        </svg>
        ${bars}
        ${footer.length ? `<div class="footer"><ha-icon icon="mdi:lightbulb-outline"></ha-icon><span>${escape(footer.join(" "))}</span></div>` : ""}
      </ha-card>`;
  }
}
