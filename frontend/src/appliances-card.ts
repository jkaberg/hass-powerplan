// custom:powerplan-appliances-card - iteration 5.
//
// One row per appliance with a 24 h lane on the same time axis (and rail width) as the Plan card.
//   solid block  = a run PowerPlan moved (slots[].planned_kwh) - obvious, no legend
//   empty track  = holds temperature / nothing moved - the state word says it, no legend
//   hatch        = lowered in expensive hours (slots[].paused) - the ONE legend entry, in the footer
//   green column = cheap hours - keyed once, in Strømpris above
//   amber tick   = deadline - not repeated as "frist 06:00" text
// Iteration 5 ("one metric once"): no Aktive/Alle filter (the footer's "N uten behov · Vis alle" does the same),
// no start times written next to the lanes on desktop (the axis + the dialog's "Valgt tid"), no "frist" in
// the state line, hatch fragments less than 30 min apart are merged. Phone keeps "Neste 22:00" because
// its lanes are too narrow to read a time from.

import {
  Hass, esc, numFmt01, timeFmt, localHour, startOfHour, rgbaHex, readable, readPlan, runsFor, loweredFor,
  newUid, fmtTemplate, pick, keepFocus, toNum, Win,
} from "./r3-util";
import { StatusDebouncer, StatusView, rawStatus, STATUS_LABELS, Kind } from "./status";
import { openApplianceDialog } from "./appliance-dialog";
import { TOKENS, SHARED, hatchDef, skeletonRows } from "./tokens";

export interface LoadCfg {
  id: string;
  name: string;
  color: string;
  icon?: string;
  area?: string;
  status: string;         // sensor.<load>_planstatus
  control?: string;       // select.<load>_styring
  deadline?: string;      // time.<load>_ferdig_til_kl
  cost?: string;          // sensor.<load>_kostnad_denne_maneden
  savings?: string;       // sensor.<load>_besparelse_denne_maneden
  energy?: string;        // sensor.<load>_energi_totalt
  legionella?: string;    // sensor.<load>_neste_legionellakjoring
  path?: string;          // subview path
  kind?: string;          // water_heater | floor_heating | heat_pump | ev | battery
}

export interface AppliancesCfg {
  type: string;
  entry_id?: string;
  entities: { plan: string; price_forecast?: string };
  loads: LoadCfg[];
  hours?: number;              // default 24
  rail_width?: number;         // must equal the Plan card's rail (strategy passes 256)
  default_filter?: "active" | "all";
  currency?: string;
  labels?: Record<string, string>;
}

const ORDER: Kind[] = ["running", "waiting", "paused", "planned", "holding", "manual", "unavailable", "idle"];
const FILTER_KEY = "powerplan-appliances-filter";

const CARD_LABELS: Record<string, Record<string, string>> = {
  nb: {
    show_all: "Vis alle", show_less: "Vis færre", lg_low: "Senket i dyre timer", lg_low_short: "Senket",
    now: "Nå", n_idle: "{n} uten behov", open: "Åpne detaljer", idle_state: "Ingen behov neste {h} t",
  },
  en: {
    show_all: "Show all", show_less: "Show fewer", lg_low: "Lowered in expensive hours", lg_low_short: "Lowered",
    now: "Now", n_idle: "{n} idle", open: "Open details", idle_state: "Nothing needed in the next {h} h",
  },
};

const DEFAULT_ICON: Record<string, string> = {
  water_heater: "mdi:water-boiler", floor_heating: "mdi:heating-coil", heat_pump: "mdi:heat-pump", ev: "mdi:ev-station", battery: "mdi:home-battery",
};

interface Row {
  load: LoadCfg;
  view: StatusView;
  runs: Win[];
  lowered: Win[];
  line: string;          // "Går · 23,8 → 24 °C"
  lineWide: string;      // "Venter · fra 22:00 · 54,8 °C · mål 45": the wide row has no "Neste" at its end
  deadline: Date | null;
  nextStart: number;
}

export class PowerplanAppliancesCard extends HTMLElement {
  private config?: AppliancesCfg;
  private hassRef?: Hass;
  private key: unknown[] = [];
  private width = 0;
  private ro?: ResizeObserver;
  private deb = new StatusDebouncer();
  private uid = newUid("ppa");
  private filter: "active" | "all" = "active";
  private flipTimer?: number;

  setConfig(config: AppliancesCfg): void {
    if (!config?.entities?.plan) throw new Error("powerplan-appliances-card needs entities.plan");
    if (!Array.isArray(config.loads) || !config.loads.length) throw new Error("powerplan-appliances-card needs loads");
    this.config = config;
    let stored: string | null = null;
    try { stored = localStorage.getItem(FILTER_KEY); } catch { /* private mode */ }
    this.filter = stored === "all" || stored === "active" ? stored : config.default_filter ?? "active";
    this.key = [];
  }

  set hass(hass: Hass) {
    this.hassRef = hass;
    const c = this.config;
    if (!c) return;
    const ids = [c.entities.plan, c.entities.price_forecast, ...c.loads.flatMap((l) => [l.status, l.control, l.deadline])];
    const k = [...ids.map((id) => (id ? hass.states[id] : undefined)), hass.language, hass.themes?.darkMode,
      Math.floor(Date.now() / 60e3), this.width, this.filter];
    if (k.length === this.key.length && k.every((v, i) => v === this.key[i])) return;
    this.key = k;
    this.render();
  }

  connectedCallback(): void {
    this.style.display ||= "block";   // measurable before the first render (the observer needs a box)
    this.ro ??= new ResizeObserver((e) => {
      const w = Math.round(e[0].contentRect.width);
      if (w && w !== this.width) { this.width = w; this.key = []; if (this.hassRef) this.hass = this.hassRef; }
    });
    this.ro.observe(this);
  }

  disconnectedCallback(): void {
    this.ro?.disconnect();
    if (this.flipTimer) clearTimeout(this.flipTimer);
  }

  getCardSize(): number { return 2 + (this.config?.loads.length ?? 4); }
  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }
  static getStubConfig() { return { entities: { plan: "" }, loads: [] }; }

  // ------------------------------------------------------------------ data
  private labels(): Record<string, string> {
    const h = this.hassRef!;
    return { ...pick(STATUS_LABELS, h), ...pick(CARD_LABELS, h), ...(this.config!.labels ?? {}) };
  }

  private rows(now: Date, a1: Date): Row[] {
    const hass = this.hassRef!;
    const c = this.config!;
    const L = this.labels();
    const tf = timeFmt(hass);
    const plan = readPlan(hass.states[c.entities.plan]);
    const nf1 = numFmt01(hass);
    const cur = plan.slots.find((s) => s.start <= now && s.end > now);
    return c.loads.map((load) => {
      const st = hass.states[load.status];
      const ctl = load.control ? hass.states[load.control] : undefined;
      const runs = runsFor(load.id, plan.slots).filter((r) => r.end > now && r.start < a1);
      const lowered = mergeGaps(loweredFor(load.id, plan.slots).filter((r) => r.end > now && r.start < a1), 30 * 60e3);
      const view = this.deb.view(load.id, rawStatus(st, ctl, {
        runNow: runs.some((r) => r.start <= now), futureRun: runs.length > 0, holdNow: (cur?.hold[load.id] ?? 0) > 0.0005,
      }, L));
      const a = st?.attributes ?? {};
      const parts: string[] = [];
      const t = toNum(a.current), tgt = toNum(a.target);
      if (t !== null && tgt !== null) {
        parts.push(a.comfort_state === "below_target" && tgt > t
          ? `${nf1.format(t)} → ${nf1.format(tgt)} °C`
          : `${nf1.format(t)} °C · ${fmtTemplate(L.target, { v: nf1.format(tgt) })}`);
      } else if (view.kind === "running" && toNum(a.granted_power)) {
        parts.push(`${nf1.format(toNum(a.granted_power)! / 1000)} kW`);
      }
      let deadline: Date | null = null;
      const dl = (load.deadline && hass.states[load.deadline]?.state) || a.deadline_time;
      if (dl && /^\d{1,2}:\d{2}/.test(dl) && ["running", "planned", "waiting"].includes(view.kind)) {
        deadline = nextClock(dl, now, hass);          // drawn as the amber tick; the dialog has the control
      }
      if (view.reason) parts.splice(0, parts.length, view.reason);
      if (view.kind === "idle") parts.splice(0, parts.length, fmtTemplate(L.idle_state, { h: c.hours ?? 24 }));
      // A load waiting for its run says when it starts (D-0630), whatever its temperature.
      const upcoming = runs.find((r) => r.start > now);
      const from = upcoming && (view.kind === "waiting" || view.kind === "planned")
        ? fmtTemplate(L.from, { time: tf.format(upcoming.start) }) : "";
      return {
        load, view, runs, lowered, deadline, line: [view.word, ...parts].filter(Boolean).join(" · "),
        lineWide: [view.word, from, ...parts].filter(Boolean).join(" · "),
        nextStart: runs.find((r) => r.start > now)?.start.getTime() ?? Number.MAX_SAFE_INTEGER,
      };
    }).sort((x, y) => ORDER.indexOf(x.view.kind) - ORDER.indexOf(y.view.kind) || x.nextStart - y.nextStart ||
      x.load.name.localeCompare(y.load.name));
  }

  /** Cheap hours: lowest quarter of the price range inside the window. */
  private cheapBands(a0: Date, a1: Date): [Date, Date][] {
    const id = this.config!.entities.price_forecast;
    const slots: any[] = (id && this.hassRef!.states[id]?.attributes?.slots) || [];
    const win = slots.map((s) => ({ s: new Date(s.start), e: new Date(s.end), p: Number(s.total) }))
      .filter((x) => x.e > a0 && x.s < a1 && Number.isFinite(x.p));
    if (!win.length) return [];
    const lo = Math.min(...win.map((x) => x.p)), hi = Math.max(...win.map((x) => x.p));
    if (hi - lo < 1e-6) return [];
    const thr = lo + (hi - lo) * 0.25;
    const out: [Date, Date][] = [];
    for (const x of win) {
      if (x.p > thr) continue;
      const last = out[out.length - 1];
      if (last && Math.abs(last[1].getTime() - x.s.getTime()) < 1000) last[1] = x.e;
      else out.push([x.s, x.e]);
    }
    return out;
  }

  // ------------------------------------------------------------------ render
  private render(): void {
    const hass = this.hassRef, c = this.config;
    if (!hass || !c) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot!.addEventListener("click", (e) => this.onClick(e));
    }
    const W = this.width || Math.round(this.getBoundingClientRect().width);
    const L = this.labels();
    const planEnt = hass.states[c.entities.plan];
    if (!W || !planEnt || planEnt.state === "unavailable" || !Array.isArray(planEnt.attributes?.slots)) {
      // Skeleton keeps the final size while data or layout is not ready yet.
      this.shadowRoot!.innerHTML = `<style>${TOKENS}${SHARED}${CSS}</style><ha-card aria-busy="true">
        <div style="height:40px"></div>${skeletonRows(Math.min(c.loads.length, 6), 56, c.rail_width ?? 256)}</ha-card>`;
      return;
    }
    const compact = W < 600;
    const hours = c.hours ?? 24;
    const now = new Date();
    const a0 = startOfHour(now);
    const a1 = new Date(a0.getTime() + hours * 3600e3);
    const rows = this.rows(now, a1);
    const idle = rows.filter((r) => r.view.kind === "idle");
    const shown = this.filter === "all" ? rows : rows.filter((r) => r.view.kind !== "idle");
    const rail = compact ? 0 : (c.rail_width ?? 256);
    const lx0 = compact ? 60 : rail;
    const lx1 = W - (compact ? 12 : 16);
    const sx = (d: Date | number) => lx0 + (+d - a0.getTime()) / (a1.getTime() - a0.getTime()) * (lx1 - lx0);
    const bands = this.cheapBands(a0, a1);
    const tf = timeFmt(hass);
    const dark = !!hass.themes?.darkMode;
    const U = this.uid;
    const xn = sx(now);

    // ---- axis
    const ticks: string[] = [];
    const every = compact ? 6 : 3;
    for (let t = new Date(a0); t <= a1; t = new Date(t.getTime() + 3600e3)) {
      const h = Math.round(localHour(t, hass));
      if (h % every) continue;
      const x = sx(t);
      if ((!compact && Math.abs(x - xn) < 40) || x < lx0 + 14 || x > lx1 - 14) continue;
      ticks.push(`<text x="${x.toFixed(1)}" y="${compact ? 22 : 26}" class="t-s" text-anchor="middle">${esc(tf.format(t))}</text>`);
    }
    // The card opens straight on the time axis (no header row: nothing left in it to say).
    const axisH = compact ? 32 : 40;
    // cheap hours: a 3 px line at the foot of the axis, over the tinted columns below it
    const cheapLine = bands.map(([s, e]) => {
      const x0 = Math.max(sx(s), lx0), x1 = Math.min(sx(e), lx1);
      return x1 > x0 ? `<rect x="${x0.toFixed(1)}" y="${axisH - 3}" width="${(x1 - x0).toFixed(1)}" height="3" rx="1.5" class="cheapline"/>` : "";
    }).join("");
    const axis = `<svg class="axis" width="${W}" height="${axisH}" viewBox="0 0 ${W} ${axisH}" aria-hidden="true">${cheapLine}${ticks.join("")}
      ${compact ? "" : `<rect x="${(xn - 16).toFixed(1)}" y="12" width="32" height="20" rx="10" class="nowpill"/>
      <text x="${xn.toFixed(1)}" y="26" class="t-s nowtxt" text-anchor="middle">${esc(L.now)}</text>`}</svg>`;

    // ---- rows
    const RH = compact ? 72 : 56;
    const laneH = compact ? 10 : 16;
    const laneY = compact ? 52 : (RH - laneH) / 2;
    const rowHtml = shown.map((r) => {
      const { load, view } = r;
      const col = load.color || "#9e9e9e";
      const icon = load.icon || DEFAULT_ICON[load.kind ?? ""] || "mdi:flash";
      const rr = laneH / 2;
      const bandsSvg = bands.map(([s, e]) => {
        const x0 = Math.max(sx(s), lx0), x1 = Math.min(sx(e), lx1);
        return x1 > x0 ? `<rect x="${x0.toFixed(1)}" y="0" width="${(x1 - x0).toFixed(1)}" height="${RH}" class="band"/>` : "";
      }).join("");
      let lane = `<rect x="${lx0}" y="${laneY}" width="${lx1 - lx0}" height="${laneH}" rx="${rr}" class="track"/>`;
      const block = (s: Date, e: Date, style: string, cls = "") => {
        const x0 = Math.max(sx(s), lx0), x1 = Math.min(sx(e), lx1);
        return x1 > x0 ? `<rect x="${x0.toFixed(1)}" y="${laneY}" width="${Math.max(x1 - x0, laneH).toFixed(1)}" height="${laneH}" rx="${rr}" class="${cls}" style="${style}"/>` : "";
      };
      if (view.kind === "manual") lane += block(a0, a1, `fill:url(#${U}-h)`);
      for (const w of r.lowered) lane += block(w.start, w.end, `fill:url(#${U}-h)`);
      for (const run of r.runs) lane += block(run.start, run.end, `fill:${col}`);
      if (view.kind === "paused") {
        const span = (a1.getTime() - a0.getTime()) * 0.012;
        lane += block(new Date(now.getTime() - span), new Date(now.getTime() + span), `fill:url(#${U}-h)`, "shed");
      }
      if (r.deadline && r.deadline > a0 && r.deadline < a1) {
        const dx = sx(r.deadline);
        lane += `<line x1="${dx.toFixed(1)}" y1="${laneY - 5}" x2="${dx.toFixed(1)}" y2="${laneY + laneH + 5}" class="deadline"/>`;
      }
      lane += compact
        ? `<line x1="${xn.toFixed(1)}" y1="${laneY - 3}" x2="${xn.toFixed(1)}" y2="${laneY + laneH + 3}" class="now"/>`
        : `<line x1="${xn.toFixed(1)}" y1="0" x2="${xn.toFixed(1)}" y2="${RH}" class="now"/>`;
      const next = r.runs.find((x) => x.start > now);
      const right = compact && next ? `<span class="next">${esc(fmtTemplate(L.next, { time: tf.format(next.start) }))}</span>` : "";
      const line = compact ? r.line : r.lineWide;
      const aria = `${load.name}: ${line}. ${L.open}`;
      return `<button type="button" class="row ${view.kind}" data-load="${esc(load.id)}" data-focus-key="row-${esc(load.id)}" aria-label="${esc(aria)}" style="height:${RH}px">
        <svg class="lane" width="${W}" height="${RH}" viewBox="0 0 ${W} ${RH}" aria-hidden="true">${bandsSvg}${lane}</svg>
        <span class="rail" style="width:${compact ? W - 24 : rail - 24}px">
          <span class="bubble" style="background:${rgbaHex(col, 0.2)};color:${readable(col, dark)}"><ha-icon icon="${esc(icon)}"></ha-icon></span>
          <span class="txt"><span class="name">${esc(load.name)}</span><span class="state">${esc(line)}</span></span>${right}
        </span></button>`;
    }).join("");

    // Footer: the one legend entry (only when a hatch is on screen) + the idle rows toggle.
    const hatched = shown.some((r) => r.lowered.length || r.view.kind === "manual" || r.view.kind === "paused");
    const key = hatched ? `<span class="legend"><i class="sw low"></i>${esc(compact ? L.lg_low_short : L.lg_low)}</span>` : "";
    const idlePart = idle.length
      ? (this.filter === "active"
        ? `<span class="avatars" aria-hidden="true">${idle.slice(0, 4).map((r) => `<span class="av" style="background:${rgbaHex(r.load.color, 0.2)};color:${readable(r.load.color, dark)}"><ha-icon icon="${esc(r.load.icon || DEFAULT_ICON[r.load.kind ?? ""] || "mdi:flash")}"></ha-icon></span>`).join("")}</span>
           <span class="ftxt">${esc(fmtTemplate(L.n_idle, { n: idle.length }))}${compact ? "" : " · " + esc(idle.map((r) => r.load.name).join(", "))}</span>
           <button type="button" class="tbtn" data-filter="all" data-focus-key="foot-toggle">${esc(L.show_all)}<ha-icon icon="mdi:chevron-down"></ha-icon></button>`
        : `<span class="grow"></span><button type="button" class="tbtn" data-filter="active" data-focus-key="foot-toggle">${esc(L.show_less)}<ha-icon icon="mdi:chevron-up"></ha-icon></button>`)
      : "";
    const foot = key || idlePart ? `<div class="foot">${key}${key && idlePart ? `<span class="sep"></span>` : ""}${idlePart}</div>` : "";

    keepFocus(this.shadowRoot!, () => {
      this.shadowRoot!.innerHTML = `<style>${TOKENS}${SHARED}${CSS}</style>
        <ha-card class="${compact ? "compact" : ""}">
          <svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>${hatchDef(`${U}-h`)}</defs></svg>
          ${axis}<div class="rows">${rowHtml}</div>${foot}
        </ha-card>`;
    });

    const due = this.deb.nextFlip();
    if (this.flipTimer) clearTimeout(this.flipTimer);
    if (due) this.flipTimer = window.setTimeout(() => { this.key = []; if (this.hassRef) this.hass = this.hassRef; }, Math.max(due - Date.now(), 1000));
  }

  private onClick(e: Event): void {
    const t = e.target as HTMLElement;
    const f = t.closest("[data-filter]") as HTMLElement | null;
    if (f) {
      this.filter = f.dataset.filter === "all" ? "all" : "active";
      try { localStorage.setItem(FILTER_KEY, this.filter); } catch { /* ignore */ }
      this.key = [];
      this.render();
      return;
    }
    const row = t.closest("[data-load]") as HTMLElement | null;
    if (!row || !this.hassRef || !this.config) return;
    const load = this.config.loads.find((l) => l.id === row.dataset.load);
    if (load) openApplianceDialog(this, this.hassRef, load, this.config);
  }
}

/** Next occurrence of a HH:MM[:SS] wall-clock time after `now`, in the HA time zone. */
export function nextClock(hhmm: string, now: Date, hass: Hass): Date {
  const [h, m] = hhmm.split(":").map(Number);
  let diffH = h + m / 60 - localHour(now, hass);
  if (diffH <= 0) diffH += 24;
  const d = new Date(now.getTime() + diffH * 3600e3);
  d.setSeconds(0, 0);
  return d;
}

const CSS = `
  ha-card { overflow: hidden; padding-bottom: 4px; }
  .grow { flex: 1 1 auto; }
  .sep { width: 1px; height: 20px; background: var(--pp-divider); flex: none; }
  .axis { display: block; }
  .nowpill { fill: var(--pp-text); }
  .nowtxt { fill: var(--pp-card); font-weight: var(--pp-fw-m); }
  .rows { display: flex; flex-direction: column; }
  .row { position: relative; display: block; width: 100%; border: 0; border-top: 1px solid var(--pp-divider); margin: 0; padding: 0;
         background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
  .row:hover { background: var(--pp-fill); }
  .row:focus-visible { outline: none; box-shadow: inset 0 0 0 2px var(--pp-focus); }
  .lane { position: absolute; inset: 0; display: block; pointer-events: none; }
  .band { fill: var(--pp-cheap); }
  .cheapline { fill: var(--pp-cheap-line); }
  .track { fill: var(--pp-track); }
  .shed { stroke: var(--pp-warn); stroke-width: 1; }
  .deadline { stroke: var(--pp-amber); stroke-width: 2; stroke-linecap: round; }
  .now { stroke: var(--pp-text); stroke-width: 1.5; stroke-linecap: round; }
  .rail { position: absolute; left: var(--pp-pad); top: 0; height: 100%; display: flex; align-items: center; gap: 12px; min-width: 0; box-sizing: border-box; }
  .compact .rail { left: 12px; align-items: flex-start; padding-top: 8px; }
  .row.idle .rail, .row.unavailable .rail { opacity: 0.72; }
  .bubble { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none; --mdc-icon-size: 20px; }
  .txt { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 auto; }
  .name { font-size: var(--pp-fs-m); font-weight: var(--pp-fw-m); line-height: 20px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .state { font-size: var(--pp-fs-s); line-height: 16px; color: var(--pp-text2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .next { font-size: var(--pp-fs-s); color: var(--pp-text2); white-space: nowrap; flex: none; padding-top: 2px; }
  .foot { display: flex; align-items: center; gap: 12px; min-height: 60px; padding: 0 8px 0 var(--pp-pad); border-top: 1px solid var(--pp-divider); box-sizing: border-box; }
  .compact .foot { min-height: 56px; padding: 0 4px 0 12px; }
  .avatars { display: flex; flex: none; }
  .av { width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; --mdc-icon-size: 16px;
        box-shadow: 0 0 0 2px var(--pp-card); margin-left: -6px; }
  .av:first-child { margin-left: 0; }
  .ftxt { flex: 1 1 auto; font-size: var(--pp-fs-m); color: var(--pp-text2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
`;

/** Joins windows whose gap is at most `gapMs`, so 15-min pauses 20 min apart read as one lowered stretch. */
function mergeGaps(ws: Win[], gapMs: number): Win[] {
  const out: Win[] = [];
  for (const w of [...ws].sort((a, b) => a.start.getTime() - b.start.getTime())) {
    const last = out[out.length - 1];
    if (last && w.start.getTime() - last.end.getTime() <= gapMs) {
      out[out.length - 1] = { ...last, end: w.end > last.end ? w.end : last.end, kwh: last.kwh + w.kwh };
    } else out.push({ ...w });
  }
  return out;
}
