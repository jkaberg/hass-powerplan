// Shared helpers for the PowerPlan cards (iteration 4). Replaces the iteration-3 file of the same name.
// Iteration 4 adds: hold/paused per slot, windows(), resolvePx() for theme tokens, keepFocus().

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, any>;
  last_changed: string;
  last_updated?: string;
}

export interface Hass {
  states: Record<string, HassEntity>;
  language: string;
  locale?: { language: string; time_zone?: string };
  config: { time_zone: string; currency?: string };
  themes?: { darkMode?: boolean };
  user?: { is_admin?: boolean };
  callWS<T = any>(msg: Record<string, any>): Promise<T>;
  callService(domain: string, service: string, data?: Record<string, any>): Promise<any>;
  localize?(key: string, ...args: any[]): string;
  loadBackendTranslation?(category: string, integration?: string | string[]): Promise<any>;
  connection?: any;
}

export const esc = (v: unknown): string =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** HA can be set to use the server or the browser time zone. */
export function tz(hass: Hass): string {
  const lz = (hass as any).locale?.time_zone;
  if (lz === "local") return Intl.DateTimeFormat().resolvedOptions().timeZone;
  return hass.config.time_zone;
}

export function lang(hass: Hass): string {
  return hass.locale?.language || hass.language || "en";
}

/** "nb" for nb/no/nn, else the base language, used to pick a label table. */
export function labelLang(hass: Hass): string {
  const l = lang(hass).split("-")[0];
  return ["no", "nn", "nb"].includes(l) ? "nb" : l;
}

export function pick<T>(tables: Record<string, T>, hass: Hass): T {
  return tables[labelLang(hass)] ?? tables.en;
}

export function numFmt(hass: Hass, digits = 2): Intl.NumberFormat {
  return new Intl.NumberFormat(lang(hass), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 0–1 decimals, for temperatures and kW ("68", "22,8"). */
export function numFmt01(hass: Hass): Intl.NumberFormat {
  return new Intl.NumberFormat(lang(hass), { minimumFractionDigits: 0, maximumFractionDigits: 1 });
}

export function timeFmt(hass: Hass): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(lang(hass), { hour: "2-digit", minute: "2-digit", timeZone: tz(hass) });
}

/** Hours since local midnight, in the HA time zone. */
export function localHour(d: Date | number, hass: Hass): number {
  const p = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: tz(hass),
  }).formatToParts(new Date(d));
  const h = Number(p.find((x) => x.type === "hour")!.value);
  const m = Number(p.find((x) => x.type === "minute")!.value);
  return h + m / 60;
}

export function startOfHour(now = new Date()): Date {
  const d = new Date(now);
  d.setMinutes(0, 0, 0);
  return d;
}

export function localMidnight(hass: Hass, now = new Date()): Date {
  const h = localHour(now, hass);
  const d = new Date(now.getTime() - h * 3600e3);
  d.setSeconds(0, 0);
  return d;
}

export function fire(node: EventTarget, type: string, detail: any = {}): void {
  node.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
}

export function moreInfo(node: HTMLElement, entityId: string): void {
  fire(node, "hass-more-info", { entityId });
}

export function navigate(path: string): void {
  history.pushState(null, "", path);
  window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
}

export function fmtTemplate(s: string, params: Record<string, string | number>): string {
  return s.replace(/\{(\w+)\}/g, (_, k) => String(params[k] ?? ""));
}

export function rgbaHex(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function lum(hex: string): number {
  const h = hex.replace("#", "");
  const c = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

/** Icon colour for an appliance on the card background: keeps the hue, guarantees ≥ 3:1. */
export function readable(hex: string, dark: boolean): string {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return hex;
  const bg = lum(dark ? "#1c1c1c" : "#ffffff");
  const ratio = (l: number) => (dark ? (l + 0.05) / (bg + 0.05) : (bg + 0.05) / (l + 0.05));
  let [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(1 + i, 3 + i), 16));
  const toHex = () => "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
  // 4.5:1 against the card, so the icon still clears 3:1 on its own 20 % tinted bubble
  for (let i = 0; i < 14 && ratio(lum(toHex())) < 4.5; i++) {
    if (dark) { r = Math.round(r + (255 - r) * 0.18); g = Math.round(g + (255 - g) * 0.18); b = Math.round(b + (255 - b) * 0.18); }
    else { r = Math.round(r * 0.84); g = Math.round(g * 0.84); b = Math.round(b * 0.84); }
  }
  return toHex();
}

/** Plan slots from sensor.<home>_planlagt_forbruk (attributes.slots). */
export interface PlanSlot {
  start: Date;
  end: Date;
  planned: Record<string, number>;  // kWh moved by PowerPlan, per load id
  hold: Record<string, number>;     // kWh to keep temperature, per load id (not moved)
  paused: string[];                 // load ids lowered ("senket") in this slot
  baseline: number | null;          // kWh, unmanaged
  baselineP90: number | null;       // kWh, unmanaged high estimate
  ceiling: number | null;           // kWh per window (window_min)
}

const numMap = (o: any): Record<string, number> =>
  Object.fromEntries(Object.entries(o ?? {}).map(([k, v]) => [k, Number(v) || 0]));

export function readPlan(ent: HassEntity | undefined): { slots: PlanSlot[]; windowMin: number; byLoad: Record<string, any> } {
  const a = ent?.attributes ?? {};
  const slots: PlanSlot[] = (a.slots ?? []).map((s: any) => ({
    start: new Date(s.start),
    end: new Date(s.end),
    planned: numMap(s.planned_kwh),
    hold: numMap(s.hold_kwh),
    paused: Array.isArray(s.paused) ? s.paused.map(String) : [],
    baseline: s.baseline_kwh == null ? null : Number(s.baseline_kwh),
    baselineP90: s.baseline_p90_kwh == null ? null : Number(s.baseline_p90_kwh),
    ceiling: s.ceiling_kwh == null ? null : Number(s.ceiling_kwh),
  }));
  return { slots, windowMin: Number(a.window_min) || 60, byLoad: a.by_load ?? {} };
}

export interface Win { start: Date; end: Date; kwh: number }

/** Merge consecutive slots where `pickFn` is true into windows (kWh summed from `valueFn`). */
export function windows(slots: PlanSlot[], pickFn: (s: PlanSlot) => boolean, valueFn: (s: PlanSlot) => number = () => 0): Win[] {
  const out: Win[] = [];
  for (const s of slots) {
    if (!pickFn(s)) continue;
    const last = out[out.length - 1];
    if (last && Math.abs(last.end.getTime() - s.start.getTime()) < 1000) {
      last.end = s.end;
      last.kwh += valueFn(s);
    } else {
      out.push({ start: s.start, end: s.end, kwh: valueFn(s) });
    }
  }
  return out;
}

/** Runs PowerPlan moved for one load. */
export const runsFor = (id: string, slots: PlanSlot[]): Win[] =>
  windows(slots, (s) => (s.planned[id] ?? 0) > 0.0005, (s) => s.planned[id] ?? 0);

/** Hours in which a heating load is lowered ("senket"). Only for loads that hold temperature at all. */
export function loweredFor(id: string, slots: PlanSlot[]): Win[] {
  if (!slots.some((s) => (s.hold[id] ?? 0) > 0.0005)) return [];
  return windows(slots, (s) => s.paused.includes(id) && (s.planned[id] ?? 0) <= 0.0005);
}

export const holdKwh = (id: string, slots: PlanSlot[]): number => slots.reduce((t, s) => t + (s.hold[id] ?? 0), 0);

/** Resolve a CSS length token (e.g. --ha-font-size-s) to px for canvas/ECharts, via a probe. */
export function resolvePx(host: Element, token: string, fallback: number): number {
  const root = (host.shadowRoot ?? host) as ShadowRoot | Element;
  const probe = document.createElement("span");
  probe.style.cssText = `position:absolute;visibility:hidden;font-size:var(${token}, ${fallback}px)`;
  root.appendChild(probe);
  const px = parseFloat(getComputedStyle(probe).fontSize);
  probe.remove();
  return Number.isFinite(px) && px > 0 ? px : fallback;
}

/** Re-render with innerHTML without losing keyboard focus (focus follows data-focus-key). */
export function keepFocus(root: ShadowRoot, write: () => void): void {
  const active = root.activeElement as HTMLElement | null;
  const key = active?.getAttribute("data-focus-key");
  write();
  if (key) (root.querySelector(`[data-focus-key="${CSS.escape(key)}"]`) as HTMLElement | null)?.focus();
}

export function toNum(v: unknown): number | null {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : null;
}

/** Savings are hidden when there is no reference cost to compare with: `reason: no_reference` (savings_guard.py)
 *  or `savings_confidence: none` (what the live backend publishes, with state 0E-8). */
export function savingsView(sav: HassEntity | undefined, cost: HassEntity | undefined): { value: number | null; missing: boolean } {
  if (!sav || ["unknown", "unavailable", ""].includes(sav.state) || sav.attributes?.reason === "no_reference"
    || sav.attributes?.savings_confidence === "none") return { value: null, missing: true };
  const s = toNum(sav.state), c = toNum(cost?.state);
  // Until the backend guard is deployed: savings exactly equal to −cost means the reference was 0.
  if (s !== null && c !== null && c > 0.005 && Math.abs(s + c) < 0.005) return { value: null, missing: true };
  return { value: s, missing: s === null };
}

/** The axis top when one day holds over 3 × the next largest (a booked lump), else null: 1,25 × that next day. */
export function outlierCap(values: number[]): number | null {
  const [first, second] = [...values].sort((a, b) => b - a);
  return first !== undefined && second !== undefined && second > 0 && first > 3 * second ? second * 1.25 : null;
}

/** The words of `resultLines`, nb and en (D12 §5.19). */
export const RESULT_LABELS: Record<string, Record<string, string>> = {
  nb: {
    saved: "Spart {total} {unit} · effekttrinn {capacity} · billigere timer {energy}",
    cost_more: "Kostet {total} {unit} mer enn uten PowerPlan",
    pending: "Besparelsen telles når døgnet er over",
    step_without: "Effekttrinn {step} – uten PowerPlan {without}",
    step_same: "Effekttrinn {step} – det samme uten PowerPlan",
    price: "Apparatene betalte {paid} mot {reference} {unit}/kWh",
    missed_one: "1 frist nådd ikke", missed: "{n} frister nådd ikke",
    comfort: "{time} under komfort: {load}",
    over_one: "1 time over effektmålet", over: "{n} timer over effektmålet",
    hours: "{h} t", hours_minutes: "{h} t {m} min", minutes: "{m} min",
  },
  en: {
    saved: "Saved {total} {unit} · capacity step {capacity} · cheaper hours {energy}",
    cost_more: "Cost {total} {unit} more than without PowerPlan",
    pending: "Savings are counted when the day is over",
    step_without: "Capacity step {step} – without PowerPlan {without}",
    step_same: "Capacity step {step} – the same without PowerPlan",
    price: "Appliances paid {paid} against {reference} {unit}/kWh",
    missed_one: "1 deadline missed", missed: "{n} deadlines missed",
    comfort: "{time} below comfort: {load}",
    over_one: "1 hour over the target", over: "{n} hours over the target",
    hours: "{h} h", hours_minutes: "{h} h {m} min", minutes: "{m} min",
  },
};

const sumOf = (v: unknown): number =>
  v && typeof v === "object" ? Object.values(v as Record<string, unknown>).reduce<number>((a, x) => a + (Number(x) || 0), 0) : 0;

/**
 * The month's results in one sentence each (D12 §5.19): savings by source, the step without
 * PowerPlan, the price paid against the reference, and the deviations only when there are any.
 */
export function resultLines(
  sav: HassEntity | undefined, cost: HassEntity | undefined, dev: HassEntity | undefined,
  loads: Record<string, string>, L: Record<string, string>, locale: string, unit: string,
): string[] {
  const out: string[] = [];
  const f0 = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
  const f2 = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const a = sav?.attributes ?? {};
  const view = savingsView(sav, cost);
  if (!view.missing && view.value !== null) {
    const money = (v: unknown) => f0.format(Math.round(parseFloat(String(v)) || 0));
    if (a.pending && Math.abs(view.value) < 0.5) out.push(L.pending);
    else if (view.value < 0) out.push(fmtTemplate(L.cost_more, { total: f0.format(-view.value), unit }));
    else out.push(fmtTemplate(L.saved, { total: f0.format(view.value), unit, capacity: money(a.capacity_savings), energy: money(a.energy_savings) }));
  }
  if (a.capacity_step && a.capacity_step_without) {
    out.push(a.capacity_step === a.capacity_step_without
      ? fmtTemplate(L.step_same, { step: a.capacity_step })
      : fmtTemplate(L.step_without, { step: a.capacity_step, without: a.capacity_step_without }));
  }
  if (a.price_paid != null && a.price_reference != null) {
    out.push(fmtTemplate(L.price, { paid: f2.format(a.price_paid), reference: f2.format(a.price_reference), unit }));
  }
  const d = dev?.attributes ?? {};
  if ((toNum(dev?.state) ?? 0) > 0) {
    const parts: string[] = [];
    const missed = sumOf(d.deadlines_missed);
    if (missed > 0) parts.push(missed === 1 ? L.missed_one : fmtTemplate(L.missed, { n: missed }));
    const worst = Object.entries((d.comfort_min ?? {}) as Record<string, number>).sort((x, y) => y[1] - x[1])[0];
    if (worst && worst[1] > 0) {
      const h = Math.floor(worst[1] / 60), m = Math.round(worst[1] % 60);
      const time = h === 0 ? fmtTemplate(L.minutes, { m }) : m === 0 ? fmtTemplate(L.hours, { h }) : fmtTemplate(L.hours_minutes, { h, m });
      parts.push(fmtTemplate(L.comfort, { time, load: loads[worst[0]] ?? worst[0] }));
    }
    const over = Number(d.over_windows) || 0;
    if (over > 0) parts.push(over === 1 ? L.over_one : fmtTemplate(L.over, { n: over }));
    if (parts.length) out.push(parts.join(" · "));
  }
  return out;
}

/** Stable per-instance id for SVG defs (several cards can be on one page). */
let _uid = 0;
export const newUid = (p: string) => `${p}${++_uid}`;
