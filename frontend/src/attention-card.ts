// custom:powerplan-attention-card - "Trenger oppmerksomhet" (iteration 4).
//
// Replaces the section with a heading, an English "Repairs · 1 issue" tile and a bare
// "Målerstatus · Treg" tile. Shows, in the user's language and with one line of why:
//   - PowerPlan's own repair issues (repairs/list_issues, titles from the integration's translations)
//   - the meter status when it is not "ok" (degraded / stale), explained from its attributes
// Renders nothing (and takes no space) when there is nothing to say.
//
//   type: custom:powerplan-attention-card
//   meter_status: sensor.home_malerstatus
//   domain: powerplan            # optional

import { Hass, esc, fmtTemplate, moreInfo, navigate, pick, toNum, numFmt, lang } from "./r3-util";
import { TOKENS, SHARED } from "./tokens";

interface AttentionCfg { type: string; meter_status?: string; domain?: string; labels?: Record<string, string> }
interface Issue { domain: string; issue_id: string; translation_key: string; translation_placeholders?: Record<string, string>;
  severity: string; ignored: boolean; dismissed_version?: string | null; is_fixable?: boolean }

const A_LABELS: Record<string, Record<string, string>> = {
  nb: {
    title: "Trenger oppmerksomhet", repair: "Reparasjon: {title}", repair_sub: "PowerPlan · {n} åpen sak", repair_sub_n: "PowerPlan · {n} åpne saker",
    open: "Åpne", details: "Detaljer", meter_degraded: "Målerstatus: treg", meter_stale: "Målerstatus: ingen data",
    meter_degraded_text: "Målerdata henger etter (avvik {bias} W). Forsvinner når måleren er i takt igjen.",
    meter_stale_text: "Ingen nye målerdata på {age}. Planen bruker anslag så lenge.",
    unmetered: "{n} styrte laster uten egen måler", min: "{n} min", s: "{n} s",
  },
  en: {
    title: "Needs attention", repair: "Repair: {title}", repair_sub: "PowerPlan · {n} open issue", repair_sub_n: "PowerPlan · {n} open issues",
    open: "Open", details: "Details", meter_degraded: "Meter status: slow", meter_stale: "Meter status: no data",
    meter_degraded_text: "Meter data is lagging (off by {bias} W). This clears when the meter catches up.",
    meter_stale_text: "No new meter data for {age}. The plan uses estimates meanwhile.",
    unmetered: "{n} managed loads without their own meter", min: "{n} min", s: "{n} s",
  },
};

export class PowerplanAttentionCard extends HTMLElement {
  private config?: AttentionCfg;
  private hassRef?: Hass;
  private issues: Issue[] = [];
  private fetchedAt = 0;
  private busy = false;
  private key: unknown[] = [];

  setConfig(c: AttentionCfg): void {
    this.config = { domain: "powerplan", ...c };
    this.key = [];
  }

  set hass(h: Hass) {
    this.hassRef = h;
    if (!this.config) return;
    if (Date.now() - this.fetchedAt > 5 * 60e3) this.fetchIssues();
    const k = [this.config.meter_status ? h.states[this.config.meter_status] : undefined, h.language, this.fetchedAt];
    if (k.every((v, i) => v === this.key[i])) return;
    this.key = k;
    this.render();
  }

  getCardSize(): number { return this.hidden ? 0 : 2; }
  getGridOptions() { return { columns: 12, rows: "auto" }; }
  static getStubConfig() { return { meter_status: "" }; }

  private async fetchIssues(): Promise<void> {
    const h = this.hassRef;
    if (!h || this.busy) return;
    this.busy = true;
    try {
      const r = await h.callWS<{ issues: Issue[] }>({ type: "repairs/list_issues" });
      this.issues = (r?.issues ?? []).filter((i) => i.domain === this.config!.domain && !i.ignored && !i.dismissed_version);
      if (this.issues.length) await h.loadBackendTranslation?.("issues", this.config!.domain!);
    } catch {
      this.issues = [];                  // non-admin users can't list repairs; show the meter part only
    } finally {
      this.busy = false;
      this.fetchedAt = Date.now();
      if (this.hassRef) this.hass = this.hassRef;
    }
  }

  private render(): void {
    const h = this.hassRef, c = this.config;
    if (!h || !c) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot!.addEventListener("click", (e) => this.onClick(e));
    }
    const L = { ...pick(A_LABELS, h), ...(c.labels ?? {}) };
    const tiles: string[] = [];
    if (this.issues.length) {
      const first = this.issues[0];
      const key = `component.${c.domain}.issues.${first.translation_key}.title`;
      const title = h.localize?.(key, first.translation_placeholders ?? {}) || first.translation_key.replace(/_/g, " ");
      const sub = fmtTemplate(this.issues.length === 1 ? L.repair_sub : L.repair_sub_n, { n: this.issues.length });
      tiles.push(tile("mdi:wrench", fmtTemplate(L.repair, { title }), sub, { label: L.open, act: "repairs" }));
    }
    const ms = c.meter_status ? h.states[c.meter_status] : undefined;
    if (ms && (ms.state === "degraded" || ms.state === "stale")) {
      const a = ms.attributes ?? {};
      const nf0 = numFmt(h, 0);
      let text: string;
      if (ms.state === "degraded") {
        text = fmtTemplate(L.meter_degraded_text, { bias: nf0.format(Math.abs(toNum(a.integral_bias_w) ?? 0)) });
      } else {
        const age = Math.max(toNum(a.power_age_s) ?? 0, toNum(a.register_age_s) ?? 0);
        text = fmtTemplate(L.meter_stale_text, { age: age >= 120 ? fmtTemplate(L.min, { n: Math.round(age / 60) }) : fmtTemplate(L.s, { n: Math.round(age) }) });
      }
      const un = Array.isArray(a.unmetered_controlled) ? a.unmetered_controlled.length : 0;
      if (un) text += " " + fmtTemplate(L.unmetered, { n: un }) + ".";
      tiles.push(tile("mdi:meter-electric", ms.state === "degraded" ? L.meter_degraded : L.meter_stale, text, { label: L.details, act: "meter" }));
    }
    this.hidden = tiles.length === 0;
    this.style.display = tiles.length ? "" : "none";
    this.shadowRoot!.innerHTML = tiles.length ? `<style>${TOKENS}${SHARED}${CSS}</style>
      <section lang="${esc(lang(h))}" aria-labelledby="h"><h2 id="h">${esc(L.title)}</h2>${tiles.join("")}</section>` : "";
  }

  private onClick(e: Event): void {
    const b = (e.target as HTMLElement).closest("[data-act]") as HTMLElement | null;
    if (!b) return;
    if (b.dataset.act === "repairs") navigate("/config/repairs");
    if (b.dataset.act === "meter" && this.config?.meter_status) moreInfo(this, this.config.meter_status);
  }
}

function tile(icon: string, title: string, text: string, action: { label: string; act: string }): string {
  return `<div class="tile"><span class="ic"><ha-icon icon="${esc(icon)}"></ha-icon></span>
    <span class="tx"><span class="tt">${esc(title)}</span><span class="ts">${esc(text)}</span></span>
    <button type="button" class="tbtn" data-act="${esc(action.act)}">${esc(action.label)}</button></div>`;
}

const CSS = `
  section { display: flex; flex-direction: column; gap: 8px; }
  h2 { margin: 0; height: 34px; display: flex; align-items: center; padding: 0 4px; font-size: var(--pp-fs-l); font-weight: 400; }
  .tile { display: flex; align-items: center; gap: 12px; padding: 10px 8px 10px 12px; border-radius: var(--pp-radius); box-sizing: border-box;
          background: var(--pp-card); border: 1px solid var(--pp-divider); min-height: 56px; }
  .ic { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex: none;
        background: var(--pp-warn-bg); color: var(--pp-warn); --mdc-icon-size: 20px; }
  .tx { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 auto; }
  .tt { font-size: var(--pp-fs-m); font-weight: var(--pp-fw-m); line-height: 20px; }
  .ts { font-size: var(--pp-fs-s); color: var(--pp-text2); line-height: 16px; }
`;
