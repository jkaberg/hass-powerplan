// `powerplan-runs-card` (D12 §5.9, N8): Now's next runs as a list, one row
// per appliance with planned energy, sorted by start - "nå" for a run already
// going, else the slot's start - with the planned kWh and cost under the
// name, a sum, a chip for each appliance running now, and the caption that the
// figures cover the whole plan. It replaces v0.4's markdown table, which HA
// draws bordered, content-wide and unstyled.

import { type HomeAssistant, moreInfo, timeZone } from "./ha";
import { ppStyles } from "./styles";
import { type ByLoad, moneyFormat, runRows } from "./transforms";

interface RunsConfig {
  entry_id: string;
  entities: { plan: string };
  loads: Array<{ id: string; name: string; color: string; status: string }>;
  /** `plan_status` states in which an appliance draws power now. */
  running?: string[];
  currency?: string;
  labels?: Record<string, string>;
}

const escape = (value: string) =>
  value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

const fill = (text: string, values: Record<string, string>) =>
  text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);

export class PowerplanRunsCard extends HTMLElement {
  private config?: RunsConfig;
  private hassRef?: HomeAssistant;
  private key: unknown[] = [];

  public setConfig(config: RunsConfig): void {
    if (!config?.entities?.plan) throw new Error("powerplan-runs-card needs entities.plan");
    this.config = config;
    this.key = [];
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    const config = this.config;
    if (!config) return;
    const key = [
      hass.states[config.entities.plan],
      ...config.loads.map((load) => hass.states[load.status]),
      hass.language,
      // "nå" turns true at a run's start with no new state: redraw each minute.
      Math.floor(Date.now() / 60_000),
    ];
    if (key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.render();
  }

  public getCardSize(): number {
    return 4;
  }

  public getGridOptions(): Record<string, number | string> {
    return { columns: 12, rows: "auto" };
  }

  private render(): void {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot!.addEventListener("click", (event) => {
        const row = (event.target as HTMLElement).closest<HTMLElement>("[data-entity]");
        if (row?.dataset.entity) moreInfo(this, row.dataset.entity);
      });
    }
    const labels = config.labels ?? {};
    const locale = hass.locale.language;
    const money = moneyFormat(locale, config.currency ?? "");
    const kwh = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: timeZone(hass) });
    const byLoad = hass.states[config.entities.plan]?.attributes.by_load as Record<string, ByLoad> | undefined;
    const { rows, kwh: total, cost } = runRows(byLoad, config.loads, Date.now());
    const status = new Map(config.loads.map((load) => [load.id, load.status]));
    const line = (energy: number, amount: number) =>
      `${kwh.format(energy)} kWh · ${fill(labels.slot_cost ?? "≈ {cost}", { cost: money.format(amount) })}`;
    const running = config.loads.filter((load) => (config.running ?? []).includes(hass.states[load.status]?.state ?? ""));
    const list = rows.length
      ? `<div class="pp-list">${rows
          .map(
            (row) => `<div class="pp-row run" data-entity="${escape(status.get(row.id) ?? "")}" role="button" tabindex="0">
              <span class="pp-dot" style="background:${escape(row.color)}"></span>
              <span class="text"><span class="pp-name">${escape(row.name)}</span><span class="pp-sub">${escape(line(row.kwh, row.cost))}</span></span>
              <span class="pp-num">${escape(row.start === null ? (labels.now_short ?? "") : clock.format(row.start))}</span></div>`,
          )
          .join("")}
          <div class="pp-row sum"><span class="pp-name">${escape(labels.total ?? "")}</span><span class="pp-num">${escape(line(total, cost))}</span></div>
        </div>`
      : `<div class="pp-empty">${escape(labels.no_runs ?? "")}</div>`;
    const chips = running.length
      ? `<div class="chips">${running
          .map((load) => `<span class="pp-chip"><ha-icon icon="mdi:flash"></ha-icon>${escape(fill(labels.running_now ?? "{name}", { name: load.name }))}</span>`)
          .join("")}</div>`
      : "";
    this.shadowRoot!.innerHTML = `
      <style>
        ${ppStyles}
        .run { cursor: pointer; }
        .text { display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0; }
        .text .pp-name { flex: none; }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
      </style>
      <ha-card><div class="pp-content">
        ${list}
        ${chips}
        ${rows.length ? `<div class="pp-caption">${escape(labels.plan_horizon ?? "")}</div>` : ""}
      </div></ha-card>`;
  }
}
