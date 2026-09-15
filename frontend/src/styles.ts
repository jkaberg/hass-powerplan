// The one style sheet every PowerPlan card starts from (D12 §5.11): the look
// of the design canvas, built only from HA's theme variables, so a card never
// invents its own sizes. The cards are plain custom elements, not Lit, so this
// is a string each card puts first in its shadow root's `<style>`.

export const ppStyles = `
  :host {
    --pp-pad: 16px;
    --pp-gap: 8px;
    --pp-radius-s: 8px;
    --pp-radius-m: 10px;
    --pp-fill: rgba(var(--rgb-primary-text-color, 225, 225, 225), 0.06);
    --pp-track: rgba(var(--rgb-primary-text-color, 225, 225, 225), 0.1);
    display: block;
    height: 100%;
    font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
    color: var(--primary-text-color);
    -webkit-font-smoothing: antialiased;
  }
  ha-card { height: 100%; box-sizing: border-box; }
  .pp-content { box-sizing: border-box; height: 100%; padding: 14px var(--pp-pad) var(--pp-pad); }
  .pp-sub     { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
  .pp-caption { margin-top: 12px; font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }

  .pp-list { display: flex; flex-direction: column; }
  .pp-row  { display: flex; align-items: center; gap: 12px; min-height: 44px; padding: 8px 0;
             box-sizing: border-box; border-bottom: 1px solid var(--divider-color); }
  .pp-row:last-child { border-bottom: 0; }
  .pp-dot  { flex: none; width: 10px; height: 10px; border-radius: 3px; }
  .pp-name { flex: 1 1 auto; min-width: 0; font-size: 14px; line-height: 20px;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pp-num  { flex: none; font-size: 14px; font-weight: 500; font-variant-numeric: tabular-nums;
             text-align: right; white-space: nowrap; }
  .pp-row.sum .pp-name, .pp-row.sum .pp-num { font-weight: 500; }

  table.pp-table { width: 100%; border-collapse: collapse; }
  .pp-table th { padding: 0 0 8px; font-size: 12px; font-weight: 500; text-align: left;
                 color: var(--secondary-text-color); border-bottom: 1px solid var(--divider-color); }
  .pp-table td { height: 44px; padding: 0; font-size: 14px; border-bottom: 1px solid var(--divider-color);
                 font-variant-numeric: tabular-nums; }
  .pp-table .num { text-align: right; padding-left: 16px; white-space: nowrap; }
  .pp-table tr.sum td { font-weight: 500; border-bottom: 0; }

  .pp-chip   { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 12px;
               border-radius: 14px; background: var(--secondary-background-color); font-size: 12px; }
  .pp-chip ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); }
  .pp-status { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px;
               border-radius: 12px; font-size: 12px; font-weight: 500; }
  .pp-status::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }

  .pp-stat-head  { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .pp-stat-name  { min-width: 0; font-size: 14px; font-weight: 500; line-height: 20px; color: var(--secondary-text-color);
                   white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pp-stat-head ha-icon { flex: none; --mdc-icon-size: 22px; color: var(--state-icon-color); }
  .pp-stat-value { font-size: 28px; font-weight: 400; letter-spacing: -0.3px; line-height: 36px;
                   font-variant-numeric: tabular-nums; white-space: nowrap; }
  .pp-stat-unit  { margin-left: 6px; font-size: 14px; letter-spacing: 0; color: var(--secondary-text-color); }

  .pp-legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 12px; line-height: 16px; }
  .pp-legend .item  { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap;
                      font: inherit; color: var(--primary-text-color); background: none; border: 0; padding: 0; cursor: pointer; }
  .pp-legend .item[aria-pressed="false"] { opacity: 0.4; }
  .pp-legend .value { color: var(--secondary-text-color); }
  .pp-swatch { flex: none; width: 10px; height: 10px; border-radius: 3px; }
  .pp-swatch.line { width: 14px; height: 0; border-radius: 0; border-top: 2px dashed currentColor; }
  .pp-swatch.strip { width: 14px; height: 10px; }
  .pp-toggle { display: inline-flex; gap: 2px; padding: 2px; border-radius: var(--pp-radius-m); background: var(--pp-fill); }
  .pp-toggle button { height: 28px; min-width: 44px; padding: 0 12px; border: 0; border-radius: var(--pp-radius-s);
                      background: transparent; color: var(--secondary-text-color);
                      font: 500 12px/28px var(--ha-font-family-body, Roboto, sans-serif); cursor: pointer; }
  .pp-toggle button[aria-pressed="true"] { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.22); color: var(--primary-color); }
  .pp-readout { display: flex; align-items: center; gap: 10px; min-height: 42px; padding: 4px 12px; box-sizing: border-box;
                border-radius: var(--pp-radius-m); background: var(--secondary-background-color); }
  .pp-readout .head { font-size: 12px; font-weight: 500; line-height: 16px; }
  .pp-readout .sub  { font-size: 11px; line-height: 16px; color: var(--secondary-text-color); }
  .pp-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
              min-height: 88px; font-size: 14px; color: var(--secondary-text-color); text-align: center; }
`;

/** A tooltip styled from the theme (G4, T5): ECharts cannot read CSS variables, so they are resolved first. */
export function tooltipStyle(css: (name: string, fallback: string) => string): Record<string, unknown> {
  return {
    backgroundColor: css("--card-background-color", "#1c1c1c"),
    borderColor: css("--divider-color", "rgba(225,225,225,.12)"),
    borderWidth: 1,
    padding: [10, 12],
    textStyle: { color: css("--primary-text-color", "#e1e1e1"), fontSize: 12, lineHeight: 20 },
    extraCssText: "border-radius:10px; box-shadow:0 6px 20px rgba(0,0,0,.45);",
  };
}
