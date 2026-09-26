// `powerplan-day-profile` (D12 §5.20 V6): what PowerPlan did to an average day. One column per local
// hour - the appliances' energy with PowerPlan, what it moved in and what it moved out - against the
// same day without it, a dashed step line. From `sensor.<site>_savings` → `day_profile` and
// `previous_day_profile` (D11 §5.12): the month the History picker's period starts in, this or last.

import { followPeriod, type Period } from "./energy";
import { type HomeAssistant, timeZone } from "./ha";
import { ChartTip, MARK_CSS, tipAttr } from "./marks";
import { hatchDef } from "./tokens";
import { ppStyles } from "./styles";
import { profileSummary, profileView } from "./transforms";

interface ProfileConfig {
  entry_id?: string;
  entities: { savings: string };
  labels?: Record<string, string>;
}

const escape = (value: string) => value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const fill = (text: string, values: Record<string, string>) => text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);
const hh = (h: number) => String(((h % 24) + 24) % 24).padStart(2, "0");

export class PowerplanDayProfile extends HTMLElement {
  private config?: ProfileConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];
  private width = 0;
  private period?: Period;
  private resize?: ResizeObserver;
  private unfollow?: () => void;
  private tip?: ChartTip;

  public setConfig(config: ProfileConfig): void {
    if (!config?.entities?.savings) throw new Error("powerplan-day-profile needs entities.savings");
    this.config = config;
    this.key = [];
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    if (!this.config) return;
    this.follow();
    const key = [hass.states[this.config.entities.savings], hass.language, hass.themes.darkMode, this.width, this.period];
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
    this.follow();
  }

  public disconnectedCallback(): void {
    this.resize?.disconnect();
    this.unfollow?.();
    this.unfollow = undefined;
  }

  public getCardSize(): number { return 5; }
  public getGridOptions() { return { columns: "full", rows: "auto", min_columns: 6 }; }

  private follow(): void {
    if (!this.hassRef || this.unfollow || !this.isConnected) return;
    this.unfollow = () => undefined; // the picker may answer at once, inside followPeriod
    this.unfollow = followPeriod(this.hassRef, (period) => {
      this.period = period;
      if (this.hassRef) this.hass = this.hassRef;
    });
  }

  private render(): void {
    const hass = this.hassRef, config = this.config;
    if (!hass || !config || !this.width) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const start = this.period?.start ?? new Date();
    const profile = profileView(hass.states[config.entities.savings]?.attributes, start, zone);
    const monthName = new Intl.DateTimeFormat(locale, { month: "long", timeZone: zone }).format(start);
    const style = `<style>${ppStyles}${MARK_CSS}
      :host { --pp-hatch: rgba(var(--rgb-primary-text-color, 20, 20, 20), .5); }
      ha-card { position: relative; }
      .pp-content { display: flex; flex-direction: column; gap: 10px; }
      svg { display: block; overflow: visible; }
      .axis { font-size: 11px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); }
      .legend span { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
      .legend i { width: 10px; height: 10px; border-radius: 3px; }
      .legend i.line { height: 0; width: 14px; border-radius: 0; border-top: 2px dashed var(--secondary-text-color); }
      .legend i.out { background: repeating-linear-gradient(45deg, rgba(var(--rgb-primary-text-color, 20, 20, 20), .5) 0 2px, transparent 2px 5px); }
    </style>`;
    if (!profile) {
      const empty = hass.states[config.entities.savings]?.attributes.day_profile ? labels.profile_months : labels.profile_none;
      this.shadowRoot!.innerHTML = `${style}<ha-card><div class="pp-content"><div class="pp-empty">${escape(empty ?? "")}</div></div></ha-card>`;
      return;
    }
    const sum = profileSummary(profile);
    const width = this.width - 32, narrow = width < 468;
    const L = 26, R = 6, top = 8, ph = narrow ? 140 : 170, H = top + ph + 24;
    const peak = Math.max(...sum.with, ...sum.without, 0.1);
    const step = [0.5, 1, 2, 5, 10, 20, 50].find((s) => peak / s <= (narrow ? 2 : 4)) ?? 100;
    const ymax = Math.ceil(peak / step) * step;
    const X = (h: number) => L + (h / 24) * (width - L - R), Y = (v: number) => top + ph - (v / ymax) * ph;
    const g: string[] = [`<defs>${hatchDef("pp-out")}</defs>`];
    for (let v = 0; v <= ymax + 1e-9; v += step) {
      g.push(`<line x1="${L}" x2="${width - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="${v ? "var(--divider-color)" : "rgba(var(--rgb-primary-text-color, 20, 20, 20), .25)"}"${v ? ' stroke-dasharray="2 4"' : ""}/>`);
      g.push(`<text class="axis" x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${escape(one.format(v).replace(/[,.]0$/, ""))}</text>`);
    }
    sum.with.forEach((a, i) => {
      const c = sum.without[i]!, x = X(i) + 0.8, bw = X(i + 1) - X(i) - 1.6, low = Math.min(a, c);
      g.push(`<rect x="${x.toFixed(1)}" y="${Y(low).toFixed(1)}" width="${bw.toFixed(1)}" height="${(Y(0) - Y(low)).toFixed(1)}" rx="2" fill="rgba(var(--rgb-primary-color, 0, 154, 199), .2)"/>`);
      if (a - c > 0.005) g.push(`<rect x="${x.toFixed(1)}" y="${Y(a).toFixed(1)}" width="${bw.toFixed(1)}" height="${(Y(c) - Y(a)).toFixed(1)}" rx="2" fill="rgba(var(--rgb-primary-color, 0, 154, 199), .75)"/>`);
      if (c - a > 0.005) g.push(`<rect x="${x.toFixed(1)}" y="${Y(c).toFixed(1)}" width="${bw.toFixed(1)}" height="${(Y(a) - Y(c)).toFixed(1)}" fill="url(#pp-out)"/>`);
    });
    let d = `M${X(0).toFixed(1)},${Y(sum.without[0]!).toFixed(1)}`;
    sum.without.forEach((v, i) => { if (i) d += `V${Y(v).toFixed(1)}`; d += `H${X(i + 1).toFixed(1)}`; });
    g.push(`<path d="${d}" fill="none" stroke="var(--secondary-text-color)" stroke-width="1.5" stroke-dasharray="4 3"/>`);
    for (let h = 0; h <= 24; h += narrow ? 6 : 3) {
      g.push(`<text class="axis" x="${X(h).toFixed(1)}" y="${top + ph + 16}" text-anchor="${h === 0 ? "start" : h === 24 ? "end" : "middle"}">${hh(h)}</text>`);
    }
    sum.with.forEach((a, i) => {
      const c = sum.without[i]!, delta = a - c;
      const lines = [`${hh(i)}–${hh(i + 1)}`, `${labels.with_powerplan ?? ""} ${one.format(a)} kWh`, `${labels.without_powerplan ?? ""} ${one.format(c)} kWh`];
      if (Math.abs(delta) > 0.05) lines.push(`${delta > 0 ? labels.moved_in ?? "" : labels.moved_out ?? ""}: ${one.format(Math.abs(delta))} kWh`);
      g.push(`<rect x="${X(i).toFixed(1)}" y="${top}" width="${(X(i + 1) - X(i)).toFixed(1)}" height="${ph}" fill="transparent"${tipAttr(lines, false)}/>`);
    });
    const most = sum.from && sum.to
      ? fill(labels.profile_most ?? "", { from: `${hh(sum.from[0])}–${hh(sum.from[1])}`, to: `${hh(sum.to[0])}–${hh(sum.to[1])}` })
      : "";
    const sub = [most, fill(labels.profile_month ?? "{month}", { month: monthName })].filter(Boolean).join(" · ");
    this.shadowRoot!.innerHTML = `${style}<ha-card><div class="pp-content">
      <div><span class="pp-stat-value">${escape(one.format(sum.movedPerDay))}<span class="pp-stat-unit">${escape(labels.moved_per_day ?? "kWh")}</span></span>
        <div class="pp-sub">${escape(sub)}</div></div>
      <svg width="${width}" height="${H}" viewBox="0 0 ${width} ${H}" role="img" aria-label="${escape(`${one.format(sum.movedPerDay)} ${labels.moved_per_day ?? ""}. ${sub}`)}">${g.join("")}</svg>
      <div class="legend"><span><i style="background:rgba(var(--rgb-primary-color, 0, 154, 199), .2)"></i>${escape(labels.with_powerplan ?? "")}</span>
        <span><i class="line"></i>${escape(labels.without_powerplan ?? "")}</span>
        <span><i style="background:rgba(var(--rgb-primary-color, 0, 154, 199), .75)"></i>${escape(labels.moved_in ?? "")}</span>
        <span><i class="out"></i>${escape(labels.moved_out ?? "")}</span></div>
    </div></ha-card>`;
    this.tip ??= new ChartTip(this.shadowRoot!, () => this.shadowRoot!.querySelector("ha-card"));
    this.tip.reset();
  }
}
