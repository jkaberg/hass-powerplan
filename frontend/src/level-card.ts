// `powerplan-level-card` (D12 §5.20 V7): an appliance's level - a temperature, or a state of charge -
// over the past 24 hours and the plan for the next 12. The level comes from HA's own history of the
// entity (or attribute) the type's comfort reads (D-0697); under it a lane: solid while it drew on its
// plan, hatched while PowerPlan made it wait, and the hours ahead from `sensor.<site>_plan`. The comfort
// and the minimum are today's values, dashed.

import { type HomeAssistant, timeZone } from "./ha";
import { ChartTip, MARK_CSS, tipAttr } from "./marks";
import { readPlan } from "./r3-util";
import { ppStyles } from "./styles";
import { hatchDef } from "./tokens";
import { type HistoryRow, laneRuns, levelSeries } from "./transforms";

interface LevelConfig {
  entry_id?: string;
  load: { id: string; name: string; color: string; kind: string };
  level: { entity: string; attribute?: string; unit: string };
  entities: { status: string; plan?: string; charge_target?: string; charge_min?: string };
  labels?: Record<string, string>;
}

const PAST = 24 * 3_600_000;
const AHEAD = 12 * 3_600_000;
const REFETCH = 5 * 60_000;
const escape = (value: string) => value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const fill = (text: string, values: Record<string, string>) => text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);
const num = (v: unknown) => { const n = typeof v === "number" ? v : parseFloat(String(v ?? "")); return Number.isFinite(n) ? n : null; };

export class PowerplanLevelCard extends HTMLElement {
  private config?: LevelConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];
  private width = 0;
  private resize?: ResizeObserver;
  private fetchedAt = 0;
  private busy = false;
  private level: Array<{ t: number; v: number }> = [];
  private states: HistoryRow[] = [];
  private tip?: ChartTip;

  public setConfig(config: LevelConfig): void {
    if (!config?.level?.entity || !config.entities?.status || !config.load?.id) {
      throw new Error("powerplan-level-card needs load, level.entity and entities.status");
    }
    this.config = config;
    this.key = [];
    this.fetchedAt = 0;
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    const c = this.config;
    if (!c) return;
    if (Date.now() - this.fetchedAt > REFETCH) void this.fetch();
    const e = c.entities;
    const key = [hass.states[c.level.entity], hass.states[e.status], e.plan && hass.states[e.plan], e.charge_target && hass.states[e.charge_target],
      hass.language, hass.themes.darkMode, this.width, this.fetchedAt];
    if (key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.render();
  }

  public connectedCallback(): void {
    this.style.display ||= "block";
    this.resize ??= new ResizeObserver((entries) => {
      const width = Math.round(entries[0]?.contentRect.width ?? 0);
      if (width && width !== this.width) {
        this.width = width;
        if (this.hassRef) this.hass = this.hassRef;
      }
    });
    this.resize.observe(this);
  }

  public disconnectedCallback(): void { this.resize?.disconnect(); }
  public getCardSize(): number { return 4; }
  public getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }

  /** HA's own history: the level's source (with attributes only when the level is one) and the plan status. */
  private async fetch(): Promise<void> {
    const hass = this.hassRef, c = this.config;
    if (!hass || !c || this.busy) return;
    this.busy = true;
    const now = Date.now();
    const ask = (ids: string[], attributes: boolean) => hass.callWS<Record<string, HistoryRow[]>>({
      type: "history/history_during_period", start_time: new Date(now - PAST).toISOString(), end_time: new Date(now).toISOString(),
      entity_ids: ids, minimal_response: !attributes, no_attributes: !attributes, significant_changes_only: false,
    }).catch(() => ({}) as Record<string, HistoryRow[]>);
    try {
      const [level, status] = await Promise.all([ask([c.level.entity], Boolean(c.level.attribute)), ask([c.entities.status], false)]);
      this.level = levelSeries(level[c.level.entity] ?? [], c.level.attribute);
      this.states = status[c.entities.status] ?? [];
    } finally {
      this.busy = false;
      this.fetchedAt = Date.now();
      this.key = [];
      if (this.hassRef) this.hass = this.hassRef;
    }
  }

  private render(): void {
    const hass = this.hassRef, c = this.config;
    if (!hass || !c || !this.width) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const labels = c.labels ?? {};
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const soc = c.level.unit === "%";
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: soc ? 0 : 1, maximumFractionDigits: soc ? 0 : 1 });
    const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: zone });
    const fmt = (v: number) => `${one.format(v)} ${c.level.unit}`;
    const status = hass.states[c.entities.status];
    const live = c.level.attribute ? num(hass.states[c.level.entity]?.attributes[c.level.attribute]) : num(hass.states[c.level.entity]?.state);
    const target = c.entities.charge_target ? num(hass.states[c.entities.charge_target]?.state) : num(status?.attributes.target);
    const floor = c.entities.charge_min ? num(hass.states[c.entities.charge_min]?.state) : num(status?.attributes.floor);
    const deadline = Date.parse(String(status?.attributes.deadline ?? ""));
    const now = Date.now(), t0 = now - PAST, t1 = now + AHEAD;
    const points = this.level.filter((p) => p.t >= t0 - 3_600_000);
    if (live !== null) points.push({ t: now, v: live });

    const width = this.width - 32, narrow = width < 368;
    const L = 30, R = 8, top = 22, ph = narrow ? 120 : 140, laneY = top + ph + 10, laneH = 9, H = laneY + laneH + 24;
    const values = [...points.map((p) => p.v), ...(target !== null ? [target] : []), ...(floor !== null ? [floor] : [])];
    const lo = soc ? 0 : Math.floor(Math.min(...values, live ?? Infinity) - 0.5);
    const hi = soc ? 100 : Math.ceil(Math.max(...values, live ?? -Infinity) + 0.5);
    const tick = soc ? 50 : hi - lo > 6 ? 2 : 1;
    const X = (t: number) => L + ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * (width - L - R);
    const Y = (v: number) => top + ph - ((v - lo) / Math.max(hi - lo, 1)) * ph;
    const g: string[] = [`<defs>${hatchDef("pp-wait")}</defs>`];
    if (Number.isFinite(lo) && Number.isFinite(hi)) {
      for (let v = Math.ceil(lo / tick) * tick; v <= hi; v += tick) {
        g.push(`<line x1="${L}" x2="${width - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="var(--divider-color)" stroke-dasharray="2 4"/>`);
        g.push(`<text class="axis" x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${escape(soc ? `${v}` : `${v}°`)}</text>`);
      }
    }
    const line = (v: number | null, stroke: string, text: string) => {
      if (v === null || v < lo || v > hi) return;
      g.push(`<line x1="${L}" x2="${width - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="${stroke}" stroke-width="1.5" stroke-dasharray="5 4"/>`);
      g.push(`<text class="axis" x="${width - R}" y="${(Y(v) - 6).toFixed(1)}" text-anchor="end">${escape(text)}</text>`);
    };
    line(target, "var(--success-color)", fill(labels.comfort_line ?? "{v}", { v: fmt(target ?? 0) }));
    // A minimum within a label's height of the comfort keeps its line and loses its label.
    const apart = target === null || floor === null || Math.abs(Y(target) - Y(floor)) >= 14;
    line(floor, "var(--error-color)", apart ? fill(labels.floor_line ?? "{v}", { v: fmt(floor ?? 0) }) : "");
    if (points.length > 1) {
      g.push(`<path d="${points.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join("")}" fill="none" stroke="var(--primary-text-color)" stroke-width="2" stroke-linejoin="round"/>`);
    }
    if (live !== null) g.push(`<circle cx="${X(now).toFixed(1)}" cy="${Y(live).toFixed(1)}" r="4" fill="var(--primary-text-color)" stroke="var(--card-background-color)" stroke-width="2"/>`);

    // The lane: the past from `plan_status`, the hours ahead from the plan.
    g.push(`<rect x="${L}" y="${laneY}" width="${width - L - R}" height="${laneH}" rx="4" fill="rgba(var(--rgb-primary-text-color, 20, 20, 20), .06)"/>`);
    const runWord = c.load.kind === "ev" || c.load.kind === "battery" ? labels.charging : labels.heating;
    for (const run of laneRuns(this.states, now)) {
      if (run.end <= t0) continue;
      const x0 = X(run.start), x1 = X(run.end);
      if (x1 - x0 < 0.5) continue;
      const lines = [`${clock.format(Math.max(run.start, t0))}–${clock.format(run.end)}`, run.kind === "run" ? runWord ?? "" : labels.waiting_pp ?? ""];
      g.push(`<rect x="${x0.toFixed(1)}" y="${laneY}" width="${(x1 - x0).toFixed(1)}" height="${laneH}" fill="${run.kind === "run" ? c.load.color : "url(#pp-wait)"}"${tipAttr(lines, false)}/>`);
    }
    const plan = c.entities.plan ? readPlan(hass.states[c.entities.plan] as never).slots : [];
    for (const slot of plan) {
      const s = slot.start.getTime(), e = slot.end.getTime();
      if (e <= now || s >= t1) continue;
      const x0 = X(Math.max(s, now)), x1 = X(e);
      if ((slot.planned[c.load.id] ?? 0) > 0.0005) {
        g.push(`<rect x="${x0.toFixed(1)}" y="${laneY}" width="${(x1 - x0).toFixed(1)}" height="${laneH}" fill="${c.load.color}"${tipAttr([`${clock.format(s)}–${clock.format(e)}`, labels.plan_run ?? ""], false)}/>`);
      } else if (slot.paused.includes(c.load.id)) {
        g.push(`<rect x="${x0.toFixed(1)}" y="${laneY}" width="${(x1 - x0).toFixed(1)}" height="${laneH}" fill="url(#pp-wait)"/>`);
      } else if ((slot.hold[c.load.id] ?? 0) > 0.0005) {
        g.push(`<rect x="${x0.toFixed(1)}" y="${(laneY + laneH * 0.3).toFixed(1)}" width="${(x1 - x0).toFixed(1)}" height="${(laneH * 0.4).toFixed(1)}" fill="${c.load.color}" fill-opacity=".4"/>`);
      }
    }
    if (Number.isFinite(deadline) && deadline > now && deadline <= t1) {
      const x = X(deadline), text = fill(labels.deadline ?? "{time}", { time: clock.format(deadline) });
      g.push(`<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${top}" y2="${laneY + laneH}" stroke="var(--warning-color)" stroke-width="1.5" stroke-dasharray="4 3"/>`);
      g.push(`<text class="axis" x="${(x - 4).toFixed(1)}" y="${top + 10}" text-anchor="end">${escape(text)}</text>`);
    }
    // "Nå" and the clock under the lane.
    const xn = X(now);
    g.push(`<line x1="${xn.toFixed(1)}" x2="${xn.toFixed(1)}" y1="${top - 4}" y2="${laneY + laneH}" stroke="rgba(var(--rgb-primary-text-color, 20, 20, 20), .45)"/>`);
    g.push(`<rect x="${(xn - 14).toFixed(1)}" y="${top - 20}" width="28" height="16" rx="8" fill="var(--primary-text-color)"/>`);
    g.push(`<text x="${xn.toFixed(1)}" y="${top - 8.5}" text-anchor="middle" class="flag">${escape(labels.now_short ?? "Nå")}</text>`);
    const every = (narrow ? 8 : 4) * 3_600_000;
    for (let t = Math.ceil(t0 / 3_600_000) * 3_600_000; t <= t1; t += 3_600_000) {
      const hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone: zone }).format(t));
      if (hour % (every / 3_600_000) || X(t) < L + 12 || X(t) > width - R - 14) continue;
      g.push(`<text class="axis" x="${X(t).toFixed(1)}" y="${laneY + laneH + 16}" text-anchor="middle">${escape(clock.format(t))}</text>`);
    }
    const lowest = points.length ? Math.min(...points.map((p) => p.v)) : null;
    const sub = !soc && lowest !== null ? fill(labels.lowest_24h ?? "{v}", { v: fmt(lowest) }) : "";
    this.shadowRoot!.innerHTML = `<style>${ppStyles}${MARK_CSS}
        :host { --pp-hatch: rgba(var(--rgb-primary-text-color, 20, 20, 20), .5); }
        ha-card { position: relative; }
        .pp-content { display: flex; flex-direction: column; gap: 10px; }
        .head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
        .head .r { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; margin-top: 8px; }
        svg { display: block; overflow: visible; }
        .axis { font-size: 11px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
        .flag { font-size: 10px; font-weight: 500; fill: var(--card-background-color); }
        .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); }
        .legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
        .legend i { width: 10px; height: 10px; border-radius: 3px; }
        .legend i.wait { background: repeating-linear-gradient(45deg, var(--pp-hatch) 0 2px, transparent 2px 5px); box-shadow: inset 0 0 0 1px var(--pp-hatch); }
      </style><ha-card><div class="pp-content">
        <div class="head"><div><span class="pp-stat-value">${escape(live !== null ? one.format(live) : "–")}<span class="pp-stat-unit">${escape(c.level.unit)}</span></span>
          ${sub ? `<div class="pp-sub">${escape(sub)}</div>` : ""}</div>
          ${target !== null ? `<div class="r">${escape(fill(labels.target_short ?? "{v}", { v: fmt(target) }))}</div>` : ""}</div>
        <svg width="${width}" height="${H}" viewBox="0 0 ${width} ${H}" role="img" aria-label="${escape(`${c.load.name}: ${live !== null ? fmt(live) : "–"}`)}">${g.join("")}</svg>
        <div class="legend"><span><i style="background:${escape(c.load.color)}"></i>${escape(runWord ?? "")}</span>
          <span><i class="wait"></i>${escape(labels.waiting_pp ?? "")}</span>
          <span><i style="background:${escape(c.load.color)};opacity:.4"></i>${escape(labels.plan_hold ?? "")}</span></div>
      </div></ha-card>`;
    this.tip ??= new ChartTip(this.shadowRoot!, () => this.shadowRoot!.querySelector("ha-card"));
    this.tip.reset();
  }
}
