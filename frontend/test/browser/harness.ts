// The fit harness (D9 §5.16, D12 §5.20): a card from src/ mounted in a copy of HA's sections view,
// themed with HA's default theme, fed the reference house, and checked by geometry, never by pixels.

import { history, NOW, statistics, states, ZONE } from "../fixtures/house";

/** Content widths that give each column count and each narrow edge (D12 §5.18, D-0696). */
export const WIDTHS = [320, 390, 768, 1024, 1184, 1664] as const;
export const THEMES = ["light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

/** HA's default theme, frontend 2026.9: the variables the cards read, light and dark. */
const COMMON: Record<string, string> = {
  "--primary-color": "#009ac7", "--rgb-primary-color": "0, 154, 199",
  "--success-color": "#43a047", "--rgb-success-color": "67, 160, 71",
  "--warning-color": "#ffa600", "--rgb-warning-color": "255, 166, 0",
  "--error-color": "#db4437", "--rgb-error-color": "219, 68, 55",
  "--amber-color": "#ffc107",
  "--energy-grid-consumption-color": "#488fc2", "--energy-grid-return-color": "#8353d1", "--energy-solar-color": "#ff9800",
  "--ha-font-family-body": "Roboto, Noto, sans-serif",
  "--ha-font-size-xs": "10px", "--ha-font-size-s": "12px", "--ha-font-size-m": "14px", "--ha-font-size-l": "16px",
  "--ha-font-size-xl": "20px", "--ha-font-size-2xl": "24px", "--ha-font-size-3xl": "28px", "--ha-font-size-4xl": "32px", "--ha-font-size-5xl": "40px",
  "--ha-font-weight-medium": "500", "--ha-space-2": "8px", "--ha-space-4": "16px", "--ha-border-radius-lg": "12px",
};
const THEME: Record<Theme, Record<string, string>> = {
  light: {
    "--primary-background-color": "#fafafa", "--card-background-color": "#ffffff", "--ha-card-background": "#ffffff",
    "--primary-text-color": "#141414", "--rgb-primary-text-color": "20, 20, 20", "--secondary-text-color": "#5e5e5e",
    "--disabled-text-color": "#bdbdbd", "--divider-color": "rgba(0, 0, 0, 0.12)",
  },
  dark: {
    "--primary-background-color": "#111111", "--card-background-color": "#1c1c1c", "--ha-card-background": "#1c1c1c",
    "--primary-text-color": "#e1e1e1", "--rgb-primary-text-color": "225, 225, 225", "--secondary-text-color": "#9b9b9b",
    "--disabled-text-color": "#6f6f6f", "--divider-color": "rgba(225, 225, 225, 0.12)",
  },
};

/** `ha-card` and `ha-icon` as HA draws them, closely enough for layout. */
export function defineHaElements(): void {
  if (!customElements.get("ha-card")) {
    customElements.define("ha-card", class extends HTMLElement {
      connectedCallback() {
        if (this.shadowRoot) return;
        this.attachShadow({ mode: "open" }).innerHTML = `<style>:host{display:block;box-sizing:border-box;background:var(--ha-card-background,var(--card-background-color));
          border:1px solid var(--divider-color);border-radius:12px;color:var(--primary-text-color)}</style><slot></slot>`;
      }
    });
  }
  if (!customElements.get("ha-icon")) {
    customElements.define("ha-icon", class extends HTMLElement {
      connectedCallback() { this.style.display ||= "inline-block"; this.style.width ||= "var(--mdc-icon-size, 24px)"; this.style.height ||= "var(--mdc-icon-size, 24px)"; }
    });
  }
}

/** The sections view's column and a span's width for a content width `w` (D12 §5.18). */
export function sectionWidth(w: number, span: number): number {
  const gap = w < 600 ? 8 : 32, pad = w < 600 ? 8 : 16;
  const inner = w - 2 * pad;
  const n = Math.max(1, Math.min(3, Math.floor((inner + gap) / (320 + gap))));
  const col = Math.min(500, (inner - (n - 1) * gap) / n);
  const k = Math.min(span, n);
  return Math.floor(k * col + (k - 1) * gap);
}

const MONTH = { start: new Date("2026-08-31T22:00:00Z"), end: new Date("2026-09-30T22:00:00Z") };

export interface Hass {
  states: ReturnType<typeof states>;
  language: string; locale: { language: string; time_zone: string; number_format: string };
  config: { time_zone: string; currency: string }; themes: { darkMode: boolean }; user: { is_admin: boolean };
  connection: Record<string, unknown>;
  callWS<T>(msg: Record<string, unknown>): Promise<T>;
  callService(): Promise<unknown>;
  localize(key: string): string;
}

export function makeHass(theme: Theme, language = "nb"): Hass {
  return {
    states: states(), language, locale: { language, time_zone: "server", number_format: "language" },
    config: { time_zone: ZONE, currency: "NOK" }, themes: { darkMode: theme === "dark" }, user: { is_admin: true },
    // The History picker's collection (`_energy_powerplan`), set to the month so far.
    connection: { _energy_powerplan: { start: MONTH.start, end: MONTH.end, subscribe: (cb: (d: typeof MONTH) => void) => { cb(MONTH); return () => undefined; } } },
    async callWS<T>(msg: Record<string, unknown>): Promise<T> {
      switch (msg.type) {
        case "recorder/statistics_during_period": return statistics(msg as never) as T;
        case "history/history_during_period": return history(msg as never) as T;
        case "repairs/list_issues": return { issues: [] } as T;
        case "lovelace/resources": return [] as T;
        default: throw new Error(`unexpected ${String(msg.type)}`);
      }
    },
    async callService() { return undefined; },
    localize: (key: string) => key,
  };
}

export interface Mounted { card: HTMLElement; frame: HTMLElement; errors: string[] }

/** Mount `tag` with `config` in a section `span` columns wide at content width `w`, `rows` 56 px grid rows or auto. */
export async function mount(tag: string, config: Record<string, unknown>, w: number, theme: Theme, span = 1, rows?: number): Promise<Mounted> {
  defineHaElements();
  document.body.innerHTML = "";
  document.body.style.margin = "0";
  const view = document.createElement("div");
  for (const [k, v] of Object.entries({ ...COMMON, ...THEME[theme] })) view.style.setProperty(k, v);
  view.style.cssText += `;background:var(--primary-background-color);width:${w}px;padding:16px 0;font-family:Roboto,Noto,sans-serif;color:var(--primary-text-color)`;
  const frame = document.createElement("div");
  frame.style.cssText = `width:${sectionWidth(w, span)}px;margin:0 auto;${rows ? `height:${rows * 56 + (rows - 1) * 8}px;` : ""}display:grid;grid-template-columns:minmax(0,1fr)`;
  view.appendChild(frame);
  document.body.appendChild(view);
  const errors: string[] = [];
  // HA's own frontend ignores this browser notice; it is not an error of the card.
  const benign = (text: string) => text.includes("ResizeObserver loop");
  const onError = (e: ErrorEvent) => { if (!benign(String(e.message))) errors.push(String(e.message)); };
  addEventListener("error", onError);
  const origError = console.error;
  console.error = (...args: unknown[]) => { const text = args.map(String).join(" "); if (!benign(text)) errors.push(text); };
  try {
    const card = document.createElement(tag) as HTMLElement & { setConfig(c: unknown): void; hass: unknown };
    card.setConfig({ type: `custom:${tag}`, ...config });
    frame.appendChild(card);
    card.hass = makeHass(theme);
    await settle(card);
    card.hass = makeHass(theme);
    await settle(card);
    return { card, frame, errors };
  } finally {
    console.error = origError;
    removeEventListener("error", onError);
  }
}

/** Let a card fetch, observe its size and draw: frames and microtasks until its markup stops changing. */
async function settle(card: HTMLElement): Promise<void> {
  let last = "";
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 25)));
    const now = card.shadowRoot?.innerHTML ?? "";
    if (now && now === last) return;
    last = now;
  }
}

function* walk(root: ParentNode): Generator<Element> {
  for (const el of Array.from(root.querySelectorAll("*"))) {
    yield el;
    if (el.shadowRoot) yield* walk(el.shadowRoot);
  }
}

function visible(el: Element): boolean {
  if (el.closest(".pp-tip, [hidden], defs, pattern, title, style, clipPath")) return false;
  const cs = getComputedStyle(el);
  if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) === 0) return false;
  if (el.getAttribute("fill") === "transparent" || el.getAttribute("style")?.includes("fill:transparent")) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

const luminance = (rgb: number[]) => {
  const c = rgb.map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!;
};
const rgbOf = (css: string) => (css.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
export const contrast = (a: string, b: string) => {
  const [x, y] = [luminance(rgbOf(a)), luminance(rgbOf(b))].sort((p, q) => q - p);
  return (x! + 0.05) / (y! + 0.05);
};

/** Every geometric promise of D9 §5.16; returns the breaches, empty when the card fits. */
export function breaches(m: Mounted): string[] {
  const out = [...m.errors.map((e) => `console: ${e}`)];
  const root = m.card.shadowRoot;
  if (!root) return [...out, "no shadow root"];
  const box = m.card.getBoundingClientRect();
  if (box.width > m.frame.getBoundingClientRect().width + 1) out.push(`card ${box.width} wider than its section`);
  const label = (el: Element) => `${el.tagName.toLowerCase()}${el.getAttribute("class") ? "." + el.getAttribute("class")!.split(" ")[0] : ""}${el.textContent?.trim() ? ` "${el.textContent.trim().slice(0, 24)}"` : ""}`;
  for (const el of walk(root)) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1) {
      out.push(`outside the card: ${label(el)} [${Math.round(r.left - box.left)}, ${Math.round(r.right - box.left)}] of ${Math.round(box.width)} × [${Math.round(r.top - box.top)}, ${Math.round(r.bottom - box.top)}] of ${Math.round(box.height)}`);
    }
    if (el instanceof HTMLElement && !(el.tagName === "HA-CARD")) {
      const cs = getComputedStyle(el);
      const clips = cs.overflowX === "hidden" || cs.overflowX === "clip";
      if (clips && cs.textOverflow !== "ellipsis" && el.scrollWidth > el.clientWidth + 1 && el.textContent?.trim()) {
        out.push(`clipped text: ${label(el)} ${el.scrollWidth} > ${el.clientWidth}`);
      }
    }
    if (el.matches("button, .pp-mark")) {
      if (r.width < 23.5 || r.height < 23.5) out.push(`target under 24 px: ${label(el)} ${Math.round(r.width)} × ${Math.round(r.height)}`);
    }
    if (el.matches(".pp-mark-dot")) {
      const card = getComputedStyle(root.querySelector("ha-card") ?? m.card).backgroundColor;
      const ratio = contrast(getComputedStyle(el).fill, card);
      if (ratio < 3) out.push(`mark at ${ratio.toFixed(2)} : 1`);
    }
  }
  return [...new Set(out)];
}

export { NOW };
