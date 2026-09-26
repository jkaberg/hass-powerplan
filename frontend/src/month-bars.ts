// custom:powerplan-month-bars - "Denne måneden" (iteration 5): the cost so far, what it is made of, what
// PowerPlan did for it (D12 §5.19), and the daily cost on a fixed 1..N day axis.
//
// Replaces the statistics-graph in "Denne måneden": with 1,5 days of data that card zooms in and
// labels the axis 4:00, 8:00 … Here the axis is always the whole month and days without data are
// simply empty. Iteration 5: no "Data fra …" / "Ingen data ennå" note (the empty days say it).
//
//   type: custom:powerplan-month-bars
//   entity: sensor.home_kostnad_denne_maneden   # long-term statistics, state_class total
//   savings: sensor.home_beregnet_besparelse_denne_maneden   # optional
//   deviations: sensor.home_avvik_denne_maneden                # optional
//   load_names: {<load id>: <name>}                                      # names for the deviations
//
// The results block (`resultLines`) says the savings by source, the step without PowerPlan, the price
// paid against the reference and the month's deviations, one sentence each; the savings are left out
// when there is no reference (see savingsView), and a line with nothing to say is absent.

import { ChartTip, MARK_CSS, markSwatch, ring, type Slice } from "./marks";
import { Hass, RESULT_LABELS, costParts, esc, fmtTemplate, lang, labelLang, numFmt, outlierCap, pick, resultLines, savingsView, tz, toNum } from "./r3-util";
import { TOKENS, SHARED } from "./tokens";

interface BarsCfg {
  type: string; entity: string; savings?: string; statistic_id?: string; height?: number; labels?: Record<string, string>;
  /** D12 §5.19: the month's deviations, and the appliances' names they are keyed by. */
  deviations?: string; load_names?: Record<string, string>;
}

const M_LABELS: Record<string, Record<string, string>> = {
  nb: { kr: "kr", day: "{date}: {v} kr", aria: "Kostnad per dag i {month}", cost: "Kostnad",
        export: "Eksport", so_far: "kr hittil",
        part_capacity: "Effektledd", part_grid: "Energiledd", part_supplier: "Strøm", part_state: "Avgifter", part_energy: "Strøm" },
  en: { kr: "", day: "{date}: {v}", aria: "Cost per day in {month}", cost: "Cost",
        export: "Export", so_far: "so far",
        part_capacity: "Capacity fee", part_grid: "Grid energy", part_supplier: "Energy", part_state: "Taxes", part_energy: "Energy" },
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
  private tip?: ChartTip;

  setConfig(c: BarsCfg): void {
    if (!c?.entity) throw new Error("powerplan-month-bars needs entity");
    this.config = c;
    this.key = [];
  }

  set hass(h: Hass) {
    this.hassRef = h;
    if (!this.config) return;
    if (Date.now() - this.fetchedAt > 30 * 60e3) this.fetch();
    const k = [h.language, h.themes?.darkMode, this.fetchedAt, this.width, h.states[this.config.entity], this.config.savings && h.states[this.config.savings],
      this.config.deviations && h.states[this.config.deviations]];
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
  getGridOptions() { return { columns: 12, rows: "auto" }; }
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
    const H = (c.height ?? 280) - 72;
    const { start, days } = this.month();
    const today = Number(new Intl.DateTimeFormat("en-GB", { day: "numeric", timeZone: tz(h) }).format(new Date()));
    const nf = numFmt(h, 2), nf0 = numFmt(h, 0);
    const monthName = new Intl.DateTimeFormat(lang(h), { month: "long", timeZone: tz(h) }).format(new Date());
    const x0 = 44, x1 = W - 16, top = 30, bottom = H - 28;
    const slot = (x1 - x0) / days;
    // One day that holds a lump (the month's capacity fee, booked when the total starts or resets) would
    // flatten every other day: the axis follows the ordinary days and that bar is clipped, its value on top.
    const clip = outlierCap(this.data.map((d) => d.v));
    const maxV = Math.max(1, clip ?? Math.max(0, ...this.data.map((d) => d.v)));
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
      const v = Math.min(Math.max(d.v, 0), ymax);
      g.push(`<rect x="${x.toFixed(1)}" y="${sy(v).toFixed(1)}" width="${(slot * 0.7).toFixed(1)}" height="${Math.max(sy(0) - sy(v), 1).toFixed(1)}" rx="2" class="${d.day === today ? "bar today" : "bar"}"><title>${esc(fmtTemplate(L.day, { date, v: nf.format(d.v) }))}</title></rect>`);
      if (d.v > ymax) {
        const cx = x + slot * 0.35, y = sy(ymax);
        g.push(`<path d="M${(x - 1).toFixed(1)} ${(y + 7).toFixed(1)} l${(slot * 0.7 + 2).toFixed(1)} -4 v3 l-${(slot * 0.7 + 2).toFixed(1)} 4 z" class="cut"/>`
          + `<text x="${cx.toFixed(1)}" y="${(y - 6).toFixed(1)}" class="t-s strong" text-anchor="middle">${nf0.format(d.v)}</text>`);
      }
    }
    for (const d of [1, 5, 10, 15, 20, 25, days]) {
      g.push(`<text x="${(x0 + (d - 0.5) * slot).toFixed(1)}" y="${bottom + 18}" class="t-s" text-anchor="middle">${d}</text>`);
    }
    const ce = h.states[c.entity], se = c.savings ? h.states[c.savings] : undefined;
    const unit = (e: any) => (e?.attributes?.unit_of_measurement === "NOK" ? "kr" : e?.attributes?.unit_of_measurement ?? "");
    // V2 (D12 §5.20): the month's cost as a ring of who gets it, the total in its centre.
    const total = toNum(ce?.state);
    const parts = costParts(ce?.attributes);
    const whole = parts.reduce((a, p) => a + p.value, 0);
    const PART_COLOR: Record<string, string> = {
      capacity: "var(--energy-grid-consumption-color, #488fc2)", grid: "rgba(72, 143, 194, .5)",
      supplier: "rgba(var(--pp-rgb-text), .55)", state: "rgba(var(--pp-rgb-text), .30)", energy: "rgba(var(--pp-rgb-text), .55)",
    };
    const slices: Slice[] = parts.map((p) => ({
      value: p.value, color: PART_COLOR[p.key]!,
      lines: [L[`part_${p.key}`] ?? p.key, `${nf.format(p.value)} ${unit(ce)} · ${nf0.format((100 * p.value) / whole)} %`],
    }));
    const exported = Math.abs(parseFloat(String(ce?.attributes?.export_credit ?? "")) || 0);
    const size = W < 330 ? 112 : 128;
    const center: [string, string] = [total !== null ? nf.format(total) : "–", L.so_far ?? ""];
    const rows = parts.map((p, i) => `<div class="row" data-tip="${esc(slices[i]!.lines.join("\n"))}" tabindex="0"><i style="background:${PART_COLOR[p.key]}"></i>`
      + `<span class="n">${esc(L[`part_${p.key}`] ?? p.key)}</span><span class="v">${esc(nf.format(p.value))}</span><span class="p">${esc(nf0.format((100 * p.value) / whole))} %</span></div>`).join("")
      + (exported > 0 ? `<div class="row"><i style="background:var(--energy-grid-return-color, #8353d1)"></i><span class="n">${esc(L.export)}</span><span class="v">−${esc(nf.format(exported))}</span><span class="p"></span></div>` : "");
    const head = whole > 0
      ? `<div class="head${W < 300 ? " stack" : ""}">${ring(size, 16, slices, center, `${L.cost} ${center[0]} ${unit(ce)}`)}<div class="rows">${rows}</div></div>`
      : `<div class="head"><div class="st"><span class="sl">${esc(L.cost)}</span><span class="sv">${esc(center[0])} <small>${esc(unit(ce))}</small></span></div></div>`;
    // D12 §5.19: what the month's savings bought, one sentence each; a line with nothing to say is absent.
    const lines = resultLines(se, ce, c.deviations ? h.states[c.deviations] : undefined, c.load_names ?? {},
      RESULT_LABELS[labelLang(h)] ?? RESULT_LABELS.en!, lang(h), unit(ce) || String(ce?.attributes?.unit_of_measurement ?? ""));
    // V2: the savings line, R1, is what PowerPlan did for the month: it carries the mark.
    const marked = !savingsView(se, ce).missing;
    const results = lines.length ? `<div class="res">${lines.map((line, i) => `<div>${i === 0 && marked ? markSwatch : ""}${esc(line)}</div>`).join("")}</div>` : "";
    this.shadowRoot!.innerHTML = `<style>${TOKENS}${SHARED}${MARK_CSS}${CSS}</style><ha-card>
      ${head}${results}
      <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(fmtTemplate(L.aria, { month: monthName }))}">${g.join("")}</svg></ha-card>`;
    this.tip ??= new ChartTip(this.shadowRoot!, () => this.shadowRoot!.querySelector("ha-card"));
    this.tip.reset();
  }
}

function tzOffsetMs(d: Date, zone: string): number {
  const p = new Intl.DateTimeFormat("en-US", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(d);
  const g = (t: string) => Number(p.find((x) => x.type === t)!.value);
  return Date.UTC(g("year"), g("month") - 1, g("day"), g("hour"), g("minute"), g("second")) - d.getTime();
}

const CSS = `
  ha-card { position: relative; overflow: hidden; }
  svg { display: block; }
  .head { display: flex; align-items: center; gap: 16px; padding: 14px var(--pp-pad) 8px; box-sizing: border-box; }
  .head.stack { flex-direction: column; align-items: stretch; } .head.stack .pp-ring { align-self: center; }
  .rows { flex: 1; min-width: 0; display: grid; }
  .row { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto 38px; gap: 8px; align-items: center; min-height: 28px;
         font-size: var(--pp-fs-m); border-bottom: 1px solid var(--pp-divider); }
  .row:last-child { border-bottom: 0; }
  .row i { width: 10px; height: 10px; border-radius: 3px; }
  .row .n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .v { font-variant-numeric: tabular-nums; text-align: right; }
  .row .p { font-size: var(--pp-fs-s); color: var(--pp-text2); text-align: right; font-variant-numeric: tabular-nums; }
  .res .pp-mark-sw { margin-right: 8px; vertical-align: 1px; }
  .st { display: flex; flex-direction: column; gap: 2px; }
  .sl { font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .sv { font-size: var(--pp-fs-xl); } .sv small { font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .ss { font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .gl0 { stroke: var(--pp-divider); } .gl { stroke: var(--pp-divider); stroke-dasharray: 2 4; }
  .bar { fill: var(--pp-primary); opacity: .75; } .bar.today { opacity: 1; }
  .cut { fill: var(--pp-card); }
  .res { display: flex; flex-direction: column; gap: 2px; padding: 4px var(--pp-pad) 8px; font-size: var(--pp-fs-m); line-height: 20px; }
  .res div { overflow-wrap: anywhere; }
`;
