// custom:powerplan-price-card - "Strømpris" (iteration 4): what you pay now, today + tomorrow as a curve,
// and what the same hours would cost on spot (Nord Pool + VAT + the same grid tariff).
//
// Iteration 4: theme tokens instead of fixed px/hex; estimated prices drawn as a dashed line instead of
// hatching the whole chart; an alert that says WHY prices are estimated (price sensor unknown or no
// known slot now) with a retry; the Kraft/Nettleie split uses fixed_price from ws_spot, which now
// reads the configured kraftledd entity (0,50 for Norgespris) instead of a slot's `energy` field.
//
// Data
//   entities.price           sensor.<home>_strompris_na   state + next/min_today/max_today/mean_today/percentile
//   entities.price_forecast  sensor.<home>_priser_kjent_til attributes.slots [{start,end,total,confidence}]
//   entities.tomorrow        binary_sensor.<home>_morgendagens_priser (optional)
//   entities.capacity_fee    sensor.energy_level_price (optional, kr/mnd)
//   entities.capacity_step   sensor.<home>_effekttrinn_denne_maneden (optional, "5–10 kW")
//   ws powerplan/spot_prices {entry_id} → spot slots, vat, fixed_price, effect (optional; card works without it)

import { Hass, esc, lang, numFmt, timeFmt, localMidnight, fmtTemplate, newUid, pick, keepFocus } from "./r3-util";
import { TOKENS, SHARED, alertHtml } from "./tokens";

interface PriceCfg {
  type: string;
  entry_id?: string;
  entities: { price: string; price_forecast: string; tomorrow?: string; capacity_fee?: string; capacity_step?: string };
  spot?: boolean;              // default true
  labels?: Record<string, string>;
}

interface Spot {
  area: string; currency: string; vat: number; fixed_price: number | null;
  slots: { start: string; end: string; spot: number }[];
  tomorrow_available: boolean;
  fixed_price_source?: string | null;
  energy_field_check?: number | null;
  effect?: { today_kwh: number; today_nok: number; month_kwh: number; month_nok: number };
}

const P_LABELS: Record<string, Record<string, string>> = {
  nb: {
    now: "Din pris nå", unit: "kr/kWh", lowest: "Laveste i dag", highest: "Høyeste i dag", today: "I dag", avg: "snitt {v} kr/kWh",
    spot_now: "Spot {area} nå", incl_vat: "inkl. mva · {v} eks. mva", saves: "Norgespris sparer", saves_sub: "kr/kWh nå · ≈ {v} kr i {month}",
    spot_cheaper: "Spot er billigere nå", until: "{v} fra {time}", unchanged: "Uendret til {time} · deretter {v}",
    today_lbl: "I dag", tomorrow_lbl: "I morgen", your_price: "Din pris", your_price_sub: "Norgespris + nettleie",
    without: "Uten Norgespris", without_sub: "spot {area} inkl. mva + nettleie", spot: "Spot {area}", cheap: "Billige timer",
    composition: "Nå", energy: "Kraft {v}", energy_fixed: "Kraft {v} · Norgespris", grid: "Nettleie {v}",
    capacity: "+ kapasitetsledd {v} kr/mnd ({step})", tomorrow_ready: "Morgendagens priser klare", tomorrow_pending: "Morgendagens priser kommer ca. 13:00",
    estimated: "Anslått", you_save: "Du sparer", spot_plus: "Spot {s} + nettleie {g}", estimated_line: "anslått",
    stale_title: "Prisene er ikke oppdatert", stale_text: "PowerPlan har ikke fått nye priser siden {time}. Planen bruker anslag til prisene er hentet.",
    stale_text_none: "PowerPlan har ingen kjente priser for denne timen. Planen bruker anslag til prisene er hentet.", retry: "Hent på nytt", retrying: "Henter …",
  },
  en: {
    now: "Your price now", unit: "/kWh", lowest: "Lowest today", highest: "Highest today", today: "Today", avg: "avg {v}",
    spot_now: "Spot {area} now", incl_vat: "incl. VAT · {v} excl.", saves: "Fixed price saves", saves_sub: "per kWh now · ≈ {v} in {month}",
    spot_cheaper: "Spot is cheaper now", until: "{v} from {time}", unchanged: "Unchanged until {time} · then {v}",
    today_lbl: "Today", tomorrow_lbl: "Tomorrow", your_price: "Your price", your_price_sub: "fixed price + grid",
    without: "Without fixed price", without_sub: "spot {area} incl. VAT + grid", spot: "Spot {area}", cheap: "Cheap hours",
    composition: "Now", energy: "Energy {v}", energy_fixed: "Energy {v} · fixed", grid: "Grid {v}",
    capacity: "+ capacity {v}/month ({step})", tomorrow_ready: "Tomorrow's prices are in", tomorrow_pending: "Tomorrow's prices arrive ≈ 13:00",
    estimated: "Estimated", you_save: "You save", spot_plus: "Spot {s} + grid {g}", estimated_line: "estimated",
    stale_title: "Prices are not up to date", stale_text: "PowerPlan has had no new prices since {time}. The plan uses estimates until prices are fetched.",
    stale_text_none: "PowerPlan has no known price for this hour. The plan uses estimates until prices are fetched.", retry: "Fetch again", retrying: "Fetching …",
  },
};

interface Pt { s: number; e: number; din: number; est: boolean; spot?: number; uten?: number }

export class PowerplanPriceCard extends HTMLElement {
  private config?: PriceCfg;
  private hassRef?: Hass;
  private key: unknown[] = [];
  private width = 0;
  private ro?: ResizeObserver;
  private spot?: Spot;
  private spotAt = 0;
  private spotBusy = false;
  private pts: Pt[] = [];
  private geo?: { x0: number; x1: number; t0: number; t1: number; top: number; bottom: number; ymax: number };
  private uid = newUid("ppp");
  private pinned: number | null = null;
  private retry: "idle" | "busy" | "unsupported" = "idle";

  setConfig(c: PriceCfg): void {
    if (!c?.entities?.price || !c.entities.price_forecast) throw new Error("powerplan-price-card needs entities.price and entities.price_forecast");
    this.config = c;
    this.key = [];
  }

  set hass(h: Hass) {
    this.hassRef = h;
    const c = this.config;
    if (!c) return;
    const e = c.entities;
    const k = [h.states[e.price], h.states[e.price_forecast], e.tomorrow && h.states[e.tomorrow], e.capacity_fee && h.states[e.capacity_fee], e.capacity_step && h.states[e.capacity_step],
      lang(h), h.themes?.darkMode, Math.floor(Date.now() / 60e3), this.width, this.spotAt, this.retry];
    if (k.every((v, i) => v === this.key[i])) return;
    this.key = k;
    this.maybeFetchSpot();
    this.render();
  }

  connectedCallback(): void {
    this.style.display ||= "block";   // measurable before the first render (the observer needs a box)
    this.ro ??= new ResizeObserver((en) => {
      const w = Math.round(en[0].contentRect.width);
      if (w && w !== this.width) { this.width = w; this.key = []; if (this.hassRef) this.hass = this.hassRef; }
    });
    this.ro.observe(this);
  }
  disconnectedCallback(): void { this.ro?.disconnect(); }
  getCardSize(): number { return 7; }
  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }

  private async maybeFetchSpot(): Promise<void> {
    const c = this.config!, h = this.hassRef!;
    if (c.spot === false || !c.entry_id || this.spotBusy) return;
    const tomorrowIn = !!(c.entities.tomorrow && h.states[c.entities.tomorrow]?.state === "on");
    const stale = Date.now() - this.spotAt > 15 * 60e3 || (tomorrowIn && this.spot && !this.spot.tomorrow_available);
    if (!stale) return;
    this.spotBusy = true;
    try {
      this.spot = await h.callWS<Spot>({ type: "powerplan/spot_prices", entry_id: c.entry_id });
    } catch (err) {
      this.spot = undefined; // card still renders "Din pris" without spot
    } finally {
      this.spotAt = Date.now();
      this.spotBusy = false;
      this.key = [];
      this.hass = this.hassRef!;
    }
  }

  private labels(): Record<string, string> {
    return { ...pick(P_LABELS, this.hassRef!), ...(this.config!.labels ?? {}) };
  }

  /** Stale = the price sensor is unknown, or no slot covering "now" is known. Returns the alert text or "". */
  private staleText(L: Record<string, string>): string {
    const h = this.hassRef!, c = this.config!;
    const pe = h.states[c.entities.price_forecast];
    const now = Date.now();
    if (!pe || ["unknown", "unavailable"].includes(pe.state)) {
      return pe ? fmtTemplate(L.stale_text, { time: timeFmt(h).format(new Date(pe.last_changed)) }) : L.stale_text_none;
    }
    const cur = (pe.attributes?.slots ?? []).find((s: any) => Date.parse(s.start) <= now && Date.parse(s.end) > now);
    return cur && cur.confidence !== "known" ? L.stale_text_none : "";
  }

  private async refreshPrices(): Promise<void> {
    const h = this.hassRef, c = this.config;
    if (!h || !c?.entry_id || this.retry !== "idle") return;
    this.retry = "busy";
    this.key = []; this.hass = h;
    try {
      await h.callWS({ type: "powerplan/refresh_prices", entry_id: c.entry_id });
      this.retry = "idle";
    } catch {
      this.retry = "unsupported";          // older backend without the command: hide the button
    }
    this.key = []; this.hass = this.hassRef!;
  }

  private buildPoints(t0: number, t1: number): Pt[] {
    const h = this.hassRef!, c = this.config!;
    const slots: any[] = h.states[c.entities.price_forecast]?.attributes?.slots ?? [];
    const pts: Pt[] = slots.map((s) => ({ s: Date.parse(s.start), e: Date.parse(s.end), din: Number(s.total), est: s.confidence !== "known" }))
      .filter((p) => p.e > t0 && p.s < t1 && Number.isFinite(p.din));
    const sp = this.spot;
    if (sp?.slots?.length) {
      const idx = sp.slots.map((x) => ({ s: Date.parse(x.start), e: Date.parse(x.end), v: x.spot }));
      for (const p of pts) {
        const m = idx.find((x) => x.s <= p.s && x.e > p.s);
        if (!m) continue;
        p.spot = m.v;
        const grid = sp.fixed_price != null ? p.din - sp.fixed_price : null;
        p.uten = grid != null ? m.v * (1 + sp.vat) + grid : m.v * (1 + sp.vat);
      }
    }
    return pts;
  }

  // ------------------------------------------------------------------ render
  private render(): void {
    const h = this.hassRef, c = this.config;
    if (!h || !c) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot!.addEventListener("pointermove", (e) => this.hover(e as PointerEvent));
      this.shadowRoot!.addEventListener("pointerleave", () => { if (this.pinned == null) this.showTip(null); });
      this.shadowRoot!.addEventListener("click", (e) => this.tap(e as MouseEvent));
    }
    const L = this.labels();
    const W = this.width || Math.round(this.getBoundingClientRect().width);
    if (!W) return;                                     // not laid out yet; the ResizeObserver renders
    const compact = W < 600;
    const stale = this.staleText(L);
    const off = stale ? (compact ? 132 : 76) : 0;       // room for the alert above the header
    const H = (compact ? 404 : 438) + off;
    const nf = numFmt(h, 2), nf1 = numFmt(h, 1), nf0 = numFmt(h, 0);
    const tf = timeFmt(h);
    const pr = h.states[c.entities.price];
    const a = pr?.attributes ?? {};
    const din = Number(pr?.state);
    const now = Date.now();
    const t0 = localMidnight(h).getTime();
    const t1 = t0 + 48 * 3600e3;
    const pts = this.buildPoints(t0, t1);
    this.pts = pts;
    const cur = pts.find((p) => p.s <= now && p.e > now);
    const sp = this.spot;
    const area = sp?.area ?? "";
    const fixed = sp?.fixed_price ?? null;

    // next change
    let nextTxt = "";
    if (cur) {
      const nx = pts.find((p) => p.s > now && Math.abs(p.din - cur.din) > 1e-4);
      if (nx) nextTxt = compact ? fmtTemplate(L.until, { v: nf.format(nx.din), time: tf.format(nx.s) })
        : fmtTemplate(L.unchanged, { time: tf.format(nx.s), v: nf.format(nx.din) });
    }
    const pct = Number(a.percentile);
    const chip = pct === 0 ? `<span class="pill good"><i></i>${esc(L.lowest)}</span>` : pct === 100 ? `<span class="pill bad"><i></i>${esc(L.highest)}</span>` : "";
    const spotNow = cur?.spot != null && sp ? cur.spot * (1 + sp.vat) : null;
    const save = cur?.uten != null && fixed != null ? cur.uten - cur.din : null;
    const month = new Intl.DateTimeFormat(lang(h), { month: "short", timeZone: h.config.time_zone }).format(new Date()).replace(".", "");

    const stat = (lbl: string, val: string, sub: string, cls = "") =>
      `<div class="st"><span class="lbl">${esc(lbl)}</span><span class="val ${cls}">${esc(val)}</span><span class="sub">${esc(sub)}</span></div>`;
    const stats = compact ? "" : `<div class="stats">
        ${stat(L.today, `${nf.format(Number(a.min_today))}–${nf.format(Number(a.max_today))}`, fmtTemplate(L.avg, { v: nf.format(Number(a.mean_today)) }))}
        ${spotNow != null ? stat(fmtTemplate(L.spot_now, { area }), nf.format(spotNow), fmtTemplate(L.incl_vat, { v: nf.format(cur!.spot!) })) : ""}
        ${save != null ? (save > 0
          ? stat(L.saves, nf.format(save), fmtTemplate(L.saves_sub, { v: sp?.effect ? nf0.format(sp.effect.month_nok) : "–", month }), "good")
          : stat(L.spot_cheaper, nf.format(-save), L.unit, "")) : ""}
      </div>`;
    const head = compact
      ? `<div class="head c" style="top:${12 + off}px"><div class="now"><span class="lbl"><ha-icon icon="mdi:flash"></ha-icon>${esc(L.now)}</span>
           <span class="big">${Number.isFinite(din) ? nf.format(din) : "–"}<small>${esc(L.unit)}</small></span><span class="sub">${esc(nextTxt)}</span></div>
         <div class="rc">${chip}${spotNow != null ? `<span class="sub r">${esc(fmtTemplate(L.spot_now, { area }))} ${nf.format(spotNow)}${save != null && save > 0 ? `<br><b class="good">${esc(L.saves)} ${nf.format(save)}</b>` : ""}</span>` : ""}</div></div>`
      : `<div class="head" style="top:${14 + off}px"><div class="now"><span class="lbl"><ha-icon icon="mdi:flash"></ha-icon>${esc(L.now)}</span>
           <span class="big">${Number.isFinite(din) ? nf.format(din) : "–"}<small>${esc(L.unit)}</small>${chip}</span><span class="sub">${esc(nextTxt)}</span></div>${stats}</div>`;

    // ---- chart geometry
    const pad = compact ? 14 : 16;
    const x0 = pad + (compact ? 26 : 30), x1 = W - pad;
    const top = (compact ? 112 : 118) + off;
    const bottom = H - (compact ? 104 : 118);
    const maxV = Math.max(1, ...pts.map((p) => Math.max(p.din, p.uten ?? 0)));
    const ymax = Math.ceil(maxV * 2) / 2;
    const sx = (t: number) => x0 + (t - t0) / (t1 - t0) * (x1 - x0);
    const sy = (v: number) => bottom - v / ymax * (bottom - top);
    this.geo = { x0, x1, t0, t1, top, bottom, ymax };
    const U = this.uid;
    const g: string[] = [];
    // cheap bands (lowest quarter of the window's range, future + past)
    if (pts.length) {
      const lo = Math.min(...pts.map((p) => p.din)), hi = Math.max(...pts.map((p) => p.din));
      if (hi - lo > 1e-4) {
        const thr = lo + (hi - lo) * 0.25;
        let run: [number, number] | null = null;
        const flush = () => { if (run) g.push(`<rect class="band" x="${sx(run[0]).toFixed(1)}" y="${top}" width="${(sx(run[1]) - sx(run[0])).toFixed(1)}" height="${bottom - top}"/>`); run = null; };
        for (const p of pts) { if (p.din <= thr) { run = run ? [run[0], p.e] : [p.s, p.e]; } else flush(); }
        flush();
      }
    }
    // grid
    for (let v = 0; v <= ymax + 1e-9; v += 0.5) {
      if (compact && Math.abs(v % 1) > 1e-6 && v !== 0) continue;
      g.push(`<line class="${v ? "gl" : "gl0"}" x1="${x0}" y1="${sy(v).toFixed(1)}" x2="${x1}" y2="${sy(v).toFixed(1)}"/>`);
      g.push(`<text class="yl" x="${x0 - 6}" y="${(sy(v) + 4).toFixed(1)}" text-anchor="end">${v ? nf1.format(v) : "0"}</text>`);
    }
    g.push(`<text class="yl" x="${x0 - 6}" y="${top - 10}" text-anchor="end">kr</text>`);
    const mid = t0 + 24 * 3600e3;
    g.push(`<line class="day" x1="${sx(mid).toFixed(1)}" y1="${top - 16}" x2="${sx(mid).toFixed(1)}" y2="${bottom}"/>`);
    g.push(`<text class="dl" x="${x0 + 2}" y="${top - 6}">${esc(L.today_lbl)}</text><text class="dl" x="${(sx(mid) + 6).toFixed(1)}" y="${top - 6}">${esc(L.tomorrow_lbl)}</text>`);
    // lines
    const step = (get: (p: Pt) => number | undefined) => {
      let d = "", open = false;
      for (const p of pts) {
        const v = get(p);
        if (v == null) { open = false; continue; }
        d += `${open ? "L" : "M"}${sx(p.s).toFixed(1)},${sy(v).toFixed(1)} L${sx(p.e).toFixed(1)},${sy(v).toFixed(1)} `;
        open = true;
      }
      return d;
    };
    const dinD = step((p) => p.din);
    if (pts.length) {
      g.push(`<path d="M${sx(pts[0].s).toFixed(1)},${sy(0).toFixed(1)} ${dinD.replace(/^M/, "L")} L${sx(pts[pts.length - 1].e).toFixed(1)},${sy(0).toFixed(1)} Z" style="fill:url(#${U}-g)"/>`);
    }
    if (pts.some((p) => p.uten != null)) g.push(`<path class="uten" d="${step((p) => p.uten)}"/>`);
    g.push(`<path class="din" d="${step((p) => (p.est ? undefined : p.din))}"/>`);
    if (pts.some((p) => p.est)) g.push(`<path class="din est" d="${step((p) => (p.est ? p.din : undefined))}"/>`);
    // past dim + now
    const xn = sx(now);
    g.push(`<rect class="past" x="${x0}" y="${top - 2}" width="${Math.max(xn - x0, 0).toFixed(1)}" height="${bottom - top + 2}"/>`);
    g.push(`<line class="nowl" x1="${xn.toFixed(1)}" y1="${top - 2}" x2="${xn.toFixed(1)}" y2="${bottom}"/>`);
    if (cur) {
      if (cur.uten != null) g.push(`<circle class="du" cx="${xn.toFixed(1)}" cy="${sy(cur.uten).toFixed(1)}" r="4"/>`);
      g.push(`<circle class="dd" cx="${xn.toFixed(1)}" cy="${sy(cur.din).toFixed(1)}" r="5"/>`);
    }
    if (!compact) g.push(`<rect class="np" x="${(xn - 14).toFixed(1)}" y="${top - 20}" width="28" height="16" rx="8"/><text class="nt" x="${xn.toFixed(1)}" y="${top - 8.5}" text-anchor="middle">${esc(this.config!.labels?.now_short ?? "Nå")}</text>`);
    // x labels
    const every = compact ? 12 : 6;
    for (let k = 0; k <= 48; k += every) {
      const t = t0 + k * 3600e3;
      g.push(`<text class="xl" x="${sx(t).toFixed(1)}" y="${bottom + 16}" text-anchor="${k === 0 ? "start" : k === 48 ? "end" : "middle"}">${esc(tf.format(t))}</text>`);
    }
    g.push(`<g id="tipg"></g>`);
    const svg = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
      <defs><linearGradient id="${U}-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--primary-color);stop-opacity:.34"/><stop offset="1" style="stop-color:var(--primary-color);stop-opacity:.02"/></linearGradient>
      </defs>
      ${g.join("")}</svg>`;

    // legend
    const hasUten = pts.some((p) => p.uten != null);
    const legend = `<div class="legend" style="top:${bottom + (compact ? 26 : 30)}px">
      <span><i class="l din"></i>${esc(L.your_price)}${compact ? "" : ` <em>${esc(L.your_price_sub)}</em>`}</span>
      ${pts.some((p) => p.est) ? `<span><i class="l din est"></i>${esc(L.estimated)}</span>` : ""}
      ${hasUten ? `<span><i class="l uten"></i>${esc(fixed != null ? L.without : fmtTemplate(L.spot, { area }))}${compact ? "" : ` <em>${esc(fmtTemplate(L.without_sub, { area }))}</em>`}</span>` : ""}
      <span><i class="sw"></i>${esc(L.cheap)}</span></div>`;

    // composition + tomorrow
    const tomorrowOn = c.entities.tomorrow ? h.states[c.entities.tomorrow]?.state === "on" : !!sp?.tomorrow_available;
    // PowerPlan's level sensor carries the step's fee as `fee` ("416.00 NOK") when no fee entity is given.
    const fee = c.entities.capacity_fee ? Number(h.states[c.entities.capacity_fee]?.state)
      : c.entities.capacity_step ? parseFloat(String(h.states[c.entities.capacity_step]?.attributes?.fee ?? "")) : NaN;
    const stepName = c.entities.capacity_step ? h.states[c.entities.capacity_step]?.state ?? "" : "";
    let comp = "";
    if (fixed != null && Number.isFinite(din)) {
      const grid = din - fixed;
      const fw = Math.max(0, Math.min(100, fixed / din * 100));
      comp = `<div class="bar"><div class="fx" style="width:${fw.toFixed(1)}%">${esc(fmtTemplate(compact ? L.energy : L.energy_fixed, { v: nf.format(fixed) }))}</div><div class="gr">${esc(fmtTemplate(L.grid, { v: nf.format(grid) }))}</div></div>`;
    }
    const foot = compact
      ? `<div class="foot c">${comp}<span class="tm"><ha-icon icon="${tomorrowOn ? "mdi:calendar-check" : "mdi:calendar-clock"}" class="${tomorrowOn ? "ok" : ""}"></ha-icon>${esc(tomorrowOn ? L.tomorrow_ready : L.tomorrow_pending)}</span></div>`
      : `<div class="foot"><span class="lbl">${esc(L.composition)}</span>${comp}
          ${Number.isFinite(fee) ? `<span class="lbl">${esc(fmtTemplate(L.capacity, { v: nf0.format(fee), step: stepName }))}</span>` : ""}
          <span class="grow"></span><span class="tm"><ha-icon icon="${tomorrowOn ? "mdi:calendar-check" : "mdi:calendar-clock"}" class="${tomorrowOn ? "ok" : ""}"></ha-icon>${esc(tomorrowOn ? L.tomorrow_ready : L.tomorrow_pending)}</span></div>`;

    const alert = stale ? `<div class="alertbox">${alertHtml({
      title: L.stale_title, text: compact ? stale.replace(/ Planen bruker.*$| The plan uses.*$/, "") : stale,
      action: c.entry_id && this.retry !== "unsupported" ? { label: this.retry === "busy" ? L.retrying : L.retry, act: "refresh" } : undefined,
    })}</div>` : "";
    keepFocus(this.shadowRoot!, () => {
      this.shadowRoot!.innerHTML = `<style>${TOKENS}${SHARED}${CSS}</style>
        <ha-card class="${compact ? "compact" : ""}" style="height:${H}px">${svg}${alert}${head}${legend}${foot}<div class="tip" hidden></div></ha-card>`;
    });
    if (this.pinned != null) this.showTip(this.pinned);
  }

  // ------------------------------------------------------------------ tooltip
  private idxAt(clientX: number): number | null {
    const g = this.geo;
    const svg = this.shadowRoot?.querySelector("svg.chart") as SVGSVGElement | null;
    if (!g || !svg) return null;
    const r = svg.getBoundingClientRect();
    const x = clientX - r.left;
    if (x < g.x0 || x > g.x1) return null;
    const t = g.t0 + (x - g.x0) / (g.x1 - g.x0) * (g.t1 - g.t0);
    const hourStart = Math.floor((t - g.t0) / 3600e3) * 3600e3 + g.t0;
    return hourStart;
  }
  private hover(e: PointerEvent): void {
    if (e.pointerType === "touch") return;
    this.showTip(this.idxAt(e.clientX));
  }
  private tap(e: MouseEvent): void {
    if ((e.target as HTMLElement).closest('[data-act="refresh"]')) { this.refreshPrices(); return; }
    const hs = this.idxAt(e.clientX);
    this.pinned = hs != null && hs !== this.pinned ? hs : null;
    this.showTip(this.pinned);
  }
  private showTip(hourStart: number | null): void {
    const root = this.shadowRoot, g = this.geo, h = this.hassRef;
    if (!root || !g || !h) return;
    const tip = root.querySelector(".tip") as HTMLElement;
    const tg = root.getElementById("tipg")!;
    if (hourStart == null) { tip.hidden = true; tg.innerHTML = ""; return; }
    const inHour = this.pts.filter((p) => p.s >= hourStart && p.s < hourStart + 3600e3);
    if (!inHour.length) { tip.hidden = true; tg.innerHTML = ""; return; }
    const avg = (f: (p: Pt) => number | undefined) => {
      const v = inHour.map(f).filter((x): x is number => x != null);
      return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null;
    };
    const din = avg((p) => p.din)!, uten = avg((p) => p.uten), spot = avg((p) => p.spot);
    const L = this.labels();
    const nf = numFmt(h, 2), tf = timeFmt(h);
    const W = this.width || 860;
    const sx = (t: number) => g.x0 + (t - g.t0) / (g.t1 - g.t0) * (g.x1 - g.x0);
    const sy = (v: number) => g.bottom - v / g.ymax * (g.bottom - g.top);
    const xm = sx(hourStart + 1800e3);
    tg.innerHTML = `<line class="hl" x1="${xm}" y1="${g.top}" x2="${xm}" y2="${g.bottom}"/>` +
      `<circle class="dd" cx="${xm}" cy="${sy(din)}" r="4"/>` + (uten != null ? `<circle class="du" cx="${xm}" cy="${sy(uten)}" r="3.5"/>` : "");
    const sp = this.spot;
    const day = new Intl.DateTimeFormat(lang(h), { weekday: "short", timeZone: h.config.time_zone }).format(hourStart);
    tip.innerHTML = `<b>${esc(day)} ${esc(tf.format(hourStart))}–${esc(tf.format(hourStart + 3600e3))}${inHour.some((p) => p.est) ? ` · ${esc(L.estimated)}` : ""}</b>
      <div class="r"><i class="l din"></i><span>${esc(L.your_price)}</span><b>${nf.format(din)}</b></div>
      ${uten != null ? `<div class="r"><i class="l uten"></i><span>${esc(sp?.fixed_price != null ? L.without : fmtTemplate(L.spot, { area: sp?.area ?? "" }))}</span><b>${nf.format(uten)}</b></div>` : ""}
      ${spot != null && sp?.fixed_price != null ? `<div class="sep">${esc(fmtTemplate(L.spot_plus, { s: nf.format(spot * (1 + sp.vat)), g: nf.format(din - sp.fixed_price) }))}</div>
      <div class="r good"><span>${esc(L.you_save)}</span><b>${nf.format(uten! - din)} ${esc(L.unit)}</b></div>` : ""}`;
    tip.hidden = false;
    const left = xm + 12 + 240 > W ? xm - 12 - 240 : xm + 12;
    tip.style.left = `${left}px`;
    tip.style.top = `${g.top + 6}px`;
  }
}

const CSS = `
  ha-card { position: relative; overflow: hidden; }
  .alertbox { position: absolute; left: var(--pp-pad); right: var(--pp-pad); top: var(--pp-pad); }
  .compact .alertbox { left: 12px; right: 12px; top: 12px; }
  .compact .alert { flex-wrap: wrap; row-gap: 0; }
  .compact .alert .at { flex-basis: calc(100% - 40px); }
  .compact .alert .tbtn { margin-left: auto; }
  .chart { position: absolute; left: 0; top: 0; display: block; touch-action: pan-y; }
  .head { position: absolute; left: var(--pp-pad); right: 12px; top: 14px; display: flex; justify-content: space-between; gap: 12px; }
  .head.c { left: 14px; right: 14px; top: 12px; }
  .now { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .lbl { font-size: var(--pp-fs-s); color: var(--pp-text2); display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; --mdc-icon-size: 16px; }
  .lbl ha-icon { color: var(--pp-primary); }
  .big { display: flex; align-items: baseline; gap: 6px; font-size: var(--pp-fs-4xl); letter-spacing: -.5px; line-height: 40px; }
  .c .big { font-size: var(--pp-fs-3xl); line-height: 34px; }
  .big small { font-size: var(--pp-fs-m); letter-spacing: 0; color: var(--pp-text2); }
  .big .pill { align-self: center; margin-left: 6px; }
  .sub { font-size: var(--pp-fs-s); line-height: 16px; color: var(--pp-text2); white-space: nowrap; }
  .sub.r { text-align: right; line-height: 16px; }
  .good { color: var(--pp-ok-text); }
  .rc { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
  .stats { display: flex; align-items: flex-start; }
  .st { display: flex; flex-direction: column; gap: 2px; padding: 0 16px; border-left: 1px solid var(--pp-divider); min-width: 0; }
  .st:first-child { border-left: 0; }
  .st .val { font-size: var(--pp-fs-l); font-weight: var(--pp-fw-m); white-space: nowrap; }
  .pill { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px 0 8px; border-radius: 12px; font-size: var(--pp-fs-s); font-weight: var(--pp-fw-m); color: var(--pp-text); }
  .pill i { width: 8px; height: 8px; border-radius: 50%; }
  .pill.good { background: var(--pp-ok-bg); } .pill.good i { background: var(--pp-ok); }
  .pill.bad { background: var(--pp-warn-bg); } .pill.bad i { background: var(--pp-warn); }
  .band { fill: var(--pp-cheap); }
  .gl0 { stroke: var(--pp-divider); stroke-width: 1; }
  .gl { stroke: var(--pp-divider); stroke-width: 1; stroke-dasharray: 2 4; }
  .yl, .xl, .dl { font-size: var(--pp-fs-s); fill: var(--pp-text2); font-family: var(--pp-font); }
  .day { stroke: var(--pp-divider); stroke-width: 1; }
  .din { fill: none; stroke: var(--pp-primary); stroke-width: 2.5; stroke-linejoin: round; }
  .din.est { stroke-dasharray: 6 4; stroke-width: 2; }
  .uten { fill: none; stroke: var(--pp-warn); stroke-width: 1.5; stroke-dasharray: 4 3; opacity: .9; }
  .past { fill: var(--pp-card); opacity: .52; }
  .nowl { stroke: var(--pp-text); stroke-width: 1.5; }
  .dd { fill: var(--pp-primary); stroke: var(--pp-card); stroke-width: 2.5; }
  .du { fill: var(--pp-warn); stroke: var(--pp-card); stroke-width: 2; }
  .np { fill: var(--pp-text); } .nt { font-size: var(--pp-fs-s); font-weight: var(--pp-fw-m); fill: var(--pp-card); font-family: var(--pp-font); }
  .hl { stroke: var(--pp-text3); stroke-width: 1; }
  .legend { position: absolute; left: var(--pp-pad); right: var(--pp-pad); display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: var(--pp-fs-s); line-height: 16px; }
  .compact .legend { left: 14px; right: 14px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  .legend em { font-style: normal; color: var(--pp-text2); }
  i.l { width: 16px; display: inline-block; } i.l.din { height: 3px; border-radius: 2px; background: var(--pp-primary); }
  i.l.din.est { height: 0; border-top: 2px dashed var(--pp-primary); background: none; }
  i.l.uten { height: 0; border-top: 2px dashed var(--pp-warn); }
  i.sw { width: 12px; height: 10px; border-radius: 3px; background: var(--pp-cheap); outline: 1px solid rgba(var(--rgb-success-color, 67, 160, 71), 0.35); display: inline-block; }
  .foot { position: absolute; left: var(--pp-pad); right: var(--pp-pad); bottom: 14px; display: flex; align-items: center; gap: 14px;
          border-top: 1px solid var(--pp-divider); padding-top: 12px; }
  .foot.c { left: 14px; right: 14px; flex-direction: column; align-items: stretch; gap: 6px; padding-top: 10px; bottom: 12px; }
  .bar { display: flex; height: 24px; width: 320px; border-radius: 6px; overflow: hidden; flex: none; font-size: var(--pp-fs-s); font-weight: var(--pp-fw-m); }
  .foot.c .bar { width: auto; height: 22px; }
  .bar > div { display: flex; align-items: center; padding-left: 8px; white-space: nowrap; overflow: hidden; color: var(--pp-text); }
  .bar .fx { background: var(--pp-price-hi); }
  .bar .gr { flex: 1 1 auto; background: var(--pp-price-lo); }
  .grow { flex: 1 1 auto; }
  .tm { display: inline-flex; align-items: center; gap: 6px; font-size: var(--pp-fs-s); white-space: nowrap; --mdc-icon-size: 16px; }
  .foot.c .tm { color: var(--pp-text2); }
  .tm ha-icon { color: var(--pp-text2); } .tm ha-icon.ok { color: var(--pp-ok); }
  .tip { position: absolute; width: 240px; box-sizing: border-box; padding: 10px 12px; border-radius: 10px; background: var(--pp-card);
         border: 1px solid var(--pp-divider); box-shadow: 0 6px 20px rgba(0,0,0,.3); font-size: var(--pp-fs-m); line-height: 22px; pointer-events: none; }
  .tip .r { display: flex; align-items: center; gap: 8px; } .tip .r span { flex: 1 1 auto; } .tip .r i.l { width: 12px; }
  .tip .sep { border-top: 1px solid var(--pp-divider); margin-top: 5px; padding-top: 5px; color: var(--pp-text2); font-size: var(--pp-fs-s); }
`;
