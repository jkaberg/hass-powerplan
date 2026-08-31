// `powerplan-appliances-card` (D12 §5.12 R1–R6): one row per appliance, each
// with its next 24 h as a lane on the same time axis and the same left rail
// width as the Plan card above it. A tap opens the appliance's dialog. It
// replaces Now's ten tiles, the power split and the next-runs list: the lanes
// show every run with its time, kWh and cost.
//
// Data, all published already: `sensor.<site>_plan` (`slots`, `by_load`), each
// `plan_status` (state, `current`, `target`, `comfort_state`, `granted_power`,
// `deadline_time`), `sensor.<site>_price_forecast` for the cheap-hour bands.

import { openApplianceDialog } from "./appliance-dialog";
import type { HomeAssistant } from "./ha";
import { timeZone } from "./ha";
import { KIND_ORDER, type Kind, rawStatus, StatusDebouncer, type StatusView } from "./status";
import { ppStyles } from "./styles";
import {
  type ByLoad,
  cheapBands,
  holdRuns,
  localHour,
  moneyFormat,
  nextClock,
  type PlanSlot,
  planRuns,
  type PriceSlot,
  readable,
  type Run,
  withAlpha,
} from "./transforms";

export interface ApplianceLoad {
  id: string;
  name: string;
  color: string;
  icon?: string;
  /** The D4 type key, for the dialog's subtitle. */
  kind?: string;
  /** The type's name in the household's language. */
  kind_name?: string;
  /** The room, from the registries. */
  area?: string;
  status: string;
  control?: string;
  ready_by?: string;
  cost_month?: string;
  savings_month?: string;
  energy?: string;
  next_legionella?: string;
  /** The appliance's subview, `{dashboard}` already rewritten by the strategy. */
  path?: string;
}

export interface AppliancesConfig {
  entry_id: string;
  entities: { plan: string; price_forecast?: string };
  loads: ApplianceLoad[];
  hours?: number;
  /** Must equal the Plan card's rail so the two time axes line up (R2). */
  rail_width?: number;
  default_filter?: "active" | "all";
  currency?: string;
  /** Each strategy's words, for the dialog's "why" (`strategy_<key>`). */
  strategies?: Record<string, string>;
  labels?: Record<string, string>;
}

interface Row {
  load: ApplianceLoad;
  view: StatusView;
  runs: Run[];
  /** Holding its setpoint (D-0501): drawn as a faint band under the runs. */
  holds: Run[];
  sub: string;
  /** "2,90 kWh · 2,11 kr" */
  meta: string;
  cost: string;
  deadline: number | null;
  nextStart: number;
}

const FILTER_KEY = "powerplan-appliances-filter";
const HOUR_MS = 3_600_000;

export const escape = (value: unknown) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export const fill = (text: string, values: Record<string, string | number>) =>
  text.replace(/\{(\w+)\}/g, (whole, key: string) => (values[key] === undefined ? whole : String(values[key])));

export const toNumber = (value: unknown): number | null => {
  const n = typeof value === "number" ? value : parseFloat(String(value ?? ""));
  return Number.isFinite(n) ? n : null;
};

export class PowerplanAppliancesCard extends HTMLElement {
  private config?: AppliancesConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];
  private width = 0;
  private resize?: ResizeObserver;
  private debouncer = new StatusDebouncer();
  private uid = `ppa${Math.random().toString(36).slice(2, 8)}`;
  private filter: "active" | "all" = "active";
  private flipTimer?: number;

  public setConfig(config: AppliancesConfig): void {
    if (!config?.entities?.plan) throw new Error("powerplan-appliances-card needs entities.plan");
    if (!Array.isArray(config.loads)) throw new Error("powerplan-appliances-card needs loads");
    this.config = config;
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(FILTER_KEY);
    } catch {
      // private mode: the default
    }
    this.filter = stored === "all" || stored === "active" ? stored : (config.default_filter ?? "active");
    this.key = [];
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    const config = this.config;
    if (!config) return;
    const ids = [config.entities.plan, config.entities.price_forecast, ...config.loads.flatMap((l) => [l.status, l.control, l.ready_by])];
    const key = [
      ...ids.map((id) => (id ? hass.states[id] : undefined)),
      hass.language,
      hass.themes.darkMode,
      Math.floor(Date.now() / 60_000),
      this.width,
      this.filter,
    ];
    if (key.length === this.key.length && key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.render();
  }

  public connectedCallback(): void {
    this.resize ??= new ResizeObserver((entries) => {
      const width = Math.round(entries[0]!.contentRect.width);
      if (width && width !== this.width) {
        this.width = width;
        this.redraw();
      }
    });
    this.resize.observe(this);
  }

  public disconnectedCallback(): void {
    this.resize?.disconnect();
    if (this.flipTimer) clearTimeout(this.flipTimer);
  }

  public getCardSize(): number {
    return 2 + (this.config?.loads.length ?? 4);
  }

  public getGridOptions(): Record<string, number | string> {
    return { columns: "full", rows: "auto" };
  }

  private redraw(): void {
    this.key = [];
    if (this.hassRef) this.hass = this.hassRef;
  }

  // ---------------------------------------------------------------- data

  private rows(now: number, until: number): Row[] {
    const hass = this.hassRef!;
    const config = this.config!;
    const labels = config.labels ?? {};
    const zone = timeZone(hass);
    const locale = hass.locale.language;
    const plan = hass.states[config.entities.plan]?.attributes ?? {};
    const slots = (plan.slots as PlanSlot[] | undefined) ?? [];
    const byLoad = (plan.by_load as Record<string, ByLoad> | undefined) ?? {};
    const kwh = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits: 1 });
    const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: zone });
    const money = moneyFormat(locale, config.currency ?? "");
    return config.loads
      .map((load) => {
        const status = hass.states[load.status];
        const a = status?.attributes ?? {};
        const runs = planRuns(slots, load.id).filter((run) => run.end > now && run.start < until);
        const runNow = runs.some((run) => run.start <= now && run.end > now);
        const holds = holdRuns(slots, load.id).filter((run) => run.end > now && run.start < until);
        const holdNow = holds.some((run) => run.start <= now && run.end > now);
        const view = this.debouncer.view(load.id, rawStatus(status?.state, a, runNow, runs.length > 0, labels, holdNow));
        const parts: string[] = [];
        const current = toNumber(a.current);
        const target = toNumber(a.target);
        if (current !== null && target !== null) {
          parts.push(
            a.comfort_state === "below_target"
              ? `${one.format(current)} → ${one.format(target)} °C`
              : `${one.format(current)} °C · ${fill(labels.target_short ?? "{v}", { v: one.format(target) })}`,
          );
        } else if (view.kind === "running" && toNumber(a.granted_power)) {
          parts.push(`${one.format(toNumber(a.granted_power)! / 1000)} kW`);
        }
        let deadline: number | null = null;
        const due = String(a.deadline_time || (load.ready_by ? hass.states[load.ready_by]?.state : "") || "");
        if (due && (view.kind === "running" || view.kind === "planned" || view.kind === "waiting")) {
          deadline = nextClock(due, now, zone);
          if (deadline !== null) parts.push(fill(labels.due ?? "{time}", { time: clock.format(deadline) }));
        }
        if (view.kind === "paused" || view.kind === "manual") parts.splice(0, parts.length, view.reason ?? "");
        const row = byLoad[load.id];
        const planned = toNumber(row?.planned_kwh ?? a.planned_kwh);
        const amount = toNumber(String(row?.cost ?? a.cost ?? "").split(" ")[0]);
        const cost = amount === null ? "" : money.format(amount);
        const meta = planned ? `${kwh.format(planned)} kWh${cost ? ` · ${cost}` : ""}` : "";
        return {
          load,
          view,
          runs,
          holds,
          sub: parts.filter(Boolean).join(" · "),
          meta,
          cost,
          deadline,
          nextStart: runs.length ? runs[0]!.start : Number.MAX_SAFE_INTEGER,
        };
      })
      .sort(
        (x, y) =>
          KIND_ORDER.indexOf(x.view.kind) - KIND_ORDER.indexOf(y.view.kind) ||
          x.nextStart - y.nextStart ||
          x.load.name.localeCompare(y.load.name),
      );
  }

  // ---------------------------------------------------------------- render

  private render(): void {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    if (!this.shadowRoot) {
      const root = this.attachShadow({ mode: "open" });
      root.addEventListener("click", (event) => this.onClick(event));
      root.addEventListener("keydown", (event) => {
        const key = (event as KeyboardEvent).key;
        if (key === "Enter" || key === " ") {
          event.preventDefault();
          this.onClick(event);
        }
      });
    }
    const W = this.width || this.getBoundingClientRect().width || 800;
    const compact = W < 600;
    const labels = config.labels ?? {};
    const zone = timeZone(hass);
    const hours = config.hours ?? 24;
    const now = Date.now();
    const a0 = Math.floor(now / HOUR_MS) * HOUR_MS;
    const a1 = a0 + hours * HOUR_MS;
    const rows = this.rows(now, a1);
    const count = (kind: Kind) => rows.filter((row) => row.view.kind === kind).length;
    const idle = rows.filter((row) => row.view.kind === "idle");
    const shown = this.filter === "all" ? rows : rows.filter((row) => row.view.kind !== "idle");
    const rail = compact ? 0 : (config.rail_width ?? (W >= 1000 ? 300 : 240));
    const lx0 = compact ? 60 : rail + 8;
    const lx1 = W - (compact ? 12 : 16);
    const sx = (t: number) => lx0 + ((t - a0) / (a1 - a0)) * (lx1 - lx0);
    const prices = (config.entities.price_forecast
      ? (hass.states[config.entities.price_forecast]?.attributes.slots as PriceSlot[] | undefined)
      : undefined) ?? [];
    const bands = cheapBands(prices, a0, a1);
    const clock = new Intl.DateTimeFormat(hass.locale.language, { hour: "2-digit", minute: "2-digit", timeZone: zone });
    const dark = hass.themes.darkMode;
    const U = this.uid;

    // ---- the head: counters, the cheap-hours key, the filter
    const counters = (
      [
        ["running", labels.count_running],
        ["waiting", labels.count_waiting],
        ["planned", labels.count_planned],
        ["holding", labels.count_holding],
        ["paused", labels.count_paused],
        ["manual", labels.count_manual],
        ["idle", labels.count_idle],
      ] as Array<[Kind, string | undefined]>
    )
      .filter(([kind]) => count(kind) > 0 && (!compact || kind === "running" || kind === "planned"))
      .map(([kind, text]) => `<span class="cnt"><i class="dot ${kind}"></i>${escape(fill(text ?? "{n}", { n: count(kind) }))}</span>`)
      .join("");
    const filters: Array<["active" | "all", string, number]> = [
      ["active", labels.filter_active ?? "", rows.length - idle.length],
      ["all", labels.filter_all ?? "", rows.length],
    ];
    const seg = filters
      .map(
        ([filter, text, n]) =>
          `<button type="button" class="seg" data-filter="${filter}" aria-pressed="${this.filter === filter}">${escape(text)}${compact ? "" : ` (${n})`}</button>`,
      )
      .join("");
    const legend = bands.length && !compact ? `<span class="legend"><i class="sw cheap"></i>${escape(labels.cheap_hours ?? "")}</span>` : "";

    // ---- the time axis
    const ticks: string[] = [];
    const every = compact ? 6 : 3;
    const xn = sx(now);
    for (let t = a0; t <= a1; t += HOUR_MS) {
      if (Math.round(localHour(t, zone)) % every) continue;
      const x = sx(t);
      if ((!compact && Math.abs(x - xn) < 38) || x < lx0 + 8 || x > lx1 - 8) continue;
      ticks.push(`<text x="${x.toFixed(1)}" y="16" class="tick" text-anchor="middle">${escape(clock.format(t))}</text>`);
    }
    const axis = `<svg class="axis" width="${W}" height="26" viewBox="0 0 ${W} 26">${ticks.join("")}
      ${compact ? "" : `<rect x="${(xn - 14).toFixed(1)}" y="3" width="28" height="16" rx="8" class="nowpill"/>
      <text x="${xn.toFixed(1)}" y="14.5" class="nowtxt" text-anchor="middle">${escape(labels.now ?? "")}</text>`}</svg>`;

    // ---- the rows
    const RH = compact ? 70 : 56;
    const laneH = compact ? 10 : 22;
    const laneY = compact ? 50 : (RH - laneH) / 2;
    const rowHtml = shown
      .map((row) => {
        const { load, view } = row;
        const color = load.color || "#9e9e9e";
        const icon = load.icon || "mdi:flash";
        const rr = compact ? laneH / 2 : 5;
        const bandsSvg = bands
          .map(([s, e]) => {
            const x0 = Math.max(sx(s), lx0);
            const x1 = Math.min(sx(e), lx1);
            return x1 > x0 ? `<rect x="${x0.toFixed(1)}" y="0" width="${(x1 - x0).toFixed(1)}" height="${RH}" class="band"/>` : "";
          })
          .join("");
        let lane = `<rect x="${lx0}" y="${laneY}" width="${lx1 - lx0}" height="${laneH}" rx="${rr}" class="track"/>`;
        const mid = laneY + laneH / 2 + 4;
        if (view.kind === "manual") {
          lane += `<rect x="${lx0}" y="${laneY}" width="${lx1 - lx0}" height="${laneH}" rx="${rr}" style="fill:url(#${U}-man)"/>`;
          if (!compact) lane += `<text x="${(lx0 + lx1) / 2}" y="${mid}" class="ltxt dim" text-anchor="middle">${escape(view.reason)}</text>`;
        } else if (view.kind === "idle" && !compact) {
          lane += `<text x="${(lx0 + lx1) / 2}" y="${mid}" class="ltxt faint" text-anchor="middle">${escape(fill(labels.no_need ?? "", { h: hours }))}</text>`;
        }
        for (const run of row.holds) {
          const x0 = Math.max(sx(run.start), lx0);
          const x1 = Math.min(sx(run.end), lx1);
          // Thinner and fainter than a run: the thermostat's own draw, not a decision (D-0501).
          const h = Math.max(laneH * 0.4, 3);
          if (x1 > x0) lane += `<rect x="${x0.toFixed(1)}" y="${(laneY + (laneH - h) / 2).toFixed(1)}" width="${(x1 - x0).toFixed(1)}" height="${h.toFixed(1)}" rx="${(h / 2).toFixed(1)}" style="fill:${withAlpha(color, 0.3)}"/>`;
        }
        for (const run of row.runs) {
          const x0 = Math.max(sx(run.start), lx0);
          const x1 = Math.min(sx(run.end), lx1);
          lane += `<rect x="${(x0 + 0.5).toFixed(1)}" y="${laneY}" width="${Math.max(x1 - x0 - 1, 3).toFixed(1)}" height="${laneH}" rx="${rr}" style="fill:${color}"/>`;
          if (run.start <= now && run.end > now) {
            lane += `<rect x="${(x0 + 0.5).toFixed(1)}" y="${laneY}" width="${Math.max(xn - x0, 0).toFixed(1)}" height="${laneH}" rx="${rr}" class="elapsed"/>`;
          }
        }
        if (view.kind === "paused") {
          const half = (lx1 - lx0) * (10 / (hours * 60));
          const x0 = Math.max(xn - half, lx0);
          const x1 = Math.min(xn + half, lx1);
          lane += `<rect x="${x0.toFixed(1)}" y="${laneY + 0.5}" width="${(x1 - x0).toFixed(1)}" height="${laneH - 1}" rx="${rr}" class="shed" style="fill:url(#${U}-shed)"/>`;
          if (!compact) lane += `<text x="${(x1 + 8).toFixed(1)}" y="${mid}" class="ltxt warn">${escape(view.reason)}</text>`;
        }
        let dlx = Infinity;
        if (row.deadline !== null && row.deadline > a0 && row.deadline < a1) {
          dlx = sx(row.deadline);
          lane += `<line x1="${dlx.toFixed(1)}" y1="${laneY - 5}" x2="${dlx.toFixed(1)}" y2="${laneY + laneH + 5}" class="deadline"/>`;
          if (!compact) lane += `<text x="${(dlx + 6).toFixed(1)}" y="${mid}" class="ltxt amber">${escape(fill(labels.due ?? "{time}", { time: clock.format(row.deadline) }))}</text>`;
        }
        if (!compact && row.runs.length) {
          const last = row.runs[row.runs.length - 1]!;
          const xe = Math.min(sx(last.end), lx1);
          const when =
            row.runs.length === 1
              ? `${clock.format(row.runs[0]!.start)}–${clock.format(row.runs[0]!.end)}`
              : row.runs.slice(0, 2).map((run) => clock.format(run.start)).join(" · ") + (row.runs.length > 2 ? ` +${row.runs.length - 2}` : "");
          let text = row.meta ? `${when}  ·  ${row.meta}` : when;
          if (xe + 8 + text.length * 6.3 > dlx - 6) text = when;
          if (xe + 8 + text.length * 6.3 > lx1) text = "";
          if (text) lane += `<text x="${(xe + 8).toFixed(1)}" y="${mid}" class="ltxt">${escape(text)}</text>`;
        }
        lane += compact
          ? `<line x1="${xn.toFixed(1)}" y1="${laneY - 3}" x2="${xn.toFixed(1)}" y2="${laneY + laneH + 3}" class="now"/>`
          : `<line x1="${xn.toFixed(1)}" y1="0" x2="${xn.toFixed(1)}" y2="${RH}" class="now"/>`;
        const extra = compact && row.cost && (view.kind === "running" || view.kind === "planned") ? `<span class="meta">${escape(row.cost)}</span>` : "";
        const pill = view.label ? `<span class="pill ${view.kind}"><i></i>${escape(view.label)}</span>` : "";
        const aria = `${load.name}: ${view.label || fill(labels.no_need ?? "", { h: hours })}${row.sub ? `, ${row.sub}` : ""}`;
        const text = compact
          ? `<div class="top"><div class="txt"><span class="name">${escape(load.name)}</span><span class="sub">${escape(row.sub)}</span></div><div class="right">${pill}${extra}</div></div>`
          : `<div class="txt"><span class="name">${escape(load.name)}</span><span class="line2">${pill}<span class="sub">${escape(row.sub)}</span></span></div>`;
        return `<div class="row ${view.kind}" role="button" tabindex="0" data-load="${escape(load.id)}" aria-label="${escape(aria)}" style="height:${RH}px">
        <svg class="lane" width="${W}" height="${RH}" viewBox="0 0 ${W} ${RH}" aria-hidden="true">${bandsSvg}${lane}</svg>
        <div class="rail" style="width:${compact ? W - 24 : rail - 24}px">
          <div class="bubble" style="background:${withAlpha(color, 0.2)};color:${readable(color, dark)}"><ha-icon icon="${escape(icon)}"></ha-icon></div>${text}
        </div></div>`;
      })
      .join("");

    const foot =
      this.filter === "active" && idle.length
        ? `<button type="button" class="foot" data-filter="all">
          <span class="avatars">${idle
            .slice(0, 4)
            .map(
              (row) =>
                `<span class="av" style="background:${withAlpha(row.load.color, 0.2)};color:${readable(row.load.color, dark)}"><ha-icon icon="${escape(row.load.icon || "mdi:flash")}"></ha-icon></span>`,
            )
            .join("")}</span>
          <span class="ftxt">${escape(fill(labels.count_idle ?? "{n}", { n: idle.length }))}${compact ? "" : ` · ${escape(idle.map((row) => row.load.name).join(", "))}`}</span>
          <span class="more">${escape(labels.show_all ?? "")}<ha-icon icon="mdi:chevron-down"></ha-icon></span></button>`
        : "";

    this.shadowRoot!.innerHTML = `
      <style>${ppStyles}${CSS}</style>
      <ha-card class="${compact ? "compact" : ""}">
        <svg width="0" height="0" style="position:absolute">${hatchDefs(U)}</svg>
        <div class="head"><div class="counters">${counters}</div><span class="grow"></span>${legend}
          <div class="segs" role="group">${seg}</div></div>
        ${axis}
        <div class="rows">${rowHtml}</div>
        ${foot}
      </ha-card>`;

    // Redraw when a held status change becomes visible (R5).
    const due = this.debouncer.nextFlip();
    if (this.flipTimer) clearTimeout(this.flipTimer);
    if (due !== null) this.flipTimer = window.setTimeout(() => this.redraw(), Math.max(due - Date.now(), 1000));
  }

  private onClick(event: Event): void {
    const target = event.target as HTMLElement;
    const filter = target.closest<HTMLElement>("[data-filter]");
    if (filter) {
      this.filter = filter.dataset.filter === "all" ? "all" : "active";
      try {
        localStorage.setItem(FILTER_KEY, this.filter);
      } catch {
        // private mode
      }
      this.redraw();
      return;
    }
    const row = target.closest<HTMLElement>("[data-load]");
    if (!row || !this.hassRef || !this.config) return;
    const load = this.config.loads.find((l) => l.id === row.dataset.load);
    if (load) openApplianceDialog(this, this.hassRef, load, this.config);
  }
}

/** The hatch patterns, their ids unique per card (several cards can share a page). */
function hatchDefs(uid: string): string {
  return `<defs>
    <pattern id="${uid}-man" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="2" height="6" style="fill:rgba(var(--rgb-primary-text-color,225,225,225),0.10)"/></pattern>
    <pattern id="${uid}-shed" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="2" height="6" style="fill:rgba(255,166,0,0.30)"/></pattern>
  </defs>`;
}

const CSS = `
  ha-card { overflow: hidden; padding-bottom: 4px; }
  .head { display: flex; align-items: center; gap: 18px; height: 52px; padding: 0 var(--pp-pad); }
  .compact .head { gap: 12px; height: 46px; padding: 0 12px; }
  .counters { display: flex; gap: 18px; flex-wrap: nowrap; overflow: hidden; }
  .compact .counters { gap: 12px; }
  .cnt { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; white-space: nowrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot.running { background: var(--success-color, #43a047); }
  .dot.planned, .dot.waiting { background: var(--primary-color); }
  .dot.holding { background: var(--success-color, #43a047); opacity: .55; }
  .dot.paused { background: var(--warning-color, #ffa600); }
  .dot.manual, .dot.idle { background: var(--disabled-text-color, #6f6f6f); }
  .grow { flex: 1 1 auto; }
  .legend { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; }
  .sw.cheap { width: 12px; height: 10px; border-radius: 3px; background: rgba(67,160,71,0.35); display: inline-block; }
  .segs { display: flex; gap: 2px; padding: 2px; border-radius: 10px; background: var(--pp-fill); flex: none; }
  .seg { height: 28px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent; cursor: pointer;
         font: 500 12px var(--ha-font-family-body, Roboto, sans-serif); color: var(--secondary-text-color); }
  .compact .seg { height: 26px; padding: 0 10px; }
  .seg[aria-pressed="true"] { background: rgba(var(--rgb-primary-color, 0,154,199), 0.22); color: var(--primary-color); }
  .axis { display: block; }
  .tick { font-size: 11px; fill: var(--secondary-text-color); }
  .compact .tick { font-size: 10px; }
  .nowpill { fill: var(--primary-text-color); }
  .nowtxt { font-size: 10px; font-weight: 500; fill: var(--card-background-color, #1c1c1c); }
  .rows { display: flex; flex-direction: column; }
  .row { position: relative; border-top: 1px solid var(--divider-color); cursor: pointer; outline: none; }
  .row:hover, .row:focus-visible { background: rgba(var(--rgb-primary-text-color, 225,225,225), 0.045); }
  .row:focus-visible { box-shadow: inset 0 0 0 2px var(--primary-color); }
  .lane { position: absolute; inset: 0; display: block; pointer-events: none; }
  .band { fill: rgba(67,160,71,0.075); }
  .track { fill: rgba(var(--rgb-primary-text-color, 225,225,225), 0.045); }
  .elapsed { fill: rgba(0,0,0,0.28); }
  .shed { stroke: var(--warning-color, #ffa600); stroke-width: 1; }
  .deadline { stroke: var(--amber-color, #ffc107); stroke-width: 2; stroke-linecap: round; }
  .now { stroke: var(--primary-text-color); stroke-width: 1.5; stroke-linecap: round; }
  .ltxt { font-size: 11px; fill: var(--primary-text-color); }
  .ltxt.dim { fill: var(--secondary-text-color); }
  .ltxt.faint { fill: var(--disabled-text-color, #6f6f6f); }
  .ltxt.warn { fill: #ffc15c; }
  .ltxt.amber { fill: #ffd54f; }
  .rail { position: absolute; left: var(--pp-pad); top: 0; height: 100%; display: flex; align-items: center; gap: 12px; min-width: 0; }
  .compact .rail { left: 12px; align-items: flex-start; padding-top: 8px; box-sizing: border-box; }
  .row.idle .rail { opacity: 0.72; }
  .bubble { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none; --mdc-icon-size: 20px; }
  .txt { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1 1 auto; }
  .compact .txt { gap: 0; }
  .name { font-size: 14px; font-weight: 500; line-height: 20px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .line2 { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .sub { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .top { display: flex; align-items: flex-start; gap: 12px; flex: 1 1 auto; min-width: 0; }
  .right { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; flex: none; }
  .meta { font-size: 11px; color: var(--secondary-text-color); }
  .pill { display: inline-flex; align-items: center; gap: 5px; height: 18px; padding: 0 8px 0 7px; border-radius: 9px;
          font-size: 11px; font-weight: 500; letter-spacing: .2px; white-space: nowrap; flex: none; }
  .pill i { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
  .pill.running { background: rgba(67,160,71,.16); color: #7ccf80; } .pill.running i { background: var(--success-color, #43a047); }
  .pill.planned, .pill.waiting { background: rgba(var(--rgb-primary-color, 0,154,199), .16); color: #5cc8ea; }
  .pill.planned i, .pill.waiting i { background: var(--primary-color); }
  .pill.paused { background: rgba(255,166,0,.16); color: #ffc15c; } .pill.paused i { background: var(--warning-color, #ffa600); }
  .pill.holding { background: rgba(67,160,71,.10); color: #9ad69d; } .pill.holding i { background: var(--success-color, #43a047); opacity: .55; }
  .pill.manual, .pill.unavailable { background: rgba(158,158,158,.16); color: #c4c4c4; } .pill.manual i, .pill.unavailable i { background: #9e9e9e; }
  .foot { display: flex; align-items: center; gap: 12px; width: 100%; height: 50px; padding: 0 var(--pp-pad); border: 0;
          border-top: 1px solid var(--divider-color); background: transparent; cursor: pointer; color: inherit; font: inherit; text-align: left; }
  .compact .foot { height: 48px; padding: 0 12px; }
  .avatars { display: flex; }
  .av { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; --mdc-icon-size: 14px;
        box-shadow: 0 0 0 2px var(--card-background-color, #1c1c1c); margin-left: -6px; }
  .av:first-child { margin-left: 0; }
  .ftxt { flex: 1 1 auto; font-size: 13px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .more { display: inline-flex; align-items: center; font-size: 13px; font-weight: 500; color: var(--primary-color); --mdc-icon-size: 18px; white-space: nowrap; }
`;
