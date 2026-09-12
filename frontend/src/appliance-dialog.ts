// powerplan-appliance-dialog - iteration 4: Home Assistant's more-info pattern.
//
//   header   X (top-left, all sizes) · breadcrumb "Area › PowerPlan" over the title · history, settings, ⋮
//   body     hero (value + target + one status line) · Styring as a full-width select ·
//            one lane with the same encoding as Apparater + price track · "why" rows · this month
//   phone    bottom sheet with a drag handle when the viewport is ≤ 870 px wide or ≤ 500 px high
//            (the same breakpoint as ha-adaptive-dialog)
//
// Opened through HA's dialog manager (`show-dialog`), so back-gesture and one-dialog-at-a-time
// work as elsewhere. The element renders a native <dialog>; it does not depend on HA-internal
// dialog components. Controls are HA's own tile/row elements via window.loadCardHelpers().

import {
  Hass, esc, numFmt, numFmt01, timeFmt, readPlan, runsFor, loweredFor, holdKwh, moreInfo, navigate, fmtTemplate,
  pick, toNum, localHour, startOfHour, tz, lang, newUid, HassEntity, savingsView,
} from "./r3-util";
import { rawStatus, STATUS_LABELS } from "./status";
import { TOKENS, SHARED, hatchDef, iconButton } from "./tokens";
import type { LoadCfg, AppliancesCfg } from "./appliances-card";

const TAG = "powerplan-appliance-dialog";
const SHEET_MQ = "(max-width: 870px), (max-height: 500px)";

const D_LABELS: Record<string, Record<string, string>> = {
  nb: {
    control: "Styring", deadline: "Ferdig til", plan: "Neste 24 timer", why: "Hvorfor denne planen", month: "Denne måneden",
    need: "Behov", chosen: "Valgt tid", prices: "Priser", est_cost: "Anslått kostnad", cost: "Kostnad", savings: "Besparelse",
    energy: "Energi", open: "Åpne apparatside", device: "Sensorinfo", close: "Lukk", more: "Flere valg", settings: "Innstillinger",
    history: "Historikk", crumb_app: "PowerPlan", done_at: "ferdig ca. {time}", before: "før {time}",
    need_moved: "{kwh} kWh flyttes", need_hold: "{kwh} kWh holder temperaturen", no_run: "Ingen kjøring planlagt",
    min: "Minst {v}", target: "mål {v}", target_cap: "Mål {v}", prices_known: "Kjente priser", prices_part: "Anslått for {n} av {m} timer",
    prices_stale: "Anslått — ingen nye priser siden {time}", cost_for: "≈ {kr} kr for {kwh} kWh", no_ref: "mangler referanse",
    more_than_ref: "mer enn uten styring", legionella: "Legionella", legionella_next: "neste {date}", planned_word: "planlagt",
  },
  en: {
    control: "Control", deadline: "Ready by", plan: "Next 24 hours", why: "Why this plan", month: "This month",
    need: "Need", chosen: "Chosen time", prices: "Prices", est_cost: "Estimated cost", cost: "Cost", savings: "Savings",
    energy: "Energy", open: "Open appliance page", device: "Sensor info", close: "Close", more: "More options", settings: "Settings",
    history: "History", crumb_app: "PowerPlan", done_at: "done ≈ {time}", before: "before {time}",
    need_moved: "{kwh} kWh moved", need_hold: "{kwh} kWh holding temperature", no_run: "No run planned",
    min: "At least {v}", target: "target {v}", target_cap: "Target {v}", prices_known: "Known prices", prices_part: "Estimated for {n} of {m} hours",
    prices_stale: "Estimated — no new prices since {time}", cost_for: "≈ {kr} for {kwh} kWh", no_ref: "no reference yet",
    more_than_ref: "more than without control", legionella: "Legionella", legionella_next: "next {date}", planned_word: "planned",
  },
};

interface Params { load: LoadCfg; cfg: AppliancesCfg }

export function openApplianceDialog(host: HTMLElement, hass: Hass, load: LoadCfg, cfg: AppliancesCfg): void {
  if (!customElements.get(TAG)) customElements.define(TAG, PowerplanApplianceDialog);
  const params: Params = { load, cfg };
  host.dispatchEvent(new CustomEvent("show-dialog", {
    bubbles: true, composed: true,
    detail: { dialogTag: TAG, dialogImport: () => Promise.resolve(), dialogParams: params },
  }));
  // Fallback when no dialog manager caught the event (card preview in the editor, tests).
  setTimeout(() => {
    const ha = document.querySelector("home-assistant");
    if (document.querySelector(TAG) || ha?.shadowRoot?.querySelector(TAG)) return;
    const el = document.createElement(TAG) as PowerplanApplianceDialog;
    el.standalone = true;
    document.body.appendChild(el);
    el.hass = hass;
    el.showDialog(params);
  }, 250);
}

export class PowerplanApplianceDialog extends HTMLElement {
  standalone = false;
  private _hass?: Hass;
  private params?: Params;
  private dlg?: HTMLDialogElement;
  private kids: { el: any }[] = [];
  private key: unknown[] = [];
  private poll?: number;
  private ro?: ResizeObserver;
  private uid = newUid("ppd");
  private drag?: { y: number; dy: number };

  set hass(h: Hass) {
    this._hass = h;
    for (const c of this.kids) c.el.hass = h;
    if (!this.params) return;
    const l = this.params.load, c = this.params.cfg;
    const ids = [l.status, l.control, l.deadline, l.cost, l.savings, l.energy, l.legionella, c.entities.plan, c.entities.price_forecast];
    const k = [...ids.map((id) => (id ? h.states[id] : undefined)), Math.floor(Date.now() / 60e3)];
    if (k.every((v, i) => v === this.key[i])) return;
    this.key = k;
    this.renderDynamic();
  }
  get hass(): Hass | undefined { return this._hass; }

  async showDialog(params: Params): Promise<void> {
    this.params = params;
    this.key = [];
    this.renderShell();
    await this.mountBuiltins();
    if (this._hass) this.hass = this._hass;
    this.dlg!.showModal();
    this.ro = new ResizeObserver(() => this.renderLane());
    this.ro.observe(this.shadowRoot!.getElementById("plan")!);
    if (this.standalone) {
      this.poll = window.setInterval(() => {
        const h = (document.querySelector("home-assistant") as any)?.hass;
        if (h && h !== this._hass) this.hass = h;
      }, 1000);
    }
  }

  closeDialog(): void {
    if (this.dlg?.open) this.dlg.close();
  }

  private onClosed(): void {
    if (this.poll) clearInterval(this.poll);
    this.ro?.disconnect();
    this.params = undefined;
    this.kids = [];
    this.dispatchEvent(new CustomEvent("dialog-closed", { bubbles: true, composed: true, detail: { dialog: TAG } }));
    if (this.standalone) this.remove();
  }

  private L(): Record<string, string> {
    const h = this._hass!;
    return { ...pick(STATUS_LABELS, h), ...pick(D_LABELS, h), ...(this.params?.cfg.labels ?? {}) };
  }

  // ---------------------------------------------------------------- static part
  private renderShell(): void {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const { load } = this.params!;
    const L = this.L();
    const crumb = [load.area, L.crumb_app].filter(Boolean).map(esc).join(`<span class="sep" aria-hidden="true">›</span>`);
    this.shadowRoot!.innerHTML = `<style>${TOKENS}${SHARED}${CSS}</style>
      <dialog aria-labelledby="t">
        <div class="handle" data-drag aria-hidden="true"><span></span></div>
        <header>
          ${iconButton("mdi:close", L.close, "close", 'data-close style="color:var(--pp-text)"')}
          <div class="ttl"><span class="crumb">${crumb}</span><h2 id="t" tabindex="-1" autofocus>${esc(load.name)}</h2></div>
          ${iconButton("mdi:chart-box-outline", L.history, "history")}
          ${iconButton("mdi:cog-outline", L.settings, "settings")}
          <div class="menu-wrap">
            ${iconButton("mdi:dots-vertical", L.more, "menu", 'aria-haspopup="menu" aria-expanded="false"')}
            <div class="menu" role="menu" hidden>
              ${load.path ? `<button type="button" role="menuitem" data-act="open"><ha-icon icon="mdi:open-in-new"></ha-icon>${esc(L.open)}</button>` : ""}
              <button type="button" role="menuitem" data-act="device"><ha-icon icon="mdi:information-outline"></ha-icon>${esc(L.device)}</button>
            </div>
          </div>
        </header>
        <div class="body">
          <section class="hero" id="hero"></section>
          ${load.control || load.deadline ? `<div class="ctl" id="ctl"></div>` : ""}
          <h3>${esc(L.plan)}</h3><div class="plan" id="plan"></div>
          <h3>${esc(L.why)}</h3><div class="why" id="why"></div>
          ${load.cost || load.savings || load.energy ? `<h3>${esc(L.month)}</h3><div class="stats" id="stats"></div>` : ""}
        </div>
      </dialog>`;
    const dlg = this.shadowRoot!.querySelector("dialog")!;
    this.dlg = dlg;
    dlg.addEventListener("close", () => this.onClosed());
    dlg.addEventListener("click", (e) => this.onAct(e));
    dlg.addEventListener("keydown", (e) => this.onKey(e));
    // Bottom sheet: drag the handle down to close.
    const handle = this.shadowRoot!.querySelector("[data-drag]") as HTMLElement;
    handle.addEventListener("pointerdown", (e) => { this.drag = { y: e.clientY, dy: 0 }; handle.setPointerCapture(e.pointerId); });
    handle.addEventListener("pointermove", (e) => {
      if (!this.drag) return;
      this.drag.dy = Math.max(0, e.clientY - this.drag.y);
      dlg.style.transform = `translateY(${this.drag.dy}px)`;
    });
    handle.addEventListener("pointerup", () => {
      const close = (this.drag?.dy ?? 0) > 80;
      this.drag = undefined;
      dlg.style.transform = "";
      if (close) this.closeDialog();
    });
  }

  private onAct(e: Event): void {
    const dlg = this.dlg!;
    if (e.target === dlg) { this.closeDialog(); return; } // backdrop
    const b = (e.target as HTMLElement).closest("[data-act]") as HTMLElement | null;
    if (!b) return;
    const { load } = this.params!;
    const act = b.dataset.act;
    const menu = this.shadowRoot!.querySelector(".menu") as HTMLElement;
    const trigger = this.shadowRoot!.querySelector('[data-act="menu"]') as HTMLElement;
    if (act === "menu") {
      menu.hidden = !menu.hidden;
      trigger.setAttribute("aria-expanded", String(!menu.hidden));
      if (!menu.hidden) (menu.querySelector("[role=menuitem]") as HTMLElement | null)?.focus();
      return;
    }
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (act === "close") this.closeDialog();
    if (act === "history") {
      const ids = [load.status, load.energy].filter(Boolean).join(",");
      this.closeDialog();
      navigate(`/history?entity_id=${encodeURIComponent(ids)}`);
    }
    if (act === "settings") { this.closeDialog(); navigate("/config/integrations/integration/powerplan"); }
    if (act === "device") moreInfo(this, load.status);
    if (act === "open" && load.path) { this.closeDialog(); navigate(load.path); }
  }

  /** Menu keyboard: arrows move, Escape closes the menu before the dialog. */
  private onKey(e: KeyboardEvent): void {
    const menu = this.shadowRoot!.querySelector(".menu") as HTMLElement;
    if (menu.hidden) return;
    const items = [...menu.querySelectorAll("[role=menuitem]")] as HTMLElement[];
    const i = items.indexOf(this.shadowRoot!.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      menu.hidden = true;
      (this.shadowRoot!.querySelector('[data-act="menu"]') as HTMLElement).focus();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    }
  }

  /** HA's own tile (select feature) and row (time) elements, full width. */
  private async mountBuiltins(): Promise<void> {
    const { load } = this.params!;
    const L = this.L();
    const ctl = this.shadowRoot!.getElementById("ctl");
    if (!ctl) return;
    const helpers = await (window as any).loadCardHelpers?.();
    if (helpers) {
      if (load.control) {
        const el = helpers.createCardElement({
          type: "tile", entity: load.control, name: L.control, icon: "mdi:tune-variant",
          features: [{ type: "select-options" }], features_position: "inline",
        });
        ctl.appendChild(el);
        this.kids.push({ el });
      }
      if (load.deadline) {
        const box = document.createElement("div");
        box.className = "rowbox";
        const el = helpers.createRowElement({ entity: load.deadline, name: L.deadline, icon: "mdi:clock-check-outline" });
        box.appendChild(el);
        ctl.appendChild(box);
        this.kids.push({ el });
      }
    } else {
      ctl.innerHTML = [load.control, load.deadline].filter(Boolean).map((id) =>
        `<div class="rowbox"><span class="k">${esc(this._hass?.states[id!]?.attributes?.friendly_name ?? id)}</span><b>${esc(this._hass?.states[id!]?.state ?? "–")}</b></div>`).join("");
    }
    if (this._hass) for (const c of this.kids) c.el.hass = this._hass;
  }

  // ---------------------------------------------------------------- live part
  private renderDynamic(): void {
    const hass = this._hass, p = this.params, root = this.shadowRoot;
    if (!hass || !p || !root) return;
    const { load, cfg } = p;
    const L = this.L();
    const nf1 = numFmt01(hass), nf2 = numFmt(hass, 2);
    const tf = timeFmt(hass);
    const st = hass.states[load.status];
    const a = st?.attributes ?? {};
    const plan = readPlan(hass.states[cfg.entities.plan]);
    const now = new Date();
    const runs = runsFor(load.id, plan.slots).filter((r) => r.end > now);
    const cur = plan.slots.find((s) => s.start <= now && s.end > now);
    const view = rawStatus(st, load.control ? hass.states[load.control] : undefined,
      { runNow: runs.some((r) => r.start <= now), futureRun: runs.length > 0, holdNow: (cur?.hold[load.id] ?? 0) > 0.0005 }, L);
    const bl = plan.byLoad[load.id] ?? {};
    const moved = toNum(bl.planned_kwh ?? a.planned_kwh) ?? 0;
    const hold = toNum(bl.hold_kwh) ?? holdKwh(load.id, plan.slots);
    const cost = toNum(String(bl.cost ?? a.cost ?? "").split(" ")[0]);
    const dlTxt = (load.deadline && hass.states[load.deadline]?.state?.slice(0, 5)) || a.deadline_time || "";

    // hero: value + target, one status line, comfort bar
    const t = toNum(a.current), tgt = toNum(a.target), flo = toNum(a.floor);
    const statusLine = [view.word + (view.kind === "running" && toNum(a.granted_power) ? ` · ${nf1.format(toNum(a.granted_power)! / 1000)} kW` : ""),
      view.reason, runs[0] && runs[0].start <= now ? fmtTemplate(L.done_at, { time: tf.format(runs[0].end) }) : "",
      dlTxt ? fmtTemplate(L.deadline, { time: dlTxt }) : ""].filter(Boolean).join(" · ");
    let hero: string;
    if (t !== null && tgt !== null) {
      const lo = flo ?? Math.min(t, tgt) - 3;
      const f = Math.max(0, Math.min(1, (t - lo) / Math.max(tgt - lo, 0.1)));
      hero = `<div class="big"><span>${nf1.format(t)} °C</span><small>${esc(fmtTemplate(L.target, { v: nf1.format(tgt) + " °C" }))}</small></div>
        <div class="sline">${esc(statusLine)}</div>
        <div class="bar" style="--c:${esc(load.color)}"><div class="fill" style="width:${(f * 100).toFixed(1)}%"></div></div>
        <div class="scale"><span>${esc(fmtTemplate(L.min, { v: nf1.format(lo) + " °C" }))}</span><span>${esc(fmtTemplate(L.target_cap, { v: nf1.format(tgt) + " °C" }))}</span></div>`;
    } else {
      hero = `<div class="big"><span>${nf2.format(moved)} kWh</span><small>${esc(L.planned_word)}</small></div>
        <div class="sline">${esc(statusLine)}</div>`;
    }
    root.getElementById("hero")!.innerHTML = hero;

    // why
    const need = [moved > 0.005 ? fmtTemplate(L.need_moved, { kwh: nf2.format(moved) }) : "",
      hold > 0.005 ? fmtTemplate(L.need_hold, { kwh: nf2.format(hold) }) : ""].filter(Boolean).join(" · ")
      + (dlTxt && moved > 0.005 ? " " + fmtTemplate(L.before, { time: dlTxt }) : "");
    const chosen = runs.length ? runs.slice(0, 3).map((r) => `${tf.format(r.start)}–${tf.format(r.end)}`).join(" · ") : L.no_run;
    const pr = this.priceState(runs.length ? runs : [{ start: startOfHour(now), end: new Date(startOfHour(now).getTime() + 24 * 3600e3), kwh: 0 }]);
    const totalKwh = moved + hold;
    const rows: [string, string, string, boolean][] = [
      ["mdi:timer-sand-complete", L.need, need || "–", false],
      ["mdi:calendar-clock", L.chosen, chosen, false],
      [pr.warn ? "mdi:alert-outline" : "mdi:check-circle-outline", L.prices, pr.text, pr.warn],
      ["mdi:cash", L.est_cost, cost !== null ? fmtTemplate(L.cost_for, { kr: nf2.format(cost), kwh: nf1.format(totalKwh) }) : "–", false],
    ];
    const leg = load.legionella ? hass.states[load.legionella] : undefined;
    if (leg && !isNaN(Date.parse(leg.state))) {
      rows.push(["mdi:water-thermometer", L.legionella, fmtTemplate(L.legionella_next, {
        date: new Intl.DateTimeFormat(lang(hass), { day: "numeric", month: "short", timeZone: tz(hass) }).format(new Date(leg.state)),
      }), false]);
    }
    root.getElementById("why")!.innerHTML = rows.map(([i, k, v, w]) =>
      `<div class="wrow"><ha-icon icon="${i}" class="${w ? "warn" : ""}"></ha-icon><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join("");

    // month
    const stats = root.getElementById("stats");
    if (stats) {
      const unitOf = (e?: HassEntity) => (e?.attributes.unit_of_measurement === "NOK" ? "kr" : e?.attributes.unit_of_measurement ?? "");
      const cell = (label: string, value: string, unit: string, sub = "") =>
        `<div class="stat"><span class="sl">${esc(label)}</span><span class="sv">${esc(value)} <small>${esc(unit)}</small></span>${sub ? `<span class="ss">${esc(sub)}</span>` : ""}</div>`;
      const ce = load.cost ? hass.states[load.cost] : undefined;
      const se = load.savings ? hass.states[load.savings] : undefined;
      const ee = load.energy ? hass.states[load.energy] : undefined;
      const sv = savingsView(se, ce);
      stats.innerHTML =
        (ce ? cell(L.cost, toNum(ce.state) !== null ? nf2.format(toNum(ce.state)!) : "–", unitOf(ce)) : "") +
        (se ? cell(L.savings, sv.missing ? "—" : nf2.format(sv.value!), sv.missing ? "" : unitOf(se), sv.missing ? L.no_ref : sv.value! < 0 ? L.more_than_ref : "") : "") +
        (ee ? cell(L.energy, toNum(ee.state) !== null ? nf2.format(toNum(ee.state)!) : "–", unitOf(ee)) : "");
    }
    this.renderLane();
  }

  /** Price confidence for the hours that matter (the chosen runs, else the next 24 h). */
  private priceState(ws: { start: Date; end: Date }[]): { text: string; warn: boolean } {
    const hass = this._hass!, L = this.L(), cfg = this.params!.cfg;
    const pe = cfg.entities.price_forecast ? hass.states[cfg.entities.price_forecast] : undefined;
    const tf = timeFmt(hass);
    if (!pe || ["unknown", "unavailable"].includes(pe.state)) {
      return { text: fmtTemplate(L.prices_stale, { time: pe ? tf.format(new Date(pe.last_changed)) : "–" }), warn: true };
    }
    const slots: any[] = pe.attributes?.slots ?? [];
    const hours = new Map<number, boolean>();
    for (const s of slots) {
      const st = Date.parse(s.start);
      if (!ws.some((w) => st >= w.start.getTime() && st < w.end.getTime())) continue;
      const hk = Math.floor(st / 3600e3);
      hours.set(hk, (hours.get(hk) ?? true) && s.confidence === "known");
    }
    const m = hours.size, n = [...hours.values()].filter((v) => !v).length;
    if (!m) return { text: fmtTemplate(L.prices_stale, { time: tf.format(new Date(pe.last_changed)) }), warn: true };
    return n ? { text: fmtTemplate(L.prices_part, { n, m }), warn: true } : { text: L.prices_known, warn: false };
  }

  /** One lane (same encoding as Apparater) + price track + axis, sized to the body width. */
  private renderLane(): void {
    const hass = this._hass, p = this.params, box = this.shadowRoot?.getElementById("plan");
    if (!hass || !p || !box) return;
    const W = Math.round(box.clientWidth);
    if (!W) return;
    const { load, cfg } = p;
    const L = this.L();
    const tf = timeFmt(hass);
    const plan = readPlan(hass.states[cfg.entities.plan]);
    const now = new Date();
    const a0 = startOfHour(now), a1 = new Date(a0.getTime() + 24 * 3600e3);
    const sx = (d: Date | number) => 1 + (+d - a0.getTime()) / (a1.getTime() - a0.getTime()) * (W - 2);
    const runs = runsFor(load.id, plan.slots).filter((r) => r.end > now && r.start < a1);
    const low = loweredFor(load.id, plan.slots).filter((r) => r.end > now && r.start < a1);
    const pe = cfg.entities.price_forecast ? hass.states[cfg.entities.price_forecast] : undefined;
    const ps = ((pe?.attributes?.slots ?? []) as any[]).map((s) => ({ s: Date.parse(s.start), e: Date.parse(s.end), p: Number(s.total), k: s.confidence === "known" }))
      .filter((x) => x.e > a0.getTime() && x.s < a1.getTime() && Number.isFinite(x.p));
    const lanes: string[] = [];
    const top = 26, lh = 20;
    lanes.push(`<rect x="1" y="${top}" width="${W - 2}" height="${lh}" rx="${lh / 2}" class="track"/>`);
    // cheap hours
    if (ps.length) {
      const lo = Math.min(...ps.map((x) => x.p)), hi = Math.max(...ps.map((x) => x.p));
      if (hi - lo > 1e-4) for (const x of ps) if (x.p <= lo + (hi - lo) * 0.25) lanes.unshift(`<rect x="${sx(x.s).toFixed(1)}" y="${top - 8}" width="${(sx(x.e) - sx(x.s)).toFixed(1)}" height="${lh + 16}" class="band"/>`);
    }
    for (const w of low) lanes.push(`<rect x="${sx(w.start).toFixed(1)}" y="${top}" width="${(sx(w.end) - sx(w.start)).toFixed(1)}" height="${lh}" rx="${lh / 2}" style="fill:url(#${this.uid}-h)"/>`);
    let lastLabelX = -Infinity;
    for (const r of runs) {
      const x0 = Math.max(sx(r.start), 1), x1 = Math.min(sx(r.end), W - 1);
      lanes.push(`<rect x="${x0.toFixed(1)}" y="${top}" width="${Math.max(x1 - x0, lh).toFixed(1)}" height="${lh}" rx="${lh / 2}" style="fill:${esc(load.color)}"/>`);
      if (r.start > now && x0 - lastLabelX > 48) {
        lanes.push(`<text x="${x0.toFixed(1)}" y="${top - 8}" class="t-s on">${esc(tf.format(r.start))}</text>`);
        lastLabelX = x0;
      }
    }
    const xn = sx(now);
    lanes.push(`<line x1="${xn.toFixed(1)}" y1="${top - 10}" x2="${xn.toFixed(1)}" y2="${top + lh + 4}" class="now"/>`);
    // price track: one block per price level, dashed outline while estimated
    const py = top + lh + 12;
    const segs: { s: number; e: number; p: number; k: boolean }[] = [];
    for (const x of ps) {
      const last = segs[segs.length - 1];
      if (last && Math.abs(last.p - x.p) < 1e-4 && Math.abs(last.e - x.s) < 1000) { last.e = x.e; last.k = last.k && x.k; }
      else segs.push({ ...x });
    }
    const nf = numFmt(hass, 2);
    const hi = segs.length ? Math.max(...segs.map((s) => s.p)) : 0;
    for (const s of segs) {
      const x0 = sx(Math.max(s.s, a0.getTime())) + 1, x1 = sx(Math.min(s.e, a1.getTime())) - 1;
      if (x1 <= x0) continue;
      lanes.push(`<rect x="${x0.toFixed(1)}" y="${py}" width="${(x1 - x0).toFixed(1)}" height="20" rx="4" class="${s.p >= hi - 1e-4 ? "phi" : "plo"} ${s.k ? "" : "pest"}"/>`);
      if (x1 - x0 > 40) lanes.push(`<text x="${((x0 + x1) / 2).toFixed(1)}" y="${py + 14.5}" class="t-s on" text-anchor="middle">${esc(nf.format(s.p))}</text>`);
    }
    // axis
    const every = W < 420 ? 6 : 3;
    for (let t = a0.getTime() + 3600e3; t < a1.getTime(); t += 3600e3) {
      if (Math.round(localHour(t, hass)) % every) continue;
      lanes.push(`<text x="${sx(t).toFixed(1)}" y="${py + 40}" class="t-s" text-anchor="middle">${esc(tf.format(t))}</text>`);
    }
    const H = py + 48;
    box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(L.plan)}">
      <defs>${hatchDef(`${this.uid}-h`)}</defs>${lanes.join("")}</svg>`;
  }
}

const CSS = `
  :host { display: contents; }
  dialog { width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 64px); padding: 0; border: 0;
           border-radius: var(--ha-dialog-border-radius, 28px); background: var(--ha-dialog-surface-background, var(--pp-card));
           color: var(--pp-text); font-family: var(--pp-font); box-shadow: 0 12px 40px rgba(0,0,0,.4); overflow: hidden;
           display: none; flex-direction: column; box-sizing: border-box; }
  dialog[open] { display: flex; }
  dialog::backdrop { background: var(--ha-dialog-scrim-color, rgba(0,0,0,.5)); }
  .handle { display: none; }
  header { display: flex; align-items: center; gap: 4px; padding: 12px 12px 8px; flex: none; }
  .ttl { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; padding-left: 4px; }
  .crumb { font-size: var(--pp-fs-s); line-height: 16px; color: var(--pp-text2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .crumb .sep { margin: 0 4px; opacity: .7; }
  .ttl h2 { margin: 0; font-size: var(--pp-fs-xl); font-weight: 400; line-height: 28px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ttl h2:focus { outline: none; }
  .menu-wrap { position: relative; }
  .menu { position: absolute; right: 0; top: 50px; z-index: 2; min-width: 220px; padding: 6px 0; border-radius: 12px;
          background: var(--pp-card); border: 1px solid var(--pp-divider); box-shadow: 0 8px 24px rgba(0,0,0,.3); }
  .menu button { display: flex; align-items: center; gap: 12px; width: 100%; height: 48px; padding: 0 16px; border: 0; background: transparent;
                 color: var(--pp-text); font: var(--pp-fs-m) var(--pp-font); cursor: pointer; --mdc-icon-size: 20px; text-align: left; }
  .menu button:hover, .menu button:focus-visible { background: var(--pp-fill); outline: none; }
  .body { padding: 0 24px 24px; overflow-y: auto; flex: 1 1 auto; }
  h3 { margin: 24px 0 8px; font-size: var(--pp-fs-m); font-weight: var(--pp-fw-m); color: var(--pp-text); }
  .hero { display: flex; flex-direction: column; gap: 8px; padding: 8px 0 4px; }
  .big { display: flex; align-items: baseline; gap: 10px; }
  .big span { font-size: var(--pp-fs-5xl); letter-spacing: -.5px; line-height: 44px; }
  .big small { font-size: var(--pp-fs-l); color: var(--pp-text2); }
  .sline { font-size: var(--pp-fs-m); color: var(--pp-text2); }
  .bar { position: relative; height: 8px; border-radius: 4px; background: var(--pp-track); overflow: hidden; margin-top: 4px; }
  .bar .fill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--c); border-radius: 4px; }
  .scale { display: flex; justify-content: space-between; font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .ctl { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }
  .ctl > * { min-width: 0; }
  .rowbox { border: 1px solid var(--pp-divider); border-radius: var(--pp-radius); padding: 4px 12px; display: flex; align-items: center; min-height: 48px; }
  .rowbox > * { flex: 1 1 auto; }
  .rowbox .k { font-size: var(--pp-fs-m); color: var(--pp-text2); } .rowbox b { flex: none; font-weight: var(--pp-fw-m); }
  .plan { min-height: 118px; }
  .plan svg { display: block; }
  .plan .track { fill: var(--pp-track); }
  .plan .band { fill: var(--pp-cheap); }
  .plan .now { stroke: var(--pp-text); stroke-width: 1.5; }
  .plan .phi { fill: var(--pp-price-hi); } .plan .plo { fill: var(--pp-price-lo); }
  .plan .pest { stroke: var(--pp-warn); stroke-width: 1; stroke-dasharray: 3 3; }
  .wrow { display: flex; align-items: center; gap: 14px; min-height: 44px; padding: 4px 0; border-top: 1px solid var(--pp-divider); --mdc-icon-size: 20px; color: var(--pp-text2); box-sizing: border-box; }
  .wrow:first-child { border-top: 0; }
  .wrow ha-icon.warn { color: var(--pp-warn); }
  .wrow .k { width: 120px; flex: none; font-size: var(--pp-fs-m); }
  .wrow .v { font-size: var(--pp-fs-m); line-height: 20px; color: var(--pp-text); min-width: 0; }
  .stats { display: flex; gap: 8px; }
  .stat { flex: 1 1 0; min-width: 0; padding: 12px; border-radius: var(--pp-radius); background: var(--pp-fill); display: flex; flex-direction: column; gap: 2px; }
  .stat .sl { font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .stat .sv { font-size: var(--pp-fs-xl); }
  .stat small { font-size: var(--pp-fs-s); color: var(--pp-text2); }
  .stat .ss { font-size: var(--pp-fs-s); line-height: 16px; color: var(--pp-text2); }
  @media ${SHEET_MQ} {
    dialog { width: 100vw; max-width: 100vw; margin: auto 0 0 0; max-height: calc(100vh - 56px); border-radius: 28px 28px 0 0;
             padding-bottom: env(safe-area-inset-bottom); transition: transform var(--pp-anim) ease-out; }
    .handle { display: flex; justify-content: center; padding: 8px 0 0; cursor: grab; touch-action: none; }
    .handle span { width: 32px; height: 4px; border-radius: 2px; background: var(--pp-text3); }
    header { padding: 4px 4px 4px; }
    .ttl h2 { font-size: var(--pp-fs-l); line-height: 24px; }
    .body { padding: 0 16px 24px; }
    .wrow .k { width: 96px; }
  }
  @media (prefers-reduced-motion: reduce) { dialog { transition: none; } }
`;
