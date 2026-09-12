// custom:powerplan-month-bars - "Denne måneden" (iteration 4): cost and savings so far, and the daily
// cost on a fixed 1..N day axis.
//
// Replaces the statistics-graph in "Denne måneden": with 1,5 days of data that card zooms in and
// labels the axis 4:00, 8:00 … Here the axis is always the whole month, days without data are
// empty, and the first day with data is named.
//
//   type: custom:powerplan-month-bars
//   entity: sensor.home_kostnad_denne_maneden   # long-term statistics, state_class total
//   savings: sensor.home_beregnet_besparelse_denne_maneden   # optional
//
// Savings show " - " with a reason when there is no reference cost (see savings_guard.py), instead
// of an entity card that says "Ukjent" or a negative number that was never computed.

import { Hass, esc, fmtTemplate, lang, numFmt, pick, tz, toNum, savingsView } from "./r3-util";
import { TOKENS, SHARED } from "./tokens";

interface BarsCfg { type: string; entity: string; savings?: string; statistic_id?: string; height?: number; labels?: Record<string, string> }

const M_LABELS: Record<string, Record<string, string>> = {
  nb: { since: "Data fra {date}", none: "Ingen data ennå denne måneden", kr: "kr", day: "{date}: {v} kr", aria: "Kostnad per dag i {month}",
        cost: "Kostnad", savings: "Besparelse", no_ref: "mangler referanse", more: "mer enn uten styring" },
  en: { since: "Data from {date}", none: "No data yet this month", kr: "", day: "{date}: {v}", aria: "Cost per day in {month}",
        cost: "Cost", savings: "Savings", no_ref: "no reference yet", more: "more than without control" },
};

export class PowerplanMonthBars extends HTMLElement {
  private config?: BarsCfg;
  private hassRef?: Hass;
  private data: { day: number; v: number }[] = [];
  private fetchedAt = 0;
  private busy = false;
  private width = 0;
  private ro?: ResizeObserver;
  private key: unknown[] = [];

  setConfig(c: BarsCfg): void {
    if (!c?.entity) throw new Error("powerplan-month-bars needs entity");
    this.config = c;
    this.key = [];
  }

  set hass(h: Hass) {
    this.hassRef = h;
    if (!this.config) return;
    if (Date.now() - this.fetchedAt > 30 * 60e3) this.fetch();
    const k = [h.language, h.themes?.darkMode, this.fetchedAt, this.width, h.states[this.config.entity], this.config.savings && h.states[this.config.savings]];
    if (k.every((v, i) => v === this.key[i])) return;
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
  disconnectedCallback(): void { this.ro?.disconnect(); }
  getCardSize(): number { return 5; }
  getGridOptions() { return { columns: 12, rows: 5 }; }
  static getStubConfig() { return { entity: "" }; }

  /** Local midnight on the 1st of the current month, and the number of days, in the HA time zone. */
  private month(): { start: Date; days: number } {
    const zone = tz(this.hassRef!);
    const p = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", timeZone: zone }).formatToParts(new Date());
    const y = Number(p.find((x) => x.type === "year")!.value), m = Number(p.find((x) => x.type === "month")!.value);
    const guess = Date.UTC(y, m - 1, 1);
    return { start: new Date(guess - tzOffsetMs(new Date(guess), zone)), days: new Date(Date.UTC(y, m, 0)).getUTCDate() };
  }

  private async fetch(): Promise<void> {
    const h = this.hassRef, c = this.config;
    if (!h || !c || this.busy) return;
    this.busy = true;
    const id = c.statistic_id ?? c.entity;
    const { start } = this.month();
    try {
      const r = await h.callWS<Record<string, { start: number | string; change?: number }[]>>({
        type: "recorder/statistics_during_period", start_time: start.toISOString(), end_time: new Date().toISOString(),
        statistic_ids: [id], period: "day", types: ["change"],
      });
      const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", timeZone: tz(h) });
      this.data = (r?.[id] ?? []).filter((x) => x.change != null)
        .map((x) => ({ day: Number(dayFmt.format(new Date(typeof x.start === "number" ? x.start : Date.parse(x.start)))), v: Number(x.change) }))
        .filter((x) => Number.isFinite(x.v));
    } catch {
      this.data = [];
    } finally {
      this.busy = false;
      this.fetchedAt = Date.now();
      if (this.hassRef) this.hass = this.hassRef;
    }
  }

  private render(): void {
    const h = this.hassRef, c = this.config;
    if (!h || !c) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const W = this.width || Math.round(this.getBoundingClientRect().width);
    if (!W) return;
    const L = { ...pick(M_LABELS, h), ...(c.labels ?? {}) };
    const head = 72;
    const H = (c.height ?? 280) - head;
    const { start, days } = this.month();
    const today = Number(new Intl.DateTimeFormat("en-GB", { day: "numeric", timeZone: tz(h) }).format(new Date()));
    const nf = numFmt(h, 2), nf0 = numFmt(h, 0);
    const monthName = new Intl.DateTimeFormat(lang(h), { month: "long", timeZone: tz(h) }).format(new Date());
    const x0 = 44, x1 = W - 16, top = 30, bottom = H - 28;
    const slot = (x1 - x0) / days;
    const maxV = Math.max(1, ...this.data.map((d) => d.v));
    const nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];
    const stepV = nice.find((s) => maxV / s <= 4) ?? 1000;
    const ymax = Math.ceil(maxV / stepV) * stepV;
    const sy = (v: number) => bottom - (v / ymax) * (bottom - top);
    const g: string[] = [];
    for (let v = 0; v <= ymax + 1e-9; v += stepV) {
      g.push(`<line x1="${x0}" x2="${x1}" y1="${sy(v).toFixed(1)}" y2="${sy(v).toFixed(1)}" class="${v ? "gl" : "gl0"}"/>`);
      g.push(`<text x="${x0 - 8}" y="${(sy(v) + 4).toFixed(1)}" class="t-s" text-anchor="end">${nf0.format(v)}</text>`);
    }
    if (L.kr) g.push(`<text x="${x0 - 8}" y="${top - 12}" class="t-s" text-anchor="end">${esc(L.kr)}</text>`);
    const dfmt = new Intl.DateTimeFormat(lang(h), { day: "numeric", month: "short", timeZone: tz(h) });
    for (const d of this.data) {
      const x = x0 + (d.day - 1) * slot + slot * 0.15;
      const date = dfmt.format(new Date(start.getTime() + (d.day - 1) * 86400e3 + 12 * 3600e3));
      g.push(`<rect x="${x.toFixed(1)}" y="${sy(Math.max(d.v, 0)).toFixed(1)}" width="${(slot * 0.7).toFixed(1)}" height="${Math.max(sy(0) - sy(Math.max(d.v, 0)), 1).toFixed(1)}" rx="2" class="${d.day === today ? "bar today" : "bar"}"><title>${esc(fmtTemplate(L.day, { date, v: nf.format(d.v) }))}</title></rect>`);
    }
    for (const d of [1, 5, 10, 15, 20, 25, days]) {
      g.push(`<text x="${(x0 + (d - 0.5) * slot).toFixed(1)}" y="${bottom + 18}" class="t-s" text-anchor="middle">${d}</text>`);
    }
    const first = this.data.length ? Math.min(...this.data.map((d) => d.day)) : null;
    const note = first == null ? L.none : first > 1 ? fmtTemplate(L.since, { date: dfmt.format(new Date(start.getTime() + (first - 1) * 86400e3 + 12 * 3600e3)) }) : "";
    const ce = h.states[c.entity], se = c.savings ? h.states[c.savings] : undefined;
    const unit = (e: any) => (e?.attributes?.unit_of_measurement === "NOK" ? "kr" : e?.attributes?.unit_of_measurement ?? "");
    const sv = savingsView(se, ce);
    const stat = (label: string, value: string, u: string, sub = "") =>
      `<div class="st"><span class="sl">${esc(label)}</span><span class="sv">${esc(value)} <small>${esc(u)}</small></span>${sub ? `<span class="ss">${esc(sub)}</span>` : ""}</div>`;
    const stats = stat(L.cost, toNum(ce?.state) !== null ? nf.format(toNum(ce!.state)!) : "–", unit(ce))
      + (se ? stat(L.savings, sv.missing ? "—" : nf.format(sv.value!), sv.missing ? "" : unit(se), sv.missing ? L.no_ref : sv.value! < 0 ? L.more : "") : "");
    this.shadowRoot!.innerHTML = `<style>${TOKENS}${SHARED}${CSS}</style><ha-card>
      <div class="head">${stats}<span class="grow"></span>${note ? `<span class="note">${esc(note)}</span>` : ""}</div>
      <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(fmtTemplate(L.aria, { month: monthName }))}">${g.join("")}</svg></ha-card>`;
  }
}

function tzOffsetMs(d: Date, zone: string): number {
  const p = new Intl.DateTimeFormat("en-US", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(d);
  const g = (t: string) => Number(p.find((x) => x.type === t)!.value);
  return Date.UTC(g("year"), g("month") - 1, g("day"), g("hour"), g("minute"), g("second")) - d.getTime();
}

const CSS = `
  ha-card { position: relative; overflow: hidden; height: 100%; }
  svg { display: block; }
  .head { display: flex; align-items: flex-start; gap: 24px; height: 72px; padding: 14px var(--pp-pad) 0; box-sizing: border-box; }
  .st { display: flex; flex-direction: column; gap: 2px; }
  .sl { font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .sv { font-size: var(--pp-fs-xl); } .sv small { font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .ss { font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .grow { flex: 1 1 auto; }
  .note { font-size: var(--pp-fs-s); color: var(--pp-text2); padding-top: 2px; }
  .gl0 { stroke: var(--pp-divider); } .gl { stroke: var(--pp-divider); stroke-dasharray: 2 4; }
  .bar { fill: var(--pp-primary); opacity: .75; } .bar.today { opacity: 1; }
`;
