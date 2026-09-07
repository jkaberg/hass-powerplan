// `powerplan-price-card` (D12 §5.12 P1–P5): what the household pays now,
// today and tomorrow as one curve, and - where a fixed price (Norgespris) is
// configured - what the same hours would cost without it.
//
// Data: `sensor.<site>_price` (state, `min_today`, `max_today`, `mean_today`,
// `percentile`), `sensor.<site>_price_forecast` → `slots` (`total`,
// `confidence`, and since WP6.4i `energy` - the energy part with its VAT - and
// `reference` - the slot without the fixed-price modifier), the tomorrow
// binary sensor, and `sensor.<site>_level` for the capacity fee.

import { escape, fill, toNumber } from "./appliances-card";
import { type HomeAssistant, timeZone } from "./ha";
import { ppStyles } from "./styles";
import { cheapThreshold, type Credit, creditHtml, currencyWord, localMidnight, type PriceSlot } from "./transforms";

interface PriceConfig {
  entry_id: string;
  entities: { price: string; price_forecast: string; prices_tomorrow?: string; level?: string; fixed_price_savings?: string };
  currency?: string;
  labels?: Record<string, string>;
}

interface Point {
  s: number;
  e: number;
  din: number;
  est: boolean;
  /** The energy part incl. VAT. */
  energy?: number;
  /** The price without the fixed-price modifier. */
  uten?: number;
}

const HOUR_MS = 3_600_000;

export class PowerplanPriceCard extends HTMLElement {
  private config?: PriceConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];
  private width = 0;
  private resize?: ResizeObserver;
  private pts: Point[] = [];
  private geo?: { x0: number; x1: number; t0: number; t1: number; top: number; bottom: number; ymax: number };
  private uid = `ppp${Math.random().toString(36).slice(2, 8)}`;
  private pinned: number | null = null;

  public setConfig(config: PriceConfig): void {
    if (!config?.entities?.price || !config.entities.price_forecast) {
      throw new Error("powerplan-price-card needs entities.price and entities.price_forecast");
    }
    this.config = config;
    this.key = [];
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    const config = this.config;
    if (!config) return;
    const e = config.entities;
    const key = [
      hass.states[e.price],
      hass.states[e.price_forecast],
      e.prices_tomorrow && hass.states[e.prices_tomorrow],
      e.level && hass.states[e.level],
      e.fixed_price_savings && hass.states[e.fixed_price_savings],
      hass.language,
      Math.floor(Date.now() / 60_000),
      this.width,
    ];
    if (key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.render();
  }

  public connectedCallback(): void {
    this.resize ??= new ResizeObserver((entries) => {
      const width = Math.round(entries[0]!.contentRect.width);
      if (width && width !== this.width) {
        this.width = width;
        this.key = [];
        if (this.hassRef) this.hass = this.hassRef;
      }
    });
    this.resize.observe(this);
  }

  public disconnectedCallback(): void {
    this.resize?.disconnect();
  }

  public getCardSize(): number {
    return 7;
  }

  public getGridOptions(): Record<string, number | string> {
    return { columns: "full", rows: "auto" };
  }

  private points(t0: number, t1: number): Point[] {
    const slots = (this.hassRef!.states[this.config!.entities.price_forecast]?.attributes.slots as PriceSlot[] | undefined) ?? [];
    return slots
      .map((slot) => ({
        s: Date.parse(slot.start),
        e: Date.parse(slot.end),
        din: Number(slot.total),
        est: slot.confidence !== "known",
        energy: toNumber(slot.energy) ?? undefined,
        uten: toNumber(slot.reference) ?? undefined,
      }))
      .filter((p) => p.e > t0 && p.s < t1 && Number.isFinite(p.din))
      .sort((a, b) => a.s - b.s);
  }

  // ---------------------------------------------------------------- render

  private render(): void {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    if (!this.shadowRoot) {
      const root = this.attachShadow({ mode: "open" });
      root.addEventListener("pointermove", (event) => this.hover(event as PointerEvent));
      root.addEventListener("pointerleave", () => {
        if (this.pinned === null) this.showTip(null);
      });
      root.addEventListener("click", (event) => this.tap(event as MouseEvent));
    }
    const labels = config.labels ?? {};
    const W = this.width || this.getBoundingClientRect().width || 860;
    const compact = W < 600;
    const H = compact ? 404 : 438;
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const nf = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const nf1 = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const tf = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: zone });
    const word = config.currency ? currencyWord(locale, config.currency) : "";
    const unit = `${word}/kWh`;
    const price = hass.states[config.entities.price];
    const a = price?.attributes ?? {};
    const din = toNumber(price?.state);
    const now = Date.now();
    const t0 = localMidnight(now, zone);
    const t1 = t0 + 48 * HOUR_MS;
    const pts = this.points(t0, t1);
    this.pts = pts;
    const cur = pts.find((p) => p.s <= now && p.e > now);
    const fixed = pts.some((p) => p.uten !== undefined);
    const forecast = hass.states[config.entities.price_forecast]?.attributes ?? {};
    const area = typeof forecast.area === "string" ? forecast.area : "";
    const vat = toNumber(forecast.vat);
    const spotName = fill(labels.spot_now ?? "", { area }).replace(/\s+/g, " ").trim();
    // The month's saving from the fixed price (D-0499), where the integration measures it.
    const monthSaved = config.entities.fixed_price_savings ? toNumber(hass.states[config.entities.fixed_price_savings]?.state) : null;
    const monthName = new Intl.DateTimeFormat(locale, { month: "short", timeZone: zone }).format(now).replace(".", "");
    const savesSub =
      monthSaved === null
        ? fill(labels.fixed_saves_sub ?? "", { unit })
        : fill(labels.fixed_saves_month ?? "", { unit, v: new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(monthSaved), currency: word, month: monthName });

    // The next change, and where today's price stands.
    let nextTxt = "";
    if (cur) {
      const next = pts.find((p) => p.s > now && Math.abs(p.din - cur.din) > 1e-4);
      if (next) {
        nextTxt = compact
          ? fill(labels.from_time ?? "", { v: nf.format(next.din), time: tf.format(next.s) })
          : fill(labels.unchanged_until ?? "", { time: tf.format(next.s), v: nf.format(next.din) });
      }
    }
    const pct = toNumber(a.percentile);
    const chip =
      pct === 0
        ? `<span class="pill good"><i></i>${escape(labels.lowest_today)}</span>`
        : pct === 100
          ? `<span class="pill bad"><i></i>${escape(labels.highest_today)}</span>`
          : "";
    // Spot now with VAT: the energy part, or - under a fixed price - what the reference adds back.
    const grid = cur?.energy !== undefined ? cur.din - cur.energy : null;
    const spotNow = cur ? (cur.uten !== undefined && grid !== null ? cur.uten - grid : fixed ? null : (cur.energy ?? null)) : null;
    const save = cur?.uten !== undefined ? cur.uten - cur.din : null;

    const stat = (name: string | undefined, value: string, sub: string, cls = "") =>
      `<div class="st"><span class="lbl">${escape(name)}</span><span class="val ${cls}">${escape(value)}</span><span class="sub">${escape(sub)}</span></div>`;
    const range = (key: string) => (toNumber(a[key]) === null ? "–" : nf.format(toNumber(a[key])!));
    const stats = compact
      ? ""
      : `<div class="stats">
        ${stat(labels.today, `${range("min_today")}–${range("max_today")}`, fill(labels.average ?? "", { v: range("mean_today"), unit }))}
        ${spotNow !== null ? stat(spotName, nf.format(spotNow), vat ? `${labels.incl_vat ?? ""} · ${fill(labels.excl_vat ?? "", { v: nf.format(spotNow / (1 + vat)) })}` : (labels.incl_vat ?? "")) : ""}
        ${save !== null ? (save > 0 ? stat(labels.fixed_saves, nf.format(save), savesSub, "good") : stat(labels.spot_cheaper, nf.format(-save), unit)) : ""}
      </div>`;
    const value = din === null ? "–" : nf.format(din);
    const head = compact
      ? `<div class="head c"><div class="now"><span class="lbl"><ha-icon icon="mdi:flash"></ha-icon>${escape(labels.price_now)}</span>
           <span class="big">${value}<small>${escape(unit)}</small></span><span class="sub">${escape(nextTxt)}</span></div>
         <div class="rc">${chip}${spotNow !== null ? `<span class="sub r">${escape(spotName)} ${nf.format(spotNow)}${save !== null && save > 0 ? `<br><b class="good">${escape(labels.fixed_saves)} ${nf.format(save)}</b>` : ""}</span>` : ""}</div></div>`
      : `<div class="head"><div class="now"><span class="lbl"><ha-icon icon="mdi:flash"></ha-icon>${escape(labels.price_now)}</span>
           <span class="big">${value}<small>${escape(unit)}</small>${chip}</span><span class="sub">${escape(nextTxt)}</span></div>${stats}</div>`;

    // ---- the chart's geometry
    const pad = compact ? 14 : 16;
    const x0 = pad + (compact ? 26 : 30);
    const x1 = W - pad;
    const top = compact ? 112 : 118;
    const bottom = H - (compact ? 104 : 118);
    const ymax = Math.ceil(Math.max(1, ...pts.map((p) => Math.max(p.din, p.uten ?? 0))) * 2) / 2;
    const sx = (t: number) => x0 + ((t - t0) / (t1 - t0)) * (x1 - x0);
    const sy = (v: number) => bottom - (v / ymax) * (bottom - top);
    this.geo = { x0, x1, t0, t1, top, bottom, ymax };
    const U = this.uid;
    const g: string[] = [];
    // Cheap bands: the lowest quarter of the two days' range.
    const threshold = cheapThreshold(pts.map((p) => p.din));
    if (threshold !== null) {
      let run: [number, number] | null = null;
      const flush = () => {
        if (run) g.push(`<rect class="band" x="${sx(run[0]).toFixed(1)}" y="${top}" width="${(sx(run[1]) - sx(run[0])).toFixed(1)}" height="${bottom - top}"/>`);
        run = null;
      };
      for (const p of pts) {
        if (p.din <= threshold) run = run ? [run[0], p.e] : [p.s, p.e];
        else flush();
      }
      flush();
    }
    for (let v = 0; v <= ymax + 1e-9; v += 0.5) {
      if (compact && Math.abs(v % 1) > 1e-6 && v !== 0) continue;
      g.push(`<line class="${v ? "gl" : "gl0"}" x1="${x0}" y1="${sy(v).toFixed(1)}" x2="${x1}" y2="${sy(v).toFixed(1)}"/>`);
      g.push(`<text class="yl" x="${x0 - 6}" y="${(sy(v) + 4).toFixed(1)}" text-anchor="end">${v ? nf1.format(v) : "0"}</text>`);
    }
    g.push(`<text class="yl" x="${x0 - 6}" y="${top - 10}" text-anchor="end">${escape(word)}</text>`);
    const mid = t0 + 24 * HOUR_MS;
    g.push(`<line class="day" x1="${sx(mid).toFixed(1)}" y1="${top - 16}" x2="${sx(mid).toFixed(1)}" y2="${bottom}"/>`);
    g.push(
      `<text class="dl" x="${x0 + 2}" y="${top - 6}">${escape(labels.today)}</text><text class="dl" x="${(sx(mid) + 6).toFixed(1)}" y="${top - 6}">${escape(labels.tomorrow)}</text>`,
    );
    const est = pts.find((p) => p.est);
    if (est) g.push(`<rect x="${sx(est.s).toFixed(1)}" y="${top}" width="${(x1 - sx(est.s)).toFixed(1)}" height="${bottom - top}" style="fill:url(#${U}-h)"/>`);
    const step = (get: (p: Point) => number | undefined) => {
      let d = "";
      let open = false;
      for (const p of pts) {
        const v = get(p);
        if (v === undefined) {
          open = false;
          continue;
        }
        d += `${open ? "L" : "M"}${sx(p.s).toFixed(1)},${sy(v).toFixed(1)} L${sx(p.e).toFixed(1)},${sy(v).toFixed(1)} `;
        open = true;
      }
      return d;
    };
    const dinD = step((p) => p.din);
    if (pts.length) {
      g.push(`<path d="M${sx(pts[0]!.s).toFixed(1)},${sy(0).toFixed(1)} ${dinD.replace(/^M/, "L")} L${sx(pts[pts.length - 1]!.e).toFixed(1)},${sy(0).toFixed(1)} Z" style="fill:url(#${U}-g)"/>`);
    }
    if (fixed) g.push(`<path class="uten" d="${step((p) => p.uten)}"/>`);
    g.push(`<path class="din" d="${dinD}"/>`);
    const xn = sx(now);
    g.push(`<rect class="past" x="${x0}" y="${top - 2}" width="${Math.max(xn - x0, 0).toFixed(1)}" height="${bottom - top + 2}"/>`);
    g.push(`<line class="nowl" x1="${xn.toFixed(1)}" y1="${top - 2}" x2="${xn.toFixed(1)}" y2="${bottom}"/>`);
    if (cur) {
      if (cur.uten !== undefined) g.push(`<circle class="du" cx="${xn.toFixed(1)}" cy="${sy(cur.uten).toFixed(1)}" r="4"/>`);
      g.push(`<circle class="dd" cx="${xn.toFixed(1)}" cy="${sy(cur.din).toFixed(1)}" r="5"/>`);
    }
    if (!compact) {
      g.push(`<rect class="np" x="${(xn - 14).toFixed(1)}" y="${top - 20}" width="28" height="16" rx="8"/><text class="nt" x="${xn.toFixed(1)}" y="${top - 8.5}" text-anchor="middle">${escape(labels.now)}</text>`);
    }
    const every = compact ? 12 : 6;
    for (let k = 0; k <= 48; k += every) {
      const t = t0 + k * HOUR_MS;
      g.push(`<text class="xl" x="${sx(t).toFixed(1)}" y="${bottom + 16}" text-anchor="${k === 0 ? "start" : k === 48 ? "end" : "middle"}">${escape(tf.format(t))}</text>`);
    }
    g.push(`<g id="tipg"></g>`);
    const svg = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
      <defs><linearGradient id="${U}-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--primary-color);stop-opacity:.34"/><stop offset="1" style="stop-color:var(--primary-color);stop-opacity:.02"/></linearGradient>
      <pattern id="${U}-h" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="6" style="fill:rgba(var(--rgb-primary-text-color,225,225,225),.07)"/></pattern></defs>
      ${g.join("")}</svg>`;

    const legend = `<div class="legend" style="top:${bottom + (compact ? 26 : 30)}px">
      <span><i class="l din"></i>${escape(labels.your_price)}${compact ? "" : ` <em>${escape(fixed ? labels.your_price_fixed_sub : labels.your_price_sub)}</em>`}</span>
      ${fixed ? `<span><i class="l uten"></i>${escape(labels.without_fixed)}${compact ? "" : ` <em>${escape(labels.without_fixed_sub)}</em>`}</span>` : ""}
      <span><i class="sw"></i>${escape(labels.cheap_hours)}</span></div>`;

    // The composition now, the capacity fee, tomorrow's prices.
    const tomorrow = config.entities.prices_tomorrow ? hass.states[config.entities.prices_tomorrow]?.state === "on" : pts.some((p) => p.s >= mid && !p.est);
    const level = config.entities.level ? hass.states[config.entities.level] : undefined;
    const fee = String(level?.attributes.fee ?? "");
    let comp = "";
    if (cur?.energy !== undefined && din !== null && din > 0) {
      const energy = cur.energy;
      const width = Math.max(0, Math.min(100, (energy / din) * 100));
      const energyText = fill((fixed && !compact ? labels.energy_part_fixed : labels.energy_part) ?? "", { v: nf.format(energy) });
      comp = `<div class="bar"><div class="fx" style="width:${width.toFixed(1)}%">${escape(energyText)}</div><div class="gr">${escape(fill(labels.grid_part ?? "", { v: nf.format(din - energy) }))}</div></div>`;
    }
    const tm = `<span class="tm"><ha-icon icon="${tomorrow ? "mdi:calendar-check" : "mdi:calendar-clock"}" class="${tomorrow ? "ok" : ""}"></ha-icon>${escape(tomorrow ? labels.tomorrow_ready : labels.tomorrow_pending)}</span>`;
    // D12 §5.13: the grid tariff's source, credited in one line (D13 §6.1).
    const credit = creditHtml(labels.credit, forecast.credit as Credit[] | undefined);
    const foot = compact
      ? `<div class="foot c">${comp}${tm}</div>`
      : `<div class="foot"><span class="lbl">${escape(labels.now)}</span>${comp}
          ${fee && level ? `<span class="lbl">${escape(fill(labels.capacity_fee ?? "", { fee: formatFee(fee, locale, config.currency), step: level.state }))}</span>` : ""}
          <span class="grow"></span>${tm}</div>${credit ? `<div class="credit">${credit}</div>` : ""}`;

    this.shadowRoot!.innerHTML = `<style>${ppStyles}${CSS}</style>
      <ha-card class="${compact ? "compact" : ""}" style="height:${H}px">${svg}${head}${legend}${foot}<div class="tip" hidden></div></ha-card>`;
    if (this.pinned !== null) this.showTip(this.pinned);
  }

  // ---------------------------------------------------------------- the tooltip

  private hourAt(clientX: number): number | null {
    const g = this.geo;
    const svg = this.shadowRoot?.querySelector("svg.chart");
    if (!g || !svg) return null;
    const x = clientX - svg.getBoundingClientRect().left;
    if (x < g.x0 || x > g.x1) return null;
    const t = g.t0 + ((x - g.x0) / (g.x1 - g.x0)) * (g.t1 - g.t0);
    return Math.floor((t - g.t0) / HOUR_MS) * HOUR_MS + g.t0;
  }

  private hover(event: PointerEvent): void {
    if (event.pointerType === "touch") return;
    this.showTip(this.hourAt(event.clientX));
  }

  private tap(event: MouseEvent): void {
    const hour = this.hourAt(event.clientX);
    this.pinned = hour !== null && hour !== this.pinned ? hour : null;
    this.showTip(this.pinned);
  }

  private showTip(hour: number | null): void {
    const root = this.shadowRoot;
    const g = this.geo;
    const hass = this.hassRef;
    if (!root || !g || !hass) return;
    const tip = root.querySelector(".tip") as HTMLElement;
    const layer = root.getElementById("tipg")!;
    const inHour = hour === null ? [] : this.pts.filter((p) => p.s >= hour && p.s < hour + HOUR_MS);
    if (hour === null || !inHour.length) {
      tip.hidden = true;
      layer.innerHTML = "";
      return;
    }
    const mean = (get: (p: Point) => number | undefined) => {
      const values = inHour.map(get).filter((v): v is number => v !== undefined);
      return values.length ? values.reduce((sum, v) => sum + v, 0) / values.length : null;
    };
    const din = mean((p) => p.din)!;
    const uten = mean((p) => p.uten);
    const energy = mean((p) => p.energy);
    const labels = this.config!.labels ?? {};
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const nf = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const tf = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: zone });
    const unit = `${this.config!.currency ? currencyWord(locale, this.config!.currency) : ""}/kWh`;
    const W = this.width || 860;
    const sx = (t: number) => g.x0 + ((t - g.t0) / (g.t1 - g.t0)) * (g.x1 - g.x0);
    const sy = (v: number) => g.bottom - (v / g.ymax) * (g.bottom - g.top);
    const xm = sx(hour + HOUR_MS / 2);
    layer.innerHTML =
      `<line class="hl" x1="${xm}" y1="${g.top}" x2="${xm}" y2="${g.bottom}"/><circle class="dd" cx="${xm}" cy="${sy(din)}" r="4"/>` +
      (uten !== null ? `<circle class="du" cx="${xm}" cy="${sy(uten)}" r="3.5"/>` : "");
    const day = new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: zone }).format(hour);
    const grid = energy !== null ? din - energy : null;
    tip.innerHTML = `<b>${escape(day)} ${escape(tf.format(hour))}–${escape(tf.format(hour + HOUR_MS))}${inHour.some((p) => p.est) ? ` · ${escape(labels.estimated_short)}` : ""}</b>
      <div class="r"><i class="l din"></i><span>${escape(labels.your_price)}</span><b>${nf.format(din)}</b></div>
      ${uten !== null ? `<div class="r"><i class="l uten"></i><span>${escape(labels.without_fixed)}</span><b>${nf.format(uten)}</b></div>` : ""}
      ${uten !== null && grid !== null ? `<div class="sep">${escape(fill(labels.spot_plus_grid ?? "", { s: nf.format(uten - grid), g: nf.format(grid) }))}</div>
      <div class="r good"><span>${escape(labels.you_save)}</span><b>${nf.format(uten - din)} ${escape(unit)}</b></div>` : ""}`;
    tip.hidden = false;
    tip.style.left = `${xm + 12 + 220 > W ? xm - 12 - 220 : xm + 12}px`;
    tip.style.top = `${g.top + 6}px`;
  }
}

/** `money_text` ("416.00 NOK") in the viewer's language, without decimals. */
function formatFee(text: string, locale: string, currency?: string): string {
  const amount = toNumber(text.split(" ")[0]);
  if (amount === null) return text;
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency: currency || text.split(" ")[1] || "NOK", maximumFractionDigits: 0 }).format(amount);
  } catch {
    return String(Math.round(amount));
  }
}

const CSS = `
  ha-card { position: relative; overflow: hidden; }
  .chart { position: absolute; left: 0; top: 0; display: block; touch-action: pan-y; }
  .head { position: absolute; left: var(--pp-pad); right: 12px; top: 14px; display: flex; justify-content: space-between; gap: 12px; }
  .head.c { left: 14px; right: 14px; top: 12px; }
  .now { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .lbl { font-size: 12px; color: var(--secondary-text-color); display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; --mdc-icon-size: 14px; }
  .lbl ha-icon { color: var(--primary-color); }
  .big { display: flex; align-items: baseline; gap: 6px; font-size: 34px; letter-spacing: -.5px; line-height: 40px; }
  .c .big { font-size: 28px; line-height: 34px; }
  .big small { font-size: 14px; letter-spacing: 0; color: var(--secondary-text-color); }
  .big .pill { align-self: center; margin-left: 6px; }
  .sub { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); white-space: nowrap; }
  .sub.r { text-align: right; font-size: 11px; line-height: 15px; }
  .good { color: #7ccf80; }
  .rc { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
  .stats { display: flex; align-items: flex-start; }
  .st { display: flex; flex-direction: column; gap: 2px; padding: 0 16px; border-left: 1px solid var(--divider-color); min-width: 0; }
  .st:first-child { border-left: 0; }
  .st .lbl { font-size: 12px; }
  .st .val { font-size: 18px; font-weight: 500; white-space: nowrap; }
  .st .sub { font-size: 11px; }
  .pill { display: inline-flex; align-items: center; gap: 5px; height: 20px; padding: 0 8px 0 7px; border-radius: 10px; font-size: 11px; font-weight: 500; letter-spacing: .2px; }
  .pill i { width: 6px; height: 6px; border-radius: 50%; }
  .pill.good { background: rgba(67,160,71,.16); color: #7ccf80; } .pill.good i { background: var(--success-color, #43a047); }
  .pill.bad { background: rgba(255,166,0,.16); color: #ffc15c; } .pill.bad i { background: var(--warning-color, #ffa600); }
  .band { fill: rgba(67,160,71,.075); }
  .gl0 { stroke: rgba(var(--rgb-primary-text-color,225,225,225), .25); stroke-width: 1; }
  .gl { stroke: rgba(var(--rgb-primary-text-color,225,225,225), .07); stroke-width: 1; stroke-dasharray: 2 4; }
  .yl, .xl { font-size: 10px; fill: var(--secondary-text-color); }
  .xl { font-size: 11px; } .compact .xl { font-size: 10px; }
  .dl { font-size: 11px; fill: var(--secondary-text-color); }
  .day { stroke: rgba(var(--rgb-primary-text-color,225,225,225), .16); stroke-width: 1; }
  .din { fill: none; stroke: var(--primary-color); stroke-width: 2.5; stroke-linejoin: round; }
  .uten { fill: none; stroke: var(--warning-color, #ffa600); stroke-width: 1.5; stroke-dasharray: 4 3; opacity: .9; }
  .past { fill: var(--card-background-color, #1c1c1c); opacity: .52; }
  .nowl { stroke: var(--primary-text-color); stroke-width: 1.5; }
  .dd { fill: var(--primary-color); stroke: var(--card-background-color, #1c1c1c); stroke-width: 2.5; }
  .du { fill: var(--warning-color, #ffa600); stroke: var(--card-background-color, #1c1c1c); stroke-width: 2; }
  .np { fill: var(--primary-text-color); } .nt { font-size: 10px; font-weight: 500; fill: var(--card-background-color, #1c1c1c); }
  .hl { stroke: rgba(var(--rgb-primary-text-color,225,225,225), .35); stroke-width: 1; }
  .legend { position: absolute; left: var(--pp-pad); right: var(--pp-pad); display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 12px; line-height: 16px; }
  .compact .legend { left: 14px; right: 14px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  .legend em { font-style: normal; color: var(--secondary-text-color); }
  i.l { width: 14px; display: inline-block; } i.l.din { height: 3px; border-radius: 2px; background: var(--primary-color); }
  i.l.uten { height: 0; border-top: 2px dashed var(--warning-color, #ffa600); }
  i.sw { width: 12px; height: 10px; border-radius: 3px; background: rgba(67,160,71,.35); display: inline-block; }
  .credit { position: absolute; left: var(--pp-pad); right: var(--pp-pad); bottom: 1px; font-size: 10px; line-height: 12px;
    color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .credit a { color: inherit; }
  .foot { position: absolute; left: var(--pp-pad); right: var(--pp-pad); bottom: 14px; display: flex; align-items: center; gap: 14px;
          border-top: 1px solid var(--divider-color); padding-top: 12px; }
  .foot.c { left: 14px; right: 14px; flex-direction: column; align-items: stretch; gap: 6px; padding-top: 10px; bottom: 12px; }
  .bar { display: flex; height: 22px; width: 300px; border-radius: 6px; overflow: hidden; flex: none; font-size: 11px; font-weight: 500; }
  .foot.c .bar { width: auto; height: 18px; font-size: 10px; border-radius: 5px; }
  .bar > div { display: flex; align-items: center; padding-left: 8px; white-space: nowrap; overflow: hidden; }
  .bar .fx { background: color-mix(in srgb, var(--primary-color) 75%, transparent); color: #fff; }
  .bar .gr { flex: 1 1 auto; background: color-mix(in srgb, var(--primary-color) 35%, transparent); }
  .grow { flex: 1 1 auto; }
  .tm { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; white-space: nowrap; --mdc-icon-size: 16px; }
  .foot.c .tm { font-size: 11px; color: var(--secondary-text-color); --mdc-icon-size: 14px; }
  .tm ha-icon { color: var(--secondary-text-color); } .tm ha-icon.ok { color: var(--success-color, #43a047); }
  .tip { position: absolute; width: 220px; box-sizing: border-box; padding: 9px 12px; border-radius: 10px; background: var(--secondary-background-color, #282828);
         border: 1px solid var(--divider-color); box-shadow: 0 6px 20px rgba(0,0,0,.45); font-size: 12px; line-height: 20px; pointer-events: none; }
  .tip .r { display: flex; align-items: center; gap: 8px; } .tip .r span { flex: 1 1 auto; } .tip .r i.l { width: 10px; }
  .tip .sep { border-top: 1px solid var(--divider-color); margin-top: 5px; padding-top: 5px; color: var(--secondary-text-color); }
`;
