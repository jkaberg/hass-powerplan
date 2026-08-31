// `powerplan-appliance-dialog` (D12 §5.12 R7): opened by tapping a row of the
// appliances card - the status and comfort, the controls, the next 24 h, why
// the plan is what it is, and the month's money, without leaving Now.
//
// It opens through HA's dialog manager (`show-dialog`), which gives one dialog
// at a time, `hass` pushed in, and the back gesture closing it, and draws a
// native `<dialog>` in HA's tokens: HA's own dialog elements changed their tags
// during 2025–2026, a native one did not. The controls are HA's own tile and
// entity row (`loadCardHelpers`), so they behave as everywhere else; the plan
// is the timeline card in single-load mode.

import type { ApplianceLoad, AppliancesConfig } from "./appliances-card";
import { escape, fill, toNumber } from "./appliances-card";
import { type HomeAssistant, moreInfo, timeZone } from "./ha";
import { rawStatus } from "./status";
import { type ByLoad, holdRuns, moneyFormat, type PlanSlot, planRuns, readable, withAlpha } from "./transforms";

const TAG = "powerplan-appliance-dialog";

interface Params {
  load: ApplianceLoad;
  config: AppliancesConfig;
}

interface Hosted extends HTMLElement {
  hass?: HomeAssistant;
  setConfig?(config: unknown): void;
}

export function openApplianceDialog(host: HTMLElement, hass: HomeAssistant, load: ApplianceLoad, config: AppliancesConfig): void {
  if (!customElements.get(TAG)) customElements.define(TAG, PowerplanApplianceDialog);
  const params: Params = { load, config };
  host.dispatchEvent(
    new CustomEvent("show-dialog", {
      bubbles: true,
      composed: true,
      detail: { dialogTag: TAG, dialogImport: () => Promise.resolve(), dialogParams: params },
    }),
  );
  // No dialog manager caught it (a card preview in the editor): open one ourselves.
  setTimeout(() => {
    const ha = document.querySelector("home-assistant");
    if (document.querySelector(TAG) || ha?.shadowRoot?.querySelector(TAG)) return;
    const dialog = document.createElement(TAG) as PowerplanApplianceDialog;
    dialog.standalone = true;
    document.body.appendChild(dialog);
    dialog.hass = hass;
    void dialog.showDialog(params);
  }, 250);
}

function navigate(path: string): void {
  history.pushState(null, "", path);
  window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
}

export class PowerplanApplianceDialog extends HTMLElement {
  public standalone = false;
  private hassRef?: HomeAssistant;
  private params?: Params;
  private dialog?: HTMLDialogElement;
  private hosted: Hosted[] = [];
  private key: unknown[] = [];
  private poll?: number;

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    for (const el of this.hosted) el.hass = hass;
    if (!this.params) return;
    const { load, config } = this.params;
    const ids = [load.status, load.control, load.ready_by, load.cost_month, load.savings_month, load.energy, load.next_legionella, config.entities.plan];
    const key = [...ids.map((id) => (id ? hass.states[id] : undefined)), Math.floor(Date.now() / 60_000)];
    if (key.every((part, i) => part === this.key[i])) return;
    this.key = key;
    this.renderDynamic();
  }

  public get hass(): HomeAssistant | undefined {
    return this.hassRef;
  }

  public async showDialog(params: Params): Promise<void> {
    this.params = params;
    this.key = [];
    this.renderShell();
    await this.mountBuiltins();
    if (this.hassRef) this.hass = this.hassRef;
    this.dialog!.showModal();
    if (this.standalone) {
      this.poll = window.setInterval(() => {
        const hass = (document.querySelector("home-assistant") as Hosted | null)?.hass;
        if (hass && hass !== this.hassRef) this.hass = hass;
      }, 1000);
    }
  }

  public closeDialog(): void {
    if (this.dialog?.open) this.dialog.close();
  }

  private onClosed(): void {
    if (this.poll) clearInterval(this.poll);
    this.params = undefined;
    this.hosted = [];
    this.dispatchEvent(new CustomEvent("dialog-closed", { bubbles: true, composed: true, detail: { dialog: TAG } }));
    if (this.standalone) this.remove();
  }

  private get labels(): Record<string, string> {
    return this.params?.config.labels ?? {};
  }

  // ---------------------------------------------------------------- the frame

  private renderShell(): void {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const { load } = this.params!;
    const labels = this.labels;
    const dark = Boolean(this.hassRef?.themes.darkMode);
    const icon = load.icon || "mdi:flash";
    this.shadowRoot!.innerHTML = `<style>${CSS}</style>
      <dialog aria-labelledby="t">
        <header>
          <button class="icon close-l" data-act="close" aria-label="${escape(labels.close)}"><ha-icon icon="mdi:close"></ha-icon></button>
          <span class="bubble" style="background:${withAlpha(load.color, 0.2)};color:${readable(load.color, dark)}"><ha-icon icon="${escape(icon)}"></ha-icon></span>
          <div class="ttl" tabindex="-1" autofocus><h2 id="t">${escape(load.name)}</h2><span>${escape([load.area, load.kind_name?.toLowerCase()].filter(Boolean).join(" · "))}</span></div>
          <div class="menu-wrap">
            <button class="icon" data-act="menu" aria-label="${escape(labels.more_options)}" aria-haspopup="menu"><ha-icon icon="mdi:dots-vertical"></ha-icon></button>
            <div class="menu" role="menu" hidden>
              <button role="menuitem" data-act="settings"><ha-icon icon="mdi:cog-outline"></ha-icon>${escape(labels.device_settings)}</button>
              <button role="menuitem" data-act="history"><ha-icon icon="mdi:chart-box-outline"></ha-icon>${escape(labels.history)}</button>
              ${load.path ? `<button role="menuitem" data-act="open"><ha-icon icon="mdi:open-in-new"></ha-icon>${escape(labels.open_details)}</button>` : ""}
            </div>
          </div>
          <button class="icon close-r" data-act="close" aria-label="${escape(labels.close)}"><ha-icon icon="mdi:close"></ha-icon></button>
        </header>
        <div class="body">
          <section class="hero" id="hero"></section>
          ${load.control || load.ready_by ? `<h3>${escape(labels.control)}</h3><div class="ctl" id="ctl"></div>` : ""}
          <h3>${escape(labels.dialog_plan)}</h3><div class="plan" id="plan"></div>
          <h3>${escape(labels.dialog_why)}</h3><div class="why" id="why"></div>
          ${load.cost_month || load.savings_month || load.energy ? `<h3>${escape(labels.dialog_month)}</h3><div class="stats" id="stats"></div>` : ""}
        </div>
        <footer><span id="leg"></span>
          ${load.path ? `<button class="text" data-act="open">${escape(labels.open_details)}<ha-icon icon="mdi:arrow-right"></ha-icon></button>` : ""}</footer>
      </dialog>`;
    const dialog = this.shadowRoot!.querySelector("dialog")!;
    this.dialog = dialog;
    dialog.addEventListener("close", () => this.onClosed());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        this.closeDialog(); // the backdrop
        return;
      }
      const button = (event.target as HTMLElement).closest<HTMLElement>("[data-act]");
      if (!button) return;
      const act = button.dataset.act;
      const menu = this.shadowRoot!.querySelector(".menu") as HTMLElement;
      if (act === "menu") {
        menu.hidden = !menu.hidden;
        return;
      }
      menu.hidden = true;
      if (act === "close") this.closeDialog();
      if (act === "settings") moreInfo(this, load.status);
      if (act === "history") {
        this.closeDialog();
        navigate(`/history?entity_id=${encodeURIComponent(load.status)}`);
      }
      if (act === "open" && load.path) {
        this.closeDialog();
        navigate(load.path);
      }
    });
  }

  /** HA's own tile and entity row for the controls; the timeline card for the plan. */
  private async mountBuiltins(): Promise<void> {
    const { load, config } = this.params!;
    const labels = this.labels;
    const root = this.shadowRoot!;
    const helpers = await (window as unknown as { loadCardHelpers?: () => Promise<{
      createCardElement(config: unknown): Hosted;
      createRowElement(config: unknown): Hosted;
    }> }).loadCardHelpers?.();
    const ctl = root.getElementById("ctl");
    if (ctl && helpers) {
      if (load.control) {
        const tile = helpers.createCardElement({
          type: "tile",
          entity: load.control,
          name: labels.control,
          vertical: false,
          features: [{ type: "select-options" }],
          features_position: "bottom",
        });
        const box = document.createElement("div");
        box.className = "tile";
        box.appendChild(tile);
        ctl.appendChild(box);
        this.hosted.push(tile);
      }
      if (load.ready_by) {
        const box = document.createElement("div");
        box.className = "rowbox";
        const row = helpers.createRowElement({ entity: load.ready_by, name: labels.ready_by, icon: "mdi:clock-check-outline" });
        box.appendChild(row);
        ctl.appendChild(box);
        this.hosted.push(row);
      }
    } else if (ctl) {
      ctl.innerHTML = [load.control, load.ready_by]
        .filter((id): id is string => Boolean(id))
        .map((id) => {
          const state = this.hassRef?.states[id];
          return `<div class="rowbox"><span class="k">${escape(state?.attributes.friendly_name ?? id)}</span><b>${escape(state?.state ?? "–")}</b></div>`;
        })
        .join("");
    }
    const plan = root.getElementById("plan");
    if (plan && config.entities.price_forecast) {
      const timeline = document.createElement("powerplan-timeline-card") as Hosted;
      timeline.setConfig?.({
        entry_id: config.entry_id,
        hours: 24,
        hours_options: [],
        loads: [{ id: load.id, name: load.name, color: load.color }],
        entities: { plan: config.entities.plan, price_forecast: config.entities.price_forecast, deadline: load.status },
        show: ["plan", "price"],
        currency: config.currency,
        labels: config.labels,
      });
      timeline.style.setProperty("--ha-card-border-width", "0");
      timeline.style.setProperty("--ha-card-box-shadow", "none");
      timeline.style.setProperty("--ha-card-background", "transparent");
      plan.appendChild(timeline);
      this.hosted.push(timeline);
    }
    if (this.hassRef) for (const el of this.hosted) el.hass = this.hassRef;
  }

  // ---------------------------------------------------------------- the live part

  private renderDynamic(): void {
    const hass = this.hassRef;
    const params = this.params;
    const root = this.shadowRoot;
    if (!hass || !params || !root) return;
    const { load, config } = params;
    const labels = this.labels;
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits: 1 });
    const two = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: zone });
    const money = moneyFormat(locale, config.currency ?? "");
    const status = hass.states[load.status];
    const a = status?.attributes ?? {};
    const planAttrs = hass.states[config.entities.plan]?.attributes ?? {};
    const now = Date.now();
    const runs = planRuns((planAttrs.slots as PlanSlot[] | undefined) ?? [], load.id).filter((run) => run.end > now);
    const planSlots = (planAttrs.slots as PlanSlot[] | undefined) ?? [];
    const holdNow = holdRuns(planSlots, load.id).some((run) => run.start <= now && run.end > now);
    const view = rawStatus(status?.state, a, runs.some((run) => run.start <= now), runs.length > 0, labels, holdNow);
    const row = (planAttrs.by_load as Record<string, ByLoad> | undefined)?.[load.id];
    const kwh = toNumber(row?.planned_kwh ?? a.planned_kwh);
    const cost = toNumber(String(row?.cost ?? a.cost ?? "").split(" ")[0]);
    const due = String(a.deadline_time || (load.ready_by ? (hass.states[load.ready_by]?.state ?? "").slice(0, 5) : "") || "");

    // The hero: status, the temperature against its target, what is still needed.
    const current = toNumber(a.current);
    const target = toNumber(a.target);
    const floor = toNumber(a.floor);
    const power = toNumber(a.granted_power);
    const pill = view.label
      ? `<span class="pill ${view.kind}"><i></i>${escape(view.label)}${view.kind === "running" && power ? ` · ${one.format(power / 1000)} kW` : ""}</span>`
      : "";
    const right = [
      runs.length ? fill(labels.done_at ?? "{time}", { time: clock.format(runs[runs.length - 1]!.end) }) : "",
      due ? fill(labels.due ?? "{time}", { time: due }) : "",
    ]
      .filter(Boolean)
      .join(" · ");
    let big: string;
    let bar = "";
    if (current !== null && target !== null) {
      const goal = fill(labels.target_short ?? "{v}", { v: `${one.format(target)} °C` });
      big = `<div class="big"><span>${one.format(current)} °C</span><small>${current < target - 0.05 ? "→ " : ""}${escape(goal)}</small></div>`;
      const lo = floor ?? Math.min(current, target) - 5;
      const f = Math.max(0, Math.min(1, (current - lo) / Math.max(target - lo, 0.1)));
      bar = `<div class="bar" style="--c:${load.color}"><div class="fill" style="width:${(f * 100).toFixed(1)}%"></div><div class="rest" style="left:${(f * 100).toFixed(1)}%"></div></div>
        <div class="scale"><span>${escape(fill(labels.min_value ?? "{v}", { v: `${one.format(lo)} °C` }))}</span>${kwh ? `<span>${escape(fill(labels.needs_kwh ?? "{kwh}", { kwh: two.format(kwh) }))}</span>` : ""}<span>${escape(fill(labels.target_value ?? "{v}", { v: `${one.format(target)} °C` }))}</span></div>`;
    } else {
      big = `<div class="big"><span>${kwh ? two.format(kwh) : "0"} kWh</span><small>${escape(labels.dialog_plan ?? "")}</small></div>`;
    }
    root.getElementById("hero")!.innerHTML = `<div class="hrow">${pill}<span class="muted">${escape(right)}</span></div>${big}${bar}`;

    // Why: the need, the chosen time, the coverage and the estimated cost.
    const chosen = runs.length
      ? runs.slice(0, 3).map((run) => `${clock.format(run.start)}–${clock.format(run.end)}`).join(", ")
      : (labels.no_run ?? "");
    const coverage = toNumber(a.coverage);
    const strategy = config.strategies?.[String(a.strategy ?? "")];
    const why: Array<[string, string | undefined, string]> = [
      [
        "mdi:timer-sand-complete",
        labels.need,
        kwh ? (due ? fill(labels.before ?? "{kwh}", { kwh: two.format(kwh), time: due }) : `${two.format(kwh)} kWh`) : "–",
      ],
      ["mdi:calendar-clock", labels.chosen, chosen],
      [
        "mdi:check-circle-outline",
        labels.coverage,
        `${Math.round((coverage ?? 1) * 100)} % · ${a.confidence === "known" ? (labels.prices_known ?? "") : (labels.prices_estimated ?? "")}`,
      ],
      ["mdi:cash", labels.est_cost, `${cost !== null ? `≈ ${money.format(cost)}` : "–"}${strategy ? ` · ${strategy}` : ""}`],
    ];
    root.getElementById("why")!.innerHTML = why
      .map(([icon, name, value]) => `<div class="wrow"><ha-icon icon="${icon}"></ha-icon><span class="k">${escape(name)}</span><span class="v">${escape(value)}</span></div>`)
      .join("");

    // This month: the appliance's own month-to-date sensors.
    const stats = root.getElementById("stats");
    if (stats) {
      const cell = (id: string | undefined, name: string | undefined, good = false) => {
        const state = id ? hass.states[id] : undefined;
        if (!state) return "";
        const n = toNumber(state.state);
        const unit = String(state.attributes.unit_of_measurement ?? "");
        const value = n === null ? escape(state.state) : unit === config.currency ? escape(money.format(n)) : `${two.format(n)} <small>${escape(unit)}</small>`;
        return `<div class="stat"><span>${escape(name)}</span><b class="${good && n !== null && n > 0 ? "good" : ""}">${value}</b></div>`;
      };
      stats.innerHTML = cell(load.cost_month, labels.cost) + cell(load.savings_month, labels.savings, true) + cell(load.energy, labels.energy);
    }

    // The footer: the next legionella run, for a water heater.
    const legionella = load.next_legionella ? hass.states[load.next_legionella] : undefined;
    const date = legionella ? Date.parse(legionella.state) : Number.NaN;
    root.getElementById("leg")!.innerHTML = Number.isNaN(date)
      ? ""
      : `<ha-icon icon="mdi:water-thermometer"></ha-icon>${escape(
          fill(labels.legionella_next ?? "{date}", {
            date: new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: zone }).format(date),
          }),
        )}`;
  }
}

const CSS = `
  dialog { width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 64px); padding: 0; border: 1px solid var(--divider-color);
           border-radius: var(--ha-dialog-border-radius, 28px); background: var(--ha-dialog-surface-background, var(--card-background-color, #1c1c1c));
           color: var(--primary-text-color); font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
           box-shadow: 0 24px 60px rgba(0,0,0,.6); overflow: hidden; display: none; flex-direction: column; }
  dialog[open] { display: flex; }
  dialog::backdrop { background: rgba(0,0,0,.55); }
  header { display: flex; align-items: center; gap: 12px; padding: 18px 12px 8px 24px; flex: none; }
  .bubble { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none; --mdc-icon-size: 22px; }
  .ttl { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; }
  .ttl h2 { margin: 0; font-size: 20px; font-weight: 400; line-height: 28px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ttl span { font-size: 13px; color: var(--secondary-text-color); }
  button.icon { width: 44px; height: 44px; border: 0; border-radius: 50%; background: transparent; color: var(--secondary-text-color);
                display: flex; align-items: center; justify-content: center; cursor: pointer; flex: none; }
  button.icon:hover { background: rgba(var(--rgb-primary-text-color,225,225,225), .06); }
  button.close-l { display: none; }
  .ttl:focus { outline: none; }
  .menu-wrap { position: relative; }
  .menu { position: absolute; right: 0; top: 46px; z-index: 2; min-width: 220px; padding: 6px 0; border-radius: 12px;
          background: var(--secondary-background-color, #282828); border: 1px solid var(--divider-color); box-shadow: 0 8px 24px rgba(0,0,0,.5); }
  .menu button { display: flex; align-items: center; gap: 12px; width: 100%; height: 44px; padding: 0 16px; border: 0; background: transparent;
                 color: var(--primary-text-color); font: 14px var(--ha-font-family-body, Roboto, sans-serif); cursor: pointer; --mdc-icon-size: 20px; }
  .menu button:hover { background: rgba(var(--rgb-primary-text-color,225,225,225), .06); }
  .body { padding: 4px 24px 16px; overflow-y: auto; flex: 1 1 auto; }
  h3 { margin: 18px 0 8px; font-size: 12px; font-weight: 500; letter-spacing: .4px; text-transform: uppercase; color: var(--secondary-text-color); }
  .hero { background: var(--secondary-background-color, #282828); border-radius: 12px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
  .hrow { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .muted { font-size: 12px; color: var(--secondary-text-color); text-align: right; }
  .big { display: flex; align-items: baseline; gap: 8px; }
  .big span { font-size: 32px; letter-spacing: -.5px; }
  .big small { font-size: 14px; color: var(--secondary-text-color); }
  .bar { position: relative; height: 8px; border-radius: 4px; background: rgba(var(--rgb-primary-text-color,225,225,225), .08); overflow: hidden; }
  .bar .fill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--c); border-radius: 4px; }
  .bar .rest { position: absolute; top: 0; bottom: 0; right: 0; background: repeating-linear-gradient(90deg, color-mix(in srgb, var(--c) 45%, transparent) 0 3px, transparent 3px 6px); }
  .scale { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); }
  .pill { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 9px 0 8px; border-radius: 11px; font-size: 12px; font-weight: 500; white-space: nowrap; }
  .pill i { width: 6px; height: 6px; border-radius: 50%; }
  .pill.running { background: rgba(67,160,71,.16); color: #7ccf80; } .pill.running i { background: var(--success-color, #43a047); }
  .pill.planned, .pill.waiting { background: rgba(var(--rgb-primary-color,0,154,199), .16); color: #5cc8ea; } .pill.planned i, .pill.waiting i { background: var(--primary-color); }
  .pill.paused { background: rgba(255,166,0,.16); color: #ffc15c; } .pill.paused i { background: var(--warning-color, #ffa600); }
  .pill.holding { background: rgba(67,160,71,.10); color: #9ad69d; } .pill.holding i { background: var(--success-color, #43a047); opacity: .55; }
  .pill.manual, .pill.unavailable, .pill.idle { background: rgba(158,158,158,.16); color: #c4c4c4; } .pill.manual i, .pill.unavailable i { background: #9e9e9e; }
  .ctl { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .ctl > * { min-width: 0; }
  .rowbox { border: 1px solid var(--divider-color); border-radius: 12px; padding: 8px 12px; display: flex; align-items: center; }
  .rowbox > * { flex: 1 1 auto; }
  .rowbox .k { font-size: 13px; color: var(--secondary-text-color); } .rowbox b { flex: none; font-weight: 500; }
  .plan { min-height: 150px; }
  .wrow { display: flex; align-items: center; gap: 12px; min-height: 36px; border-top: 1px solid var(--divider-color); --mdc-icon-size: 18px; color: var(--secondary-text-color); }
  .wrow:first-child { border-top: 0; }
  .wrow .k { width: 128px; flex: none; font-size: 13px; }
  .wrow .v { font-size: 13px; color: var(--primary-text-color); min-width: 0; }
  .stats { display: flex; gap: 8px; }
  .stat { flex: 1 1 0; padding: 10px 12px; border-radius: 10px; background: var(--secondary-background-color, #282828); display: flex; flex-direction: column; gap: 2px; }
  .stat span { font-size: 12px; color: var(--secondary-text-color); }
  .stat b { font-size: 18px; font-weight: 500; }
  .stat b.good { color: #7ccf80; }
  .stat small { font-size: 12px; font-weight: 400; color: var(--secondary-text-color); }
  footer { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 64px;
           padding: 0 16px 0 24px; border-top: 1px solid var(--divider-color); }
  #leg { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--secondary-text-color); --mdc-icon-size: 16px; }
  button.text { display: inline-flex; align-items: center; gap: 6px; height: 40px; padding: 0 12px; border: 0; border-radius: 20px; background: transparent;
                color: var(--primary-color); font: 500 14px var(--ha-font-family-body, Roboto, sans-serif); cursor: pointer; --mdc-icon-size: 18px; }
  button.text:hover { background: rgba(var(--rgb-primary-color,0,154,199), .08); }
  @media (max-width: 600px) {
    dialog { width: 100vw; max-width: 100vw; height: 100%; max-height: 100%; border-radius: 0; border: 0; margin: 0; }
    header { padding: 8px 4px; }
    button.close-l { display: flex; color: var(--primary-text-color); }
    button.close-r, .bubble { display: none; }
    .ttl h2 { font-size: 18px; }
    .body { padding: 4px 16px 16px; }
    footer { padding: 0 8px 0 16px; min-height: 72px; padding-bottom: env(safe-area-inset-bottom); }
  }
`;
