// Theme tokens and small shared parts for every PowerPlan card (iteration 5).
//
// Rule: sizes come from --ha-font-size-* / --ha-space-* (so they follow --ha-font-size-scale),
// text colours from --primary/secondary-text-color, fills from rgba(var(--rgb-…)) so light and
// dark themes both work. Appliance colours are only ever used as fills, never as text.
// Every token has a fallback equal to Home Assistant's default theme.
//
// Iteration 5 - contrast. The neutral data tokens were tuned on a light card and nearly vanished on a dark
// one (Holder temperaturen at 16 % was #3c3c3c on #1c1c1c, 1,5:1). Every mark that carries data now
// reaches ≥ 3:1 against the card in BOTH HA default themes (WCAG 1.4.11): the alpha is what it takes
// on the harder theme (dark needs ≥ .38, light ≥ .48). Structure (lane track, grid) stays quiet on purpose.
//   base  .50  → dark #7f7f7f 4,2:1 · light #909090 3,2:1        Annet forbruk
//   hold  outline .62 (5,8:1 · 4,6:1) over a .12 fill            Holder temperaturen (hollow = held, not moved)
//   hatch .50  → stripes #7f7f7f on the lane                      Senket i dyre timer
//   cheap .13 fill + a 3 px line in full success colour           Billige timer (the fill alone was 1,1:1; the line carries it)

import { esc } from "./r3-util";

export const TOKENS = `
  :host {
    --pp-font: var(--ha-font-family-body, Roboto, Noto, sans-serif);
    --pp-fs-s: var(--ha-font-size-s, 12px);
    --pp-fs-m: var(--ha-font-size-m, 14px);
    --pp-fs-l: var(--ha-font-size-l, 16px);
    --pp-fs-xl: var(--ha-font-size-xl, 20px);
    --pp-fs-3xl: var(--ha-font-size-3xl, 28px);
    --pp-fs-4xl: var(--ha-font-size-4xl, 32px);
    --pp-fs-5xl: var(--ha-font-size-5xl, 40px);
    --pp-fw-m: var(--ha-font-weight-medium, 500);
    --pp-pad: var(--ha-space-4, 16px);
    --pp-gap: var(--ha-space-2, 8px);
    --pp-radius: var(--ha-border-radius-lg, 12px);
    --pp-text: var(--primary-text-color, #141414);
    --pp-text2: var(--secondary-text-color, #5e5e5e);
    --pp-text3: var(--disabled-text-color, #bdbdbd);
    --pp-divider: var(--divider-color, rgba(0, 0, 0, 0.12));
    --pp-card: var(--ha-card-background, var(--card-background-color, #fff));
    --pp-rgb-text: var(--rgb-primary-text-color, 33, 33, 33);
    --pp-fill: rgba(var(--pp-rgb-text), 0.06);
    --pp-fill-hover: rgba(var(--pp-rgb-text), 0.09);
    --pp-track: rgba(var(--pp-rgb-text), 0.10);
    --pp-hatch: rgba(var(--pp-rgb-text), 0.50);
    --pp-base: rgba(var(--pp-rgb-text), 0.50);          /* Annet forbruk */
    --pp-hold: rgba(var(--pp-rgb-text), 0.12);          /* Holder temperaturen: fill … */
    --pp-hold-line: rgba(var(--pp-rgb-text), 0.62);     /* … and the outline that carries it */
    --pp-cheap: rgba(var(--rgb-success-color, 67, 160, 71), 0.13);
    --pp-cheap-line: var(--success-color, #43a047);
    --pp-sel-bg: var(--ha-color-fill-primary-normal-resting, rgba(var(--rgb-primary-color, 0, 154, 199), 0.2));
    --pp-sel-fg: var(--ha-color-on-primary-normal, var(--primary-color, #009ac7));
    --pp-primary: var(--primary-color, #009ac7);
    --pp-price-hi: rgba(var(--rgb-primary-color, 0, 154, 199), 0.5);
    --pp-price-lo: rgba(var(--rgb-primary-color, 0, 154, 199), 0.24);
    --pp-warn: var(--warning-color, #ffa600);
    --pp-warn-bg: rgba(var(--rgb-warning-color, 255, 166, 0), 0.12);
    --pp-ok: var(--success-color, #43a047);
    --pp-ok-bg: rgba(var(--rgb-success-color, 67, 160, 71), 0.12);
    --pp-ok-text: var(--ha-color-on-success-quiet, var(--pp-text));
    --pp-error: var(--error-color, #db4437);
    --pp-amber: var(--amber-color, #ffc107);
    --pp-focus: var(--ha-color-focus, var(--primary-color, #009ac7));
    --pp-anim: var(--ha-animation-duration-normal, 250ms);
    display: block;
    font-family: var(--pp-font);
    color: var(--pp-text);
    -webkit-font-smoothing: antialiased;
  }
`;

/** Shared component styles: segmented control, icon button, alert, skeleton, legend. */
export const SHARED = `
  .seg-group { display: inline-flex; gap: 2px; padding: 2px; border-radius: 12px; background: var(--pp-fill); flex: none; }
  .seg { min-width: 48px; height: 40px; padding: 0 14px; border: 0; border-radius: 10px; background: transparent; cursor: pointer;
         font: var(--pp-fw-m) var(--pp-fs-m) var(--pp-font); color: var(--pp-text2); }
  .seg:hover { background: var(--pp-fill-hover); }
  .seg[aria-pressed="true"] { background: var(--pp-sel-bg); color: var(--pp-sel-fg); }
  .ibtn { width: 48px; height: 48px; border: 0; border-radius: 50%; background: transparent; color: var(--pp-text2);
          display: inline-flex; align-items: center; justify-content: center; cursor: pointer; flex: none; --mdc-icon-size: 24px; padding: 0; }
  .ibtn:hover { background: var(--pp-fill-hover); }
  .tbtn { height: 40px; padding: 0 12px; border: 0; border-radius: 20px; background: transparent; color: var(--pp-primary); cursor: pointer;
          font: var(--pp-fw-m) var(--pp-fs-m) var(--pp-font); display: inline-flex; align-items: center; gap: 4px; flex: none; --mdc-icon-size: 18px; white-space: nowrap; }
  .tbtn:hover { background: rgba(var(--rgb-primary-color, 0, 154, 199), 0.08); }
  button:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--pp-focus); outline-offset: 2px; }
  .alert { display: flex; align-items: center; gap: 12px; padding: 8px 8px 8px 12px; border-radius: 8px; background: var(--pp-warn-bg);
           --mdc-icon-size: 24px; }
  .alert > ha-icon { color: var(--pp-warn); flex: none; }
  .alert.info { background: rgba(var(--rgb-primary-color, 0, 154, 199), 0.12); } .alert.info > ha-icon { color: var(--pp-primary); }
  .alert .at { display: flex; flex-direction: column; gap: 2px; flex: 1 1 auto; min-width: 0; font-size: var(--pp-fs-m); line-height: 20px; }
  .alert .at b { font-weight: var(--pp-fw-m); }
  .skel { background: var(--pp-fill); border-radius: 6px; position: relative; overflow: hidden; }
  .skel::after { content: ""; position: absolute; inset: 0; transform: translateX(-100%);
                 background: linear-gradient(90deg, transparent, rgba(var(--pp-rgb-text), 0.06), transparent);
                 animation: pp-shimmer calc(var(--pp-anim) * 6) infinite; }
  @keyframes pp-shimmer { to { transform: translateX(100%); } }
  @media (prefers-reduced-motion: reduce) { .skel::after { animation: none; } }
  .legend { display: inline-flex; align-items: center; gap: 6px; font-size: var(--pp-fs-s); color: var(--pp-text2); white-space: nowrap; }
  .sw { width: 16px; height: 10px; border-radius: 5px; display: inline-block; flex: none; box-sizing: border-box; }
  .sw.run { background: var(--pp-text2); }
  .sw.hold { background: var(--pp-hold); box-shadow: inset 0 0 0 1.5px var(--pp-hold-line); }
  .sw.low { background: repeating-linear-gradient(135deg, var(--pp-hatch) 0 2px, transparent 2px 5px); box-shadow: inset 0 0 0 1px var(--pp-hatch); }
  .sw.cheap { border-radius: 3px; background: var(--pp-cheap); box-shadow: inset 0 3px 0 var(--pp-cheap-line); }
  .t-s { font-size: var(--pp-fs-s); fill: var(--pp-text2); font-family: var(--pp-font); }
  .t-s.strong { fill: var(--pp-text); }
  .t-s.on { fill: var(--pp-text); font-weight: var(--pp-fw-m); }
`;

/** SVG hatch used for "lowered" (senket) and "manual". Ids are per card instance. */
export function hatchDef(id: string): string {
  return `<pattern id="${id}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">`
    + `<rect width="2" height="6" style="fill:var(--pp-hatch)"/></pattern>`;
}

export function iconButton(icon: string, label: string, act: string, extra = ""): string {
  return `<button type="button" class="ibtn" data-act="${esc(act)}" aria-label="${esc(label)}" title="${esc(label)}" ${extra}><ha-icon icon="${esc(icon)}"></ha-icon></button>`;
}

export function alertHtml(o: { title?: string; text: string; action?: { label: string; act: string }; kind?: "warning" | "info" }): string {
  const icon = o.kind === "info" ? "mdi:information-outline" : "mdi:alert-outline";
  return `<div class="alert ${o.kind ?? "warning"}" role="status"><ha-icon icon="${icon}"></ha-icon>
    <div class="at">${o.title ? `<b>${esc(o.title)}</b>` : ""}<span>${esc(o.text)}</span></div>
    ${o.action ? `<button type="button" class="tbtn" data-act="${esc(o.action.act)}">${esc(o.action.label)}</button>` : ""}</div>`;
}

/** Card-shaped placeholder that keeps the final size while data loads. */
export function skeletonRows(n: number, rowH: number, withRail: number): string {
  return Array.from({ length: n }, (_, i) =>
    `<div style="display:flex;align-items:center;gap:12px;height:${rowH}px;padding:0 var(--pp-pad);border-top:1px solid var(--pp-divider)">
      <span class="skel" style="width:36px;height:36px;border-radius:50%;flex:none"></span>
      <span style="display:flex;flex-direction:column;gap:6px;width:${Math.max(withRail - 80, 120)}px">
        <span class="skel" style="height:12px;width:${70 - (i % 3) * 12}%"></span><span class="skel" style="height:10px;width:45%"></span></span>
      <span class="skel" style="flex:1;height:14px;border-radius:7px"></span></div>`).join("");
}
