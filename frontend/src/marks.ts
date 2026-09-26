// The PowerPlan mark, the ring and the chart tooltip every iteration-6 chart shares (D12 §5.20).
//
// The mark says "PowerPlan acted here": a 7 px dot in the primary colour, ringed in the card's colour,
// with a 24 × 24 px hit area (WCAG 2.5.8). It carries no text; its tooltip does. A span PowerPlan held
// back keeps the lanes' hatch instead (`hatchDef` in tokens.ts).

import { esc } from "./r3-util";

/** The mark's radius and ring, in px (D12 §5.20). */
export const MARK_R = 3.5;
const MARK_RING = 1.75;
/** Half the smallest hit target, in px. */
export const HIT = 12;

/**
 * A `data-tip` attribute: lines joined by "\n", the first one drawn bold. `focus: false` for a dense cell
 * (a carpet's 720 hours) that would otherwise be a tab stop each; its figures are in the chart's label.
 */
export function tipAttr(lines: readonly string[], focus = true): string {
  return ` data-tip="${esc(lines.filter(Boolean).join("\n"))}"${focus ? ' tabindex="0"' : ""}`;
}

/** The PowerPlan mark at (cx, cy), explained by `lines`. `r` shrinks it inside a small cell. */
export function mark(cx: number, cy: number, lines: readonly string[], r = MARK_R): string {
  const x = cx.toFixed(1), y = cy.toFixed(1);
  return `<g class="pp-mark"${tipAttr(lines)} role="img" aria-label="${esc(lines.join(". "))}">`
    + `<circle cx="${x}" cy="${y}" r="${HIT}" fill="transparent"/>`
    + `<circle cx="${x}" cy="${y}" r="${(r + MARK_RING).toFixed(2)}" style="fill:var(--pp-card, var(--card-background-color, #fff))"/>`
    + `<circle class="pp-mark-dot" cx="${x}" cy="${y}" r="${r}" style="fill:var(--pp-mark)"/></g>`;
}

/** The mark without its own tooltip, where the chart's own hover already explains the spot (V3). */
export function markDot(cx: number, cy: number, r = MARK_R): string {
  const x = cx.toFixed(1), y = cy.toFixed(1);
  return `<circle cx="${x}" cy="${y}" r="${(r + MARK_RING).toFixed(2)}" style="fill:var(--pp-card, var(--card-background-color, #fff))"/>`
    + `<circle class="pp-mark-dot" cx="${x}" cy="${y}" r="${r}" style="fill:var(--pp-mark)"/>`;
}

/** The mark as a legend swatch or before a line of text. */
export const markSwatch = `<i class="pp-mark-sw" aria-hidden="true"></i>`;

export interface Slice {
  value: number;
  color: string;
  lines: string[];
  /** Energy PowerPlan moved, drawn as an arc outside the slice from its start (V5). */
  moved?: number;
  movedLines?: string[];
}

/**
 * A ring of `slices`, 2 px gaps, `center` as two lines. With any `moved`, a 3 px arc in the mark's
 * colour runs outside each slice for its moved share, and the ring shrinks to leave room.
 */
export function ring(size: number, thick: number, slices: readonly Slice[], center: [string, string], label: string): string {
  const total = slices.reduce((sum, s) => sum + Math.max(0, s.value), 0);
  const outer = slices.some((s) => (s.moved ?? 0) > 0);
  const c = size / 2, r = size / 2 - thick / 2 - (outer ? 7 : 0);
  const circ = 2 * Math.PI * r, gap = slices.filter((s) => s.value > 0).length > 1 ? 2 : 0;
  let at = 0, out = "";
  for (const s of slices) {
    if (!(s.value > 0) || total <= 0) continue;
    const len = (s.value / total) * circ, dash = Math.max(len - gap, 0.6);
    out += `<circle class="pp-slice" cx="${c}" cy="${c}" r="${r.toFixed(2)}" fill="none" stroke-dasharray="${dash.toFixed(2)} ${(circ - dash).toFixed(2)}" stroke-dashoffset="${(-at).toFixed(2)}" transform="rotate(-90 ${c} ${c})" style="stroke:${s.color};stroke-width:${thick}"${tipAttr(s.lines)}/>`;
    if (outer && (s.moved ?? 0) > 0) {
      const r2 = r + thick / 2 + 5, circ2 = 2 * Math.PI * r2, len2 = (Math.min(s.moved!, s.value) / total) * circ2;
      out += `<circle class="pp-arc" cx="${c}" cy="${c}" r="${r2.toFixed(2)}" fill="none" stroke-dasharray="${Math.max(len2 - 1, 0.6).toFixed(2)} ${circ2.toFixed(2)}" stroke-dashoffset="${(-(at / circ) * circ2).toFixed(2)}" transform="rotate(-90 ${c} ${c})" style="stroke:var(--pp-mark);stroke-width:3"${tipAttr(s.movedLines ?? [])}/>`;
    }
    at += len;
  }
  out += `<text x="${c}" y="${c + 3}" text-anchor="middle" class="pp-ring-v">${esc(center[0])}</text>`;
  out += `<text x="${c}" y="${c + 21}" text-anchor="middle" class="pp-ring-u">${esc(center[1])}</text>`;
  return `<svg class="pp-ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="${esc(label)}">${out}</svg>`;
}

/** Styles for the mark, the ring and the tooltip; every card that uses them adds this once. */
export const MARK_CSS = `
  :host { --pp-mark: var(--primary-color, #009ac7); }
  .pp-mark { cursor: pointer; outline: none; }
  .pp-mark:focus-visible .pp-mark-dot { stroke: var(--pp-focus, var(--primary-color)); stroke-width: 2; }
  .pp-mark-sw { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--pp-mark);
                box-shadow: 0 0 0 1.75px var(--pp-card, var(--card-background-color, #fff)); flex: none; }
  [data-tip] { outline: none; }
  [data-tip]:focus-visible { outline: 2px solid var(--pp-focus, var(--primary-color)); outline-offset: 1px; }
  .pp-ring { display: block; flex: none; overflow: visible; }
  .pp-ring-v { font-size: var(--ha-font-size-xl, 20px); fill: var(--primary-text-color, #141414); font-variant-numeric: tabular-nums; }
  .pp-ring-u { font-size: var(--ha-font-size-s, 12px); fill: var(--secondary-text-color, #5e5e5e); }
  .pp-tip { position: absolute; z-index: 2; max-width: 240px; box-sizing: border-box; padding: 10px 12px; border-radius: 10px;
            background: var(--card-background-color, #fff); color: var(--primary-text-color, #141414);
            border: 1px solid var(--divider-color, rgba(0,0,0,.12)); box-shadow: 0 6px 20px rgba(0,0,0,.3);
            font-size: var(--ha-font-size-s, 12px); line-height: 20px; pointer-events: none; white-space: pre-line; }
  .pp-tip b { font-weight: var(--ha-font-weight-medium, 500); }
`;

/**
 * One tooltip per card for every `[data-tip]` in its shadow root: hover with a fine pointer, focus with
 * the keyboard, a tap pins it and a tap elsewhere or a scroll clears it. The tooltip is placed inside
 * `host` (positioned) and kept within it.
 */
export class ChartTip {
  private el?: HTMLElement;
  private pinned: Element | null = null;

  constructor(root: ShadowRoot, private readonly host: () => HTMLElement | null) {
    root.addEventListener("pointerover", (e) => {
      const ev = e as PointerEvent;
      if (ev.pointerType !== "mouse" || this.pinned) return;
      const t = this.target(ev.target);
      if (t) this.show(t);
    });
    root.addEventListener("pointerout", (e) => {
      if ((e as PointerEvent).pointerType === "mouse" && !this.pinned) this.hide();
    });
    root.addEventListener("click", (e) => {
      const t = this.target(e.target);
      if (!t) { this.pinned = null; this.hide(); return; }
      this.pinned = this.pinned === t ? null : t;
      if (this.pinned) this.show(t); else this.hide();
    });
    root.addEventListener("focusin", (e) => { const t = this.target(e.target); if (t) this.show(t); });
    root.addEventListener("focusout", () => { if (!this.pinned) this.hide(); });
    addEventListener("scroll", () => { if (this.pinned) { this.pinned = null; this.hide(); } }, { passive: true });
  }

  private target(node: EventTarget | null): Element | null {
    return node instanceof Element ? node.closest("[data-tip]") : null;
  }

  /** Called after each render: the old tooltip element went with the old markup. */
  public reset(): void {
    this.el = undefined;
    this.pinned = null;
  }

  private show(target: Element): void {
    const host = this.host();
    if (!host) return;
    const text = target.getAttribute("data-tip") ?? "";
    if (!text) return;
    if (!this.el || !this.el.isConnected) {
      this.el = document.createElement("div");
      this.el.className = "pp-tip";
      this.el.setAttribute("role", "tooltip");
      host.appendChild(this.el);
    }
    const [first, ...rest] = text.split("\n");
    this.el.innerHTML = `<b>${esc(first)}</b>${rest.length ? "\n" + rest.map(esc).join("\n") : ""}`;
    this.el.hidden = false;
    const box = host.getBoundingClientRect(), at = target.getBoundingClientRect();
    const w = this.el.offsetWidth, h = this.el.offsetHeight;
    let left = at.left - box.left + at.width / 2 - w / 2;
    left = Math.max(8, Math.min(left, box.width - w - 8));
    let top = at.top - box.top - h - 8;
    if (top < 4) top = at.bottom - box.top + 8;
    this.el.style.left = `${left}px`;
    this.el.style.top = `${Math.max(4, Math.min(top, box.height - h - 4))}px`;
  }

  private hide(): void {
    if (this.el) this.el.hidden = true;
  }
}
