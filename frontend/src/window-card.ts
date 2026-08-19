// `powerplan-window-card` (D12 §5.3): the capacity window now - used kWh
// against the ceiling, a needle at the projection, coloured by the ladder
// stage - as a semicircle in the style of the Energy dashboard's gauges.
// Tapping opens the window's more-info dialog.

import { cssVar, type HomeAssistant, moreInfo, numeric, timeZone } from "./ha";
import { arcPoint, gauge, stageTone, TONE_COLOR } from "./transforms";

interface WindowConfig {
  entry_id: string;
  entities: Partial<
    Record<
      | "window_used"
      | "window_projected"
      | "ceiling"
      | "allowance"
      | "stage"
      | "peak_warning"
      | "next_peak_warning",
      string
    >
  >;
  labels?: Record<string, string>;
}

const RADIUS = 40;

function arc(from: number, to: number): string {
  const [x0, y0] = arcPoint(from, RADIUS);
  const [x1, y1] = arcPoint(to, RADIUS);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${RADIUS} ${RADIUS} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

const escape = (value: string) =>
  value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

export class PowerplanWindowCard extends HTMLElement {
  private config?: WindowConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];

  public setConfig(config: WindowConfig): void {
    if (!config?.entities?.window_used) {
      throw new Error("powerplan-window-card needs entities.window_used");
    }
    this.config = config;
    this.key = [];
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    const config = this.config;
    if (!config) return;
    const key = [
      ...Object.values(config.entities).map((id) => (id ? hass.states[id] : undefined)),
      hass.language,
    ];
    if (key.length === this.key.length && key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.render();
  }

  public getCardSize(): number {
    return 4;
  }

  public getGridOptions(): Record<string, number> {
    return { columns: 6, rows: 4, min_rows: 3, min_columns: 4 };
  }

  private render(): void {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.addEventListener("click", () => {
        const id = this.config?.entities.window_used;
        if (id) moreInfo(this, id);
      });
    }
    const labels = config.labels ?? {};
    const state = (key: keyof WindowConfig["entities"]) => {
      const id = config.entities[key];
      return id ? hass.states[id] : undefined;
    };
    const usedEntity = state("window_used");
    const used = numeric(usedEntity) ?? 0;
    const projected = numeric(state("window_projected"));
    const ceiling = numeric(state("ceiling"));
    const tone = stageTone(numeric(state("stage")));
    const scale = gauge(used, projected, ceiling);
    const color = scale.over ? TONE_COLOR.alert : TONE_COLOR[tone];
    const locale = hass.locale.language;
    const kwh = new Intl.NumberFormat(locale, { maximumFractionDigits: 1, minimumFractionDigits: 1 });
    const clock = new Intl.DateTimeFormat(locale, {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: timeZone(hass),
    });

    const value =
      ceiling !== null && ceiling > 0
        ? `${kwh.format(used)} ${labels.of ?? "/"} ${kwh.format(ceiling)} kWh`
        : `${kwh.format(used)} kWh`;
    const lines: string[] = [];
    if (projected !== null) lines.push(`${labels.expected ?? ""} ${kwh.format(projected)} kWh`);
    const remaining = Number(usedEntity?.attributes.t_rem_min);
    if (Number.isFinite(remaining)) {
      lines.push((labels.min_left ?? "{minutes}").replace("{minutes}", String(Math.round(remaining))));
    }
    if (ceiling === null) {
      const allowance = numeric(state("allowance"));
      const unit = state("allowance")?.attributes.unit_of_measurement ?? "W";
      if (allowance !== null) lines.push(`${labels.allowance ?? ""} ${kwh.format(allowance)} ${unit}`);
    }
    const next = state("next_peak_warning")?.state;
    if (state("peak_warning")?.state === "on" && next && !Number.isNaN(Date.parse(next))) {
      lines.push((labels.peak_at ?? "{time}").replace("{time}", clock.format(Date.parse(next))));
    }

    const track = cssVar(this, "--primary-background-color", "#fafafa");
    const needle =
      scale.needle === null
        ? ""
        : (() => {
            const [x0, y0] = arcPoint(scale.needle, RADIUS - 9);
            const [x1, y1] = arcPoint(scale.needle, RADIUS + 5);
            return `<line class="needle" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"/>`;
          })();
    this.shadowRoot!.innerHTML = `
      <style>
        :host { display: block; height: 100%; cursor: pointer; }
        ha-card { height: 100%; box-sizing: border-box; padding: 12px 16px 16px;
                  display: flex; flex-direction: column; align-items: center; justify-content: center; }
        svg { width: 100%; max-width: 240px; overflow: visible; }
        .track { fill: none; stroke: ${track}; stroke-width: 8; filter: brightness(0.9); }
        .value { fill: none; stroke: ${color}; stroke-width: 8; }
        .needle { stroke: var(--primary-text-color); stroke-width: 2.5; stroke-linecap: round; }
        .reading { font-size: 1.4em; font-weight: 500; color: var(--primary-text-color); margin-top: 4px; }
        .sub { color: var(--secondary-text-color); font-size: 0.9em; text-align: center; line-height: 1.5; }
      </style>
      <ha-card>
        <svg viewBox="-50 -50 100 55" role="img" aria-label="${escape(value)}">
          <path class="track" d="${arc(0, 1)}"/>
          ${scale.used > 0 ? `<path class="value" d="${arc(0, scale.used)}"/>` : ""}
          ${needle}
        </svg>
        <div class="reading">${escape(value)}</div>
        <div class="sub">${lines.map(escape).join("<br>")}</div>
      </ha-card>`;
  }
}
