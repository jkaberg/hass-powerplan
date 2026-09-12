// `powerplan-timeline-card` (D12 §5.2): the next 12–48 hours on one kW axis -
// each appliance's planned power stacked on the rest of the house, so a bar's
// top is the total the household compares with the dashed limit - and the
// price as a strip under the time axis. ECharts at HA's own major version,
// themed from HA's CSS variables. The rule numbers are D12 §5.2's; T1–T10 the
// iteration-2 polish (§5.11). `mode: history` (D12 §5.7) draws the past instead,
// on the History view's picker: grid usage per hour or per day against the
// limit, the days that count.
//
// One vertical stack, nothing over the canvas (T1): the toggle, the chart,
// the touch readout, the legend. Inside the canvas two grids of fixed pixel
// geometry (T2), the markers as horizontal `graphic` labels (T3).

import type { ECharts, EChartsCoreOption } from "echarts/core";

import type { Hass } from "./r3-util";
import { observePlanHost, renderPlanMode } from "./timeline-plan-mode";
import { fetchStatistics, followPeriod, gridHours, kwhScale, monthRanking, type Period, statisticsPeriod, type StatRow } from "./energy";
import { cssVar, type HomeAssistant, timeZone } from "./ha";
import { ppStyles, tooltipStyle } from "./styles";
import {
  countingDays,
  currencyWord,
  type DayPeak,
  dayKey,
  estimatedRanges,
  legendItems,
  midnights,
  moneyFormat,
  nextPlanned,
  niceScale,
  type PlanSlot,
  type PriceRun,
  priceRuns,
  type PriceSlot,
  type Readout,
  sliceWindow,
  slotReadout,
  stackOffsets,
  steps,
  type TimelineSlot,
  timelineSlots,
  windowHours,
  withAlpha,
  withoutDate,
} from "./transforms";

interface TimelineConfig {
  entry_id: string;
  mode?: "plan" | "history";
  /** `mode: history`: the Energy preferences' grid consumption statistics. */
  grid_entities?: string[];
  hours?: number;
  hours_options?: number[];
  narrow_hours?: number;
  narrow_width?: number;
  loads: Array<{ id: string; name: string; color?: string }>;
  entities: {
    plan: string;
    price_forecast: string;
    deadline?: string;
    window_used?: string;
    ceiling?: string;
    price?: string;
    advice?: string;
  };
  show?: string[];
  currency?: string;
  /** Now's Plan card: the whole-house forecast with a rail this wide (D12 §5.12 F1–F6). */
  rail_width?: number;
  /** Iteration 4: `window` sums the slots to the capacity window (kWh/h), `slot` keeps 15 min. */
  bucket?: "window" | "slot";
  /** Iteration 4: the plan mode's own words over its built-in tables (`FORECAST_LABELS`). */
  plan_labels?: Record<string, string>;
  labels?: Record<string, string>;
}

/** HA's chart palette (`--color-1…10`), where a load has no colour and the theme sets none (D-0454). */
const PALETTE = [
  "#4269d0", "#f4bd4a", "#ff725c", "#6cc5b0", "#a463f2",
  "#ff8ab7", "#9c6b4e", "#97bbf5", "#01ab63", "#094bad",
];
/** Redraw at least this often, so the "now" line moves with no new data (rule 6). */
const NOW_STEP_MS = 5 * 60_000;
const HOUR_MS = 3_600_000;
/** T2: the main grid and the price strip, in px of the canvas. */
const GRID = { top: 24, left: 36, right: 8, bottom: 62 };
const STRIP = { left: 36, right: 8, bottom: 8, height: 22 };
/** T10: the plot's height - about 250 px on a desktop, at least 180 px on a phone. */
const PLOT_PX = 250;
const PLOT_NARROW_PX = 180;
const OFFSET = "\u0000offset";
const MARKS = "\u0000marks";

const fill = (text: string, values: Record<string, string>) =>
  text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);

const escape = (value: string) =>
  value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

/** A marker the canvas draws as a horizontal label on a vertical line (T3). */
interface Marker {
  at: number;
  kind: "now" | "midnight" | "deadline";
  text: string;
}

interface LegendEntry {
  name: string;
  color: string;
  value?: string;
  swatch?: "line" | "strip";
  /** The chart series the entry toggles; none for a mark that is not a series. */
  series?: boolean;
}

export class PowerplanTimelineCard extends HTMLElement {
  private config?: TimelineConfig;
  private hassRef?: HomeAssistant;
  private chart?: ECharts;
  private els?: {
    toggle: HTMLDivElement;
    plot: HTMLDivElement;
    chart: HTMLDivElement;
    side: HTMLDivElement;
    readout: HTMLDivElement;
    legend: HTMLDivElement;
    message: HTMLDivElement;
    /** Now's Plan card (iteration 4): the host `renderPlanMode` draws into. */
    fc: HTMLDivElement;
  };
  private key: unknown[] = [];
  private resize?: ResizeObserver;
  private width = 0;
  /** The household's toggle; never written to the config (rule 1). */
  private chosen?: number;
  private pinned?: number;
  private off = new Set<string>();
  private slots: TimelineSlot[] = [];
  private markers: Marker[] = [];
  private stripUnit = "";

  private period?: Period;
  /** Now's Plan card's own chart and its resize observer (`timeline-plan-mode.ts`). */
  private fcChart?: { dispose(): void };
  private unobserveFc?: () => void;
  private unfollow?: () => void;

  public setConfig(config: TimelineConfig): void {
    if (config?.mode === "history") {
      if (!config.grid_entities?.length) throw new Error("powerplan-timeline-card history needs grid_entities");
      this.config = { ...config, loads: config.loads ?? [], entities: config.entities ?? ({} as TimelineConfig["entities"]) };
      this.key = [];
      return;
    }
    if (!config?.entities?.plan || !config.entities.price_forecast) {
      throw new Error("powerplan-timeline-card needs entities.plan and entities.price_forecast");
    }
    this.config = config;
    this.key = [];
  }

  public set hass(hass: HomeAssistant) {
    this.hassRef = hass;
    const config = this.config;
    if (!config) return;
    if (config.mode === "history") {
      this.follow();
      return;
    }
    // HA replaces a state object only when it changes: redraw on a new plan,
    // a new curve, a new deadline, a theme or language switch, or the clock.
    const key = [
      hass.states[config.entities.plan],
      hass.states[config.entities.price_forecast],
      config.entities.deadline ? hass.states[config.entities.deadline] : undefined,
      hass.themes.darkMode,
      hass.language,
      Math.floor(Date.now() / NOW_STEP_MS),
    ];
    if (key.every((part, index) => part === this.key[index])) return;
    this.key = key;
    void this.render();
  }

  public connectedCallback(): void {
    this.resize = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === this) {
          const width = entry.contentRect.width;
          if (Math.abs(width - this.width) < 1) continue;
          const narrowBefore = this.narrow;
          this.width = width;
          // Now's Plan card resizes itself in place (`observePlanHost`); the rest redraws on a new width class.
          if (this.narrow !== narrowBefore || (!this.chart && !this.forecast)) {
            if (this.config?.mode === "history") void this.renderHistory();
            else void this.render();
            continue;
          }
        }
        this.chart?.resize();
        this.drawMarkers();
      }
    });
    this.resize.observe(this);
    if (this.els) this.resize.observe(this.els.plot);
    if (this.hassRef) {
      this.key = [];
      this.hass = this.hassRef;
    }
  }

  /** `mode: history` follows the History view's picker (D12 §5.7). */
  private follow(): void {
    if (!this.hassRef || this.unfollow || !this.isConnected) return;
    this.unfollow = followPeriod(this.hassRef, (period) => {
      this.period = period;
      void this.renderHistory();
    });
  }

  public disconnectedCallback(): void {
    this.unfollow?.();
    this.unfollow = undefined;
    this.resize?.disconnect();
    this.chart?.dispose();
    this.chart = undefined;
    this.unobserveFc?.();
    this.unobserveFc = undefined;
    this.fcChart?.dispose();
    this.fcChart = undefined;
  }

  public getCardSize(): number {
    return 7;
  }

  /** T10: the card is as tall as its stack, so the plot keeps its height under any legend. */
  public getGridOptions(): Record<string, number | string> {
    return { columns: 12, rows: "auto", min_columns: 6 };
  }

  private get single(): boolean {
    return this.config?.loads.length === 1 && Boolean(this.config.entities.deadline);
  }

  private get forecast(): boolean {
    return this.config?.rail_width !== undefined && this.config.mode !== "history" && !this.single;
  }

  private get narrow(): boolean {
    return this.width > 0 && this.width < (this.config?.narrow_width ?? 500);
  }

  private get touch(): boolean {
    const coarse = typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches;
    return coarse || this.narrow;
  }

  private css(name: string, fallback: string): string {
    return cssVar(this, name, fallback);
  }

  private shell(): NonNullable<PowerplanTimelineCard["els"]> {
    if (this.els) return this.els;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ${ppStyles}
        .tl { display: flex; flex-direction: column; gap: 10px; height: 100%; box-sizing: border-box;
              padding: 14px var(--pp-pad) var(--pp-pad); }
        .head { display: flex; justify-content: flex-end; }
        .head[hidden] { display: none; }
        .tl-chart { position: relative; flex: none; }
        .chart { position: absolute; inset: 0; }
        .message { position: absolute; inset: 0 0 ${GRID.bottom}px ${GRID.left}px; display: flex; align-items: center;
                   justify-content: center; text-align: center; font-size: 14px; color: var(--secondary-text-color);
                   pointer-events: none; }
        .pp-readout { flex-direction: column; align-items: flex-start; justify-content: center; gap: 0; }
        .pp-readout[hidden], .message[hidden], .pp-legend[hidden] { display: none; }
        .tl.fc { position: relative; padding: 0; gap: 0; }
        .tl.fc .head { position: absolute; top: 10px; right: 12px; z-index: 2; }
        .tl.fc .message { inset: 0; }
        .side:empty { display: none; }
        .pp-fc { position: relative; }
        .pp-fc[hidden] { display: none; }
      </style>
      <ha-card><div class="tl">
        <div class="head"><div class="pp-toggle" role="group"></div></div>
        <div class="tl-chart"><div class="side"></div><div class="chart"></div><div class="message" hidden></div></div>
        <div class="pp-readout" hidden></div>
        <div class="pp-legend"></div>
        <div class="pp-fc" hidden></div>
      </div></ha-card>`;
    this.els = {
      toggle: root.querySelector(".pp-toggle") as HTMLDivElement,
      plot: root.querySelector(".tl-chart") as HTMLDivElement,
      chart: root.querySelector(".chart") as HTMLDivElement,
      side: root.querySelector(".side") as HTMLDivElement,
      readout: root.querySelector(".pp-readout") as HTMLDivElement,
      legend: root.querySelector(".pp-legend") as HTMLDivElement,
      message: root.querySelector(".message") as HTMLDivElement,
      fc: root.querySelector(".pp-fc") as HTMLDivElement,
    };
    this.resize?.observe(this.els.plot);
    return this.els;
  }

  /** T10: the canvas is the plot plus T2's fixed top and bottom. */
  private sizePlot(): void {
    const plot = this.narrow ? PLOT_NARROW_PX : PLOT_PX;
    this.els!.plot.style.height = `${plot + GRID.top + GRID.bottom}px`;
  }

  private async ensureChart(): Promise<boolean> {
    if (this.chart) return true;
    const { echarts } = await import("./chart");
    if (this.chart || !this.isConnected) return Boolean(this.chart);
    this.chart = echarts.init(this.els!.chart, undefined, { renderer: "canvas" });
    this.chart.getZr().on("click", (event: { offsetX: number; offsetY: number }) => this.tap(event));
    return true;
  }

  private async render(): Promise<void> {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    if (this.forecast) {
      await this.renderForecast(hass, config);
      return;
    }
    const els = this.shell();
    this.sizePlot();
    const labels = config.labels ?? {};
    const hours = windowHours(this.width, config, this.chosen);
    const plan = hass.states[config.entities.plan];
    const prices = hass.states[config.entities.price_forecast];
    const all = timelineSlots(
      (prices?.attributes.slots as PriceSlot[] | undefined) ?? [],
      (plan?.attributes.slots as PlanSlot[] | undefined) ?? [],
      Number(plan?.attributes.window_min ?? 60),
      config.loads.map((load) => load.id),
      Date.now(),
      48,
    );
    this.slots = all.length ? sliceWindow(all, all[0]!.start, hours) : [];
    this.drawToggle(hours);
    const empty = this.slots.length === 0;
    els.chart.hidden = empty;
    const idle =
      !empty &&
      this.single &&
      legendItems(config.loads, this.slots).length === 0 &&
      !this.slots.some((slot) => Object.values(slot.holdKw).some((kw) => kw > 0));
    els.message.hidden = !(empty || idle);
    els.message.textContent = empty ? (labels.no_plan ?? "") : idle ? (labels.no_run ?? "") : "";
    if (empty) {
      els.readout.hidden = true;
      els.legend.replaceChildren();
      return;
    }
    if (!(await this.ensureChart())) return;
    this.chart!.setOption(this.option(hass, config, this.slots, hours), { notMerge: true });
    for (const name of this.off) this.chart!.dispatchAction({ type: "legendUnSelect", name });
    this.chart!.resize();
    this.markersOnFinish();
    this.drawMarkers();
    this.drawLegend(this.planLegend(config));
    this.drawReadout();
  }

  // ------------------------------------------------- the forecast (F1–F6)

  /** Now's Plan card (iteration 4): the whole house per capacity window, drawn by `renderPlanMode` as given. */
  private async renderForecast(hass: HomeAssistant, config: TimelineConfig): Promise<void> {
    const els = this.shell();
    for (const el of [els.plot, els.readout, els.legend]) el.hidden = true;
    (els.plot.parentElement as HTMLElement).classList.add("fc");
    const host = els.fc;
    host.hidden = false;
    const compact = this.width > 0 && this.width < 600;
    host.style.height = `${compact ? 420 : 440}px`;
    this.unobserveFc ??= observePlanHost(host, () => void this.render());
    const hours = windowHours(this.width, config, this.chosen);
    this.drawToggle(hours);
    const { echarts } = await import("./chart");
    if (!this.isConnected) return;
    const loads = config.loads.map((load) => ({ id: load.id, name: load.name, color: this.colorOf(load.id) }));
    this.fcChart = renderPlanMode(
      host,
      hass as unknown as Hass,
      { entities: config.entities, loads, rail_width: config.rail_width, bucket: config.bucket, currency: config.currency, labels: config.plan_labels },
      hours,
      this.fcChart,
      echarts,
    );
  }

  // --------------------------------------------------------- rule 1, T6

  private drawToggle(hours: number): void {
    const configured = this.config?.hours_options ?? [];
    // T6: a narrow card offers 12 h too, pressed on the first render.
    const options = configured.length && this.narrow ? [...new Set([12, ...configured])].sort((a, b) => a - b) : configured;
    const labels = this.config?.labels ?? {};
    const toggle = this.els!.toggle;
    (toggle.parentElement as HTMLElement).hidden = options.length === 0;
    toggle.replaceChildren(
      ...options.map((option) => {
        const button = document.createElement("button");
        button.textContent = fill(labels.hours ?? "{hours} h", { hours: String(option) });
        button.setAttribute("aria-pressed", String(option === hours));
        button.addEventListener("click", () => {
          this.chosen = option;
          this.pinned = undefined;
          void this.render();
        });
        return button;
      }),
    );
  }

  // --------------------------------------------------------- rule 9, T8

  private planLegend(config: TimelineConfig): LegendEntry[] {
    const labels = config.labels ?? {};
    const show = this.show(config);
    const kwh = new Intl.NumberFormat(this.hassRef!.locale.language, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const items: LegendEntry[] = [];
    if (show.has("plan")) {
      for (const load of legendItems(config.loads, this.slots)) {
        items.push({ name: load.name, color: this.colorOf(load.id), value: `${kwh.format(load.kwh)} kWh`, series: true });
      }
    }
    if (show.has("baseline") && !this.single) {
      items.push({ name: labels.other_usage ?? "", color: this.css("--secondary-text-color", "#727272"), series: true });
    }
    if (show.has("ceiling") && !this.single && this.slots.some((slot) => slot.ceilingKw !== null)) {
      items.push({ name: labels.limit ?? "", color: this.css("--error-color", "#db4437"), swatch: "line", series: true });
    }
    items.push({ name: this.priceName(), color: this.css("--primary-color", "#03a9f4"), swatch: "strip", series: true });
    return items;
  }

  private drawLegend(items: LegendEntry[]): void {
    this.els!.legend.replaceChildren(
      ...items.map((item) => {
        const button = document.createElement("button");
        button.className = "item";
        button.setAttribute("aria-pressed", String(!this.off.has(item.name)));
        const swatch =
          item.swatch === "line"
            ? `<span class="pp-swatch line" style="color:${item.color}"></span>`
            : item.swatch === "strip"
              ? `<span class="pp-swatch strip" style="background:linear-gradient(90deg, ${withAlpha(item.color, 0.28)} 50%, ${withAlpha(item.color, 0.62)} 50%)"></span>`
              : `<span class="pp-swatch" style="background:${item.color}"></span>`;
        button.innerHTML = `${swatch}<span>${escape(item.name)}</span>${item.value ? `<span class="value">${escape(item.value)}</span>` : ""}`;
        if (item.series) {
          button.addEventListener("click", () => {
            if (this.off.has(item.name)) this.off.delete(item.name);
            else this.off.add(item.name);
            this.chart?.dispatchAction({ type: "legendToggleSelect", name: item.name });
            button.setAttribute("aria-pressed", String(!this.off.has(item.name)));
          });
        } else {
          button.disabled = true;
          button.style.cursor = "default";
        }
        return button;
      }),
    );
  }

  // ------------------------------------------------ rules 10, 11, T7

  private tap(event: { offsetX: number; offsetY: number }): void {
    if (!this.chart || !this.touch || this.config?.mode === "history" || this.forecast) return;
    const [value] = this.chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]) as number[];
    const slot = this.slots.find((s) => s.start <= value! && value! < s.end);
    if (!slot) return;
    this.pinned = this.pinned === slot.start ? undefined : slot.start;
    this.drawReadout();
  }

  private drawReadout(): void {
    const els = this.els!;
    const config = this.config!;
    els.readout.hidden = !this.touch;
    if (!this.touch) return;
    const ids = config.loads.map((load) => load.id);
    const slot = this.slots.find((s) => s.start === this.pinned) ?? nextPlanned(this.slots, ids) ?? this.slots[0];
    if (!slot) {
      els.readout.replaceChildren();
      return;
    }
    const [head, sub] = this.readoutLines(slotReadout(slot, ids));
    els.readout.innerHTML = `<div class="head">${escape(head)}</div><div class="sub">${escape(sub)}</div>`;
  }

  /** Two lines: "ons 22:00–22:15 · Sum 9,41 av 10 kW" and "4 apparater · 0,73 kr/kWh · ≈ 0,82 kr". */
  private readoutLines(readout: Readout): [string, string] {
    const { labels = {} } = this.config!;
    const f = this.formats();
    const when = `${f.day.format(readout.start)} ${f.clock.format(readout.start)}–${f.clock.format(readout.end)}`;
    const sum =
      readout.limitKw === null
        ? `${f.kw.format(readout.sumKw)} kW`
        : fill(labels.sum_of_limit ?? "{sum} / {limit} kW", { sum: f.kw.format(readout.sumKw), limit: f.kw.format(readout.limitKw) });
    const parts = [fill(labels.appliance_count ?? "{n}", { n: String(readout.count) })];
    if (readout.price !== null) {
      const estimated = readout.estimated ? ` (${labels.estimated_short ?? ""})` : "";
      parts.push(`${f.number.format(readout.price)} ${f.unit}/kWh${estimated}`);
    }
    if (readout.cost !== null && readout.cost > 0) {
      parts.push(fill(labels.slot_cost ?? "≈ {cost}", { cost: f.money.format(readout.cost) }));
    }
    return [`${when} · ${sum}`, parts.join(" · ")];
  }

  // ---------------------------------------------------------- the chart

  private show(config: TimelineConfig): Set<string> {
    return new Set(config.show ?? ["plan", "baseline", "ceiling", "price"]);
  }

  /** "Pris kr/kWh" - the currency as the viewer's language writes it (G7). */
  private priceName(): string {
    return fill(this.config?.labels?.price_strip ?? "{currency}/kWh", { currency: this.formats().unit });
  }

  private colorOf(id: string): string {
    const loads = this.config!.loads;
    const index = loads.findIndex((load) => load.id === id);
    const load = loads[index];
    return load?.color ?? this.css(`--graph-color-${index + 1}`, PALETTE[index % PALETTE.length]!);
  }

  private formats() {
    const hass = this.hassRef!;
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const currency = this.config?.currency ?? "";
    return {
      zone,
      clock: new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: zone }),
      day: new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: zone }),
      midnight: new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", timeZone: zone }),
      kw: new Intl.NumberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 }),
      number: new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      money: moneyFormat(locale, currency),
      unit: currency ? currencyWord(locale, currency) : "",
    };
  }

  /** T9: x labels every 2 h on 24 h, 3 h on 12 h, 4 h on 48 h - 6 h on a narrow 48 h. */
  private labelStep(hours: number): number {
    const every = hours <= 12 ? 3 : hours <= 24 ? (this.narrow ? 4 : 2) : this.narrow ? 6 : 4;
    const plot = this.els!.plot.clientHeight - GRID.top - GRID.bottom;
    // T10: under 180 px of plot, fewer labels rather than a smaller plot.
    return (plot > 0 && plot < PLOT_NARROW_PX ? 2 : 1) * every * HOUR_MS;
  }

  /** The axes, grids and tooltip both modes share (T2, T5, T9). */
  private frame(start: number, end: number, scale: { max: number; step: number }, unit: string, labelStep: number, xLabel: (value: number) => string, strip: boolean) {
    const muted = this.css("--secondary-text-color", "#727272");
    const text = this.css("--primary-text-color", "#212121");
    const track = this.css("--pp-track", "rgba(127,127,127,.12)");
    return {
      grid: [
        { ...GRID, containLabel: false },
        { ...STRIP, height: strip ? STRIP.height : 0, containLabel: false },
      ],
      tooltip: { ...tooltipStyle((name, fallback) => this.css(name, fallback)) },
      xAxis: [
        {
          type: "time",
          min: start,
          max: end,
          minInterval: labelStep,
          maxInterval: labelStep,
          axisLine: { show: true, lineStyle: { color: withAlpha(text, 0.25) } },
          axisTick: { show: false },
          axisLabel: { color: muted, fontSize: 11, margin: 8, hideOverlap: true, rotate: 0, formatter: xLabel },
          splitLine: { show: false },
        },
        { type: "time", gridIndex: 1, min: start, max: end, show: false },
      ],
      yAxis: [
        {
          type: "value",
          name: unit,
          nameGap: 10,
          nameTextStyle: { color: muted, fontSize: 10, align: "right", padding: [0, 6, 0, 0] },
          min: 0,
          max: scale.max,
          interval: scale.step,
          axisLabel: { color: muted, fontSize: 11 },
          splitLine: { lineStyle: { color: track, type: [2, 4] } },
        },
        { type: "value", gridIndex: 1, min: 0, max: 1, show: false },
      ],
    };
  }

  private option(hass: HomeAssistant, config: TimelineConfig, slots: TimelineSlot[], hours: number): EChartsCoreOption {
    const labels = config.labels ?? {};
    const show = this.show(config);
    const single = this.single;
    const ids = config.loads.map((load) => load.id);
    const f = this.formats();
    const muted = this.css("--secondary-text-color", "#727272");
    const surface = this.css("--card-background-color", "#fff");
    const error = this.css("--error-color", "#db4437");
    const start = slots[0]!.start;
    const end = slots[slots.length - 1]!.end;
    const now = Date.now();

    // Rule 2: the bar's width is the slot's less a 2 px gap (1 px under 6 px).
    const plotPx = Math.max(100, (this.width || 600) - 32 - GRID.left - GRID.right);
    const slotPx = (plotPx * (slots[0]!.end - slots[0]!.start)) / (end - start);
    const barWidth = Math.max(1, slotPx - (slotPx < 6 ? 1 : 2));
    const drawBaseline = show.has("baseline") && (!single || config.show?.includes("baseline"));
    const drawCeiling = show.has("ceiling") && (!single || config.show?.includes("ceiling"));

    // Rule 3: one axis with the limit inside it.
    const stacks = slots.map((slot) => stackOffsets(slot, ids));
    const held = (slot: TimelineSlot) => ids.reduce((sum, id) => sum + (slot.holdKw[id] ?? 0), 0);
    const tops = stacks.map((stack, i) => (drawBaseline ? stack.total : stack.total - stack.offset) + held(slots[i]!));
    const ceilings = slots.map((slot) => slot.ceilingKw ?? 0);
    const candidates = single
      ? [Math.max(0, ...tops) * 1.3]
      : [Math.max(0, ...ceilings) * (drawCeiling ? 1.2 : 0), Math.max(0, ...tops) * 1.1];
    const scale = niceScale(Math.max(0, ...candidates.filter(Number.isFinite)));

    const series: Record<string, unknown>[] = [];
    if (drawBaseline) {
      series.push({
        name: labels.other_usage ?? "",
        type: "line",
        step: "end",
        symbol: "none",
        silent: true,
        lineStyle: { width: 1, color: muted, opacity: 0.55 },
        areaStyle: { color: muted, opacity: 0.2 },
        data: steps(slots, (slot) => slot.baselineKw),
        z: 1,
      });
      series.push({
        name: OFFSET,
        type: "bar",
        stack: "total",
        silent: true,
        barWidth,
        itemStyle: { color: "transparent" },
        data: slots.map((slot, i) => [(slot.start + slot.end) / 2, stacks[i]!.offset]),
        z: 2,
      });
    }
    if (show.has("plan")) {
      config.loads.forEach((load, index) => {
        series.push({
          name: load.name,
          type: "bar",
          stack: "total",
          barWidth,
          barGap: "-100%",
          itemStyle: { color: this.colorOf(load.id), borderColor: surface, borderWidth: slotPx >= 6 ? 1 : 0 },
          data: slots.map((slot, i) => ({
            value: [(slot.start + slot.end) / 2, stacks[i]!.loads[index]],
            itemStyle: stacks[i]!.top === index ? { borderRadius: [2, 2, 0, 0] } : undefined,
          })),
          z: 3,
        });
      });
    }
    if (show.has("plan")) {
      // Holding a thermal store's setpoint: each load's colour, lighter, over its runs (D-0501).
      for (const load of config.loads) {
        if (!slots.some((slot) => (slot.holdKw[load.id] ?? 0) > 0)) continue;
        series.push({
          name: load.name,
          type: "bar",
          stack: "total",
          barWidth,
          barGap: "-100%",
          itemStyle: { color: withAlpha(this.colorOf(load.id), 0.45), borderColor: surface, borderWidth: slotPx >= 6 ? 1 : 0 },
          data: slots.map((slot) => [(slot.start + slot.end) / 2, slot.holdKw[load.id] ?? 0]),
          z: 3,
        });
      }
    }
    if (show.has("production") && slots.some((slot) => slot.productionKw !== null)) {
      series.push({
        name: "production",
        type: "line",
        step: "end",
        symbol: "none",
        lineStyle: { width: 0 },
        areaStyle: { color: this.css("--energy-solar-color", "#ff9800"), opacity: 0.4 },
        data: steps(slots, (slot) => slot.productionKw),
      });
    }
    if (drawCeiling && slots.some((slot) => slot.ceilingKw !== null)) {
      const limit = Math.max(...ceilings);
      series.push({
        name: labels.limit ?? "",
        type: "line",
        step: "end",
        symbol: "none",
        lineStyle: { type: "dashed", width: 1.5, color: error },
        itemStyle: { color: error },
        endLabel: {
          show: true,
          formatter: fill(labels.limit_value ?? "{kw} kW", { kw: f.kw.format(limit) }),
          color: muted,
          fontSize: 11,
          align: "right",
          offset: [-4, -10],
        },
        data: steps(slots, (slot) => slot.ceilingKw),
        z: 4,
      });
    }

    // The estimated band on the main grid; the markers are `graphic` (T3).
    series.push({
      name: MARKS,
      type: "line",
      data: [],
      silent: true,
      markArea: {
        silent: true,
        itemStyle: { color: muted, opacity: 0.03 },
        label: { color: muted, position: "insideTop", fontSize: 10 },
        data: estimatedRanges(slots).map(([from, to]) => [{ xAxis: from, name: labels.estimated_prices ?? "" }, { xAxis: to }]),
      },
    });
    this.markers = [];
    if (now >= start && now < end) this.markers.push({ at: now, kind: "now", text: labels.now ?? "" });
    for (const midnight of midnights(start, end, f.zone)) {
      this.markers.push({ at: midnight, kind: "midnight", text: f.midnight.format(midnight) });
    }
    const deadline = single ? this.deadline(hass, config) : null;
    if (deadline !== null && deadline > start && deadline <= end) {
      this.markers.push({ at: deadline, kind: "deadline", text: fill(labels.deadline ?? "{time}", { time: f.clock.format(deadline) }) });
    }

    // Rule 5, T4: the price strip, a grid of its own sharing the time axis.
    series.push(this.strip(priceRuns(slots), f.number));
    this.stripUnit = f.unit;

    const find = (value: number) => slots.find((slot) => slot.start <= value && value < slot.end);
    const frame = this.frame(start, end, scale, "kW", this.labelStep(hours), (value) => f.clock.format(value), true);
    return {
      animation: false,
      textStyle: { color: this.css("--primary-text-color", "#212121"), fontFamily: this.css("--ha-font-family-body", "Roboto, sans-serif") },
      legend: { show: false, data: series.map((s) => s.name).filter((name) => name !== OFFSET && name !== MARKS) },
      ...frame,
      tooltip: {
        ...frame.tooltip,
        show: !this.touch,
        trigger: "axis",
        triggerOn: this.touch ? "click" : "mousemove",
        axisPointer: { type: "line", lineStyle: { color: this.css("--divider-color", "rgba(0,0,0,.12)") } },
        formatter: (params: Array<{ axisValue: number }>) => {
          const slot = params[0] ? find(params[0].axisValue) : undefined;
          return slot ? this.tooltip(slot) : "";
        },
      },
      series,
    };
  }

  /** T4: one rect per run of equal price, 1 px gaps, the outer corners rounded, the price inside when it fits. */
  private strip(runs: PriceRun[], number: Intl.NumberFormat): Record<string, unknown> {
    const primary = this.css("--primary-color", "#03a9f4");
    const text = this.css("--primary-text-color", "#212121");
    const hatch = this.hatch(this.css("--secondary-text-color", "#727272"));
    const last = runs.length - 1;
    return {
      name: this.priceName(),
      type: "custom",
      xAxisIndex: 1,
      yAxisIndex: 1,
      silent: true,
      data: runs.map((run, i) => [run.start, run.end, run.price, run.alpha, run.estimated ? 1 : 0, i]),
      renderItem: (_params: unknown, api: { value(i: number): number; coord(point: number[]): number[] }) => {
        const [x0, y0] = api.coord([api.value(0), 1]);
        const [x1, y1] = api.coord([api.value(1), 0]);
        const index = api.value(5);
        const width = Math.max(0, x1! - x0! - (index === last ? 0 : 1));
        const r = [index === 0 ? 6 : 0, index === last ? 6 : 0, index === last ? 6 : 0, index === 0 ? 6 : 0];
        const shape = { x: x0!, y: y0!, width, height: y1! - y0!, r };
        const children: Record<string, unknown>[] = [{ type: "rect", shape, style: { fill: withAlpha(primary, api.value(3)) } }];
        if (api.value(4) && hatch) children.push({ type: "rect", shape, style: { fill: hatch } });
        if (width >= 34) {
          children.push({
            type: "text",
            style: {
              text: number.format(api.value(2)),
              x: x0! + width / 2,
              y: y0! + (y1! - y0!) / 2,
              align: "center",
              verticalAlign: "middle",
              fontSize: 11,
              fontWeight: 500,
              fill: text,
            },
          });
        }
        return { type: "group", children };
      },
    };
  }

  /** F8: draw the markers once the chart has finished its first paint, not only on a resize. */
  private markersOnFinish(): void {
    const chart = this.chart;
    if (!chart) return;
    chart.off("finished");
    chart.on("finished", () => {
      chart.off("finished");
      this.drawMarkers();
    });
  }

  /** T3: "Nå", midnight and the deadline as horizontal labels, and the strip's unit (T4), from the grid's pixels. */
  private drawMarkers(): void {
    const chart = this.chart;
    if (!chart || !this.els || this.forecast) return;
    const height = this.els.plot.clientHeight;
    const width = this.els.plot.clientWidth;
    if (!height || !width) return;
    const text = this.css("--primary-text-color", "#212121");
    const background = this.css("--primary-background-color", "#fafafa");
    const muted = this.css("--secondary-text-color", "#727272");
    const divider = this.css("--divider-color", "rgba(0,0,0,.12)");
    const warning = this.css("--warning-color", "#ffa600");
    const top = GRID.top;
    const axis = height - GRID.bottom;
    const elements: Record<string, unknown>[] = [];
    for (const marker of this.markers) {
      // An axis finder converts ONE value and returns a number; an array made it NaN (iteration 4, F8).
      const p = chart.convertToPixel({ xAxisIndex: 0 }, marker.at) as number | number[];
      const x = Array.isArray(p) ? p[0] : p;
      if (typeof x !== "number" || !Number.isFinite(x)) continue;
      if (marker.kind === "now") {
        elements.push(
          { type: "line", silent: true, z: 50, shape: { x1: x, y1: Math.max(2, top - 10), x2: x, y2: axis }, style: { stroke: text, lineWidth: 1.5 } },
          { type: "rect", silent: true, z: 51, shape: { x, y: Math.max(2, top - 26), width: 28, height: 16, r: 4 }, style: { fill: text } },
          {
            type: "text", silent: true, z: 52, x: x + 14, y: Math.max(2, top - 26) + 8,
            style: { text: marker.text, fill: background, fontSize: 10, fontWeight: 500, align: "center", verticalAlign: "middle" },
          },
        );
      } else if (marker.kind === "midnight") {
        elements.push(
          { type: "line", silent: true, z: 40, shape: { x1: x, y1: top - 4, x2: x, y2: axis }, style: { stroke: divider, lineWidth: 1 } },
          {
            type: "text", silent: true, z: 41, x: x + 6, y: top - 14,
            style: { text: marker.text, fill: muted, fontSize: 11, align: "left", verticalAlign: "middle" },
          },
        );
      } else {
        const left = width - GRID.right - x < 100;
        elements.push(
          { type: "line", silent: true, z: 45, shape: { x1: x, y1: top, x2: x, y2: axis }, style: { stroke: warning, lineWidth: 1.5, lineDash: [4, 3] } },
          {
            type: "text", silent: true, z: 46, x: left ? x - 6 : x + 6, y: top + 8,
            style: { text: marker.text, fill: text, fontSize: 11, align: left ? "right" : "left", verticalAlign: "middle" },
          },
        );
      }
    }
    if (this.stripUnit) {
      elements.push({
        type: "text", silent: true, x: STRIP.left - 6, y: height - STRIP.bottom - STRIP.height / 2,
        style: { text: this.stripUnit, fill: muted, fontSize: 10, align: "right", verticalAlign: "middle" },
      });
    }
    chart.setOption({ graphic: elements }, { replaceMerge: ["graphic"] });
  }

  /** A 45° hatch for provisional prices (rule 5), as a canvas pattern ECharts can fill with. */
  private hatch(color: string): { image: HTMLCanvasElement; repeat: string } | null {
    const canvas = document.createElement("canvas");
    canvas.width = 6;
    canvas.height = 6;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.strokeStyle = withAlpha(color, 0.5);
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(0, 6);
    context.lineTo(6, 0);
    context.stroke();
    return { image: canvas, repeat: "repeat" };
  }

  private deadline(hass: HomeAssistant, config: TimelineConfig): number | null {
    const id = config.entities.deadline;
    const value = id ? hass.states[id]?.attributes.deadline : undefined;
    const time = typeof value === "string" ? Date.parse(value) : Number.NaN;
    return Number.isNaN(time) ? null : time;
  }

  /** Rule 10: the slot, a row per load with power, the rest of the house, the sum, the price, the cost. */
  private tooltip(slot: TimelineSlot): string {
    const config = this.config!;
    const labels = config.labels ?? {};
    const f = this.formats();
    const ids = config.loads.map((load) => load.id);
    const readout = slotReadout(slot, ids);
    const dot = (color: string) =>
      `<span style="display:inline-block;width:8px;height:8px;border-radius:3px;background:${color};margin-right:8px"></span>`;
    const rows = [`<b style="font-weight:500">${escape(`${f.day.format(slot.start)} ${f.clock.format(slot.start)}–${f.clock.format(slot.end)}`)}</b>`];
    for (const load of config.loads) {
      const kw = slot.loadKw[load.id] ?? 0;
      if (kw > 0) rows.push(`${dot(this.colorOf(load.id))}${escape(load.name)}: ${f.kw.format(kw)} kW`);
    }
    if (slot.baselineKw !== null && !this.single) {
      rows.push(`${escape(labels.other_usage ?? "")}: ${f.kw.format(slot.baselineKw)} kW`);
    }
    const [head, sub] = this.readoutLines(readout);
    const divider = this.css("--divider-color", "rgba(0,0,0,.12)");
    rows.push(`<div style="border-top:1px solid ${divider};margin:4px 0"></div>`);
    rows.push(escape(head.split(" · ").slice(1).join(" · ")));
    rows.push(escape(sub.split(" · ").slice(1).join(" · ")));
    return rows.filter(Boolean).join("<br>");
  }

  // --------------------------------------------------------- mode: history

  private async renderHistory(): Promise<void> {
    const hass = this.hassRef;
    const config = this.config;
    const period = this.period;
    if (!hass || !config || !period) return;
    const els = this.shell();
    this.sizePlot();
    (els.toggle.parentElement as HTMLElement).hidden = true;
    els.readout.hidden = true;
    const labels = config.labels ?? {};
    const zone = timeZone(hass);
    const grain = statisticsPeriod(period) === "hour" ? "hour" : "day";
    const sources = config.grid_entities ?? [];
    const e = config.entities;
    const month = {
      start: new Date(period.start.getFullYear(), period.start.getMonth(), 1),
      end: new Date(period.start.getFullYear(), period.start.getMonth() + 1, 1),
    };
    const hourly = grain === "hour";
    // The price over the day: the forecast's own slots while it covers the day,
    // else the statistics from an hour before, so a first partial hour can be dropped.
    const early = { start: new Date(period.start.getTime() - HOUR_MS), end: period.end };
    const forecast = hourly && e.price_forecast ? ((hass.states[e.price_forecast]?.attributes.slots as PriceSlot[] | undefined) ?? []) : [];
    const covered = forecast.length > 0 && Date.parse(forecast[0]!.start) <= period.start.getTime();
    const [usage, ceilingStats, priceStats, peaks, ranking] = await Promise.all([
      fetchStatistics(hass, period, sources, ["change"], grain),
      hourly && e.ceiling ? fetchStatistics(hass, period, [e.ceiling], ["mean"], "hour").catch(() => ({})) : Promise.resolve({}),
      hourly && e.price && !covered ? fetchStatistics(hass, early, [e.price], ["mean"], "hour").catch(() => ({})) : Promise.resolve({}),
      !hourly && e.window_used ? fetchStatistics(hass, period, [e.window_used], ["max"], "day").catch(() => ({})) : Promise.resolve({}),
      monthRanking(hass, month, { used: e.window_used, grid: sources, advice: e.advice ? hass.states[e.advice]?.attributes.items : undefined }, zone),
    ]);
    if (this.period !== period || !this.isConnected) return;
    const hours = gridHours(usage, sources, (id) => kwhScale(hass, id));
    const empty = hours.length === 0;
    els.chart.hidden = empty;
    els.message.hidden = !empty;
    els.message.textContent = empty ? withoutDate(labels.collecting ?? "") : "";
    if (empty) {
      els.legend.replaceChildren();
      return;
    }
    if (!(await this.ensureChart())) return;
    const f = this.formats();
    const step = hourly ? HOUR_MS : 86_400_000;
    const start = period.start.getTime();
    const end = period.end.getTime();
    const muted = this.css("--secondary-text-color", "#727272");
    const text = this.css("--primary-text-color", "#212121");
    const error = this.css("--error-color", "#db4437");
    const warning = this.css("--warning-color", "#ffa600");
    const gridColor = this.css("--energy-grid-consumption-color", "#488fc2");
    const shades = [gridColor, withAlpha(gridColor, 0.6), withAlpha(gridColor, 0.35)];
    const plotPx = Math.max(100, (this.width || 600) - 32 - GRID.left - GRID.right);
    const barWidth = Math.max(1, (plotPx * step) / (end - start) - 2);
    const legend: LegendEntry[] = [];
    const series: Record<string, unknown>[] = sources.map((id, index) => {
      const rows = new Map((usage[id] ?? []).map((row) => [row.start, (row.change ?? 0) * kwhScale(hass, id)]));
      const name = String(hass.states[id]?.attributes.friendly_name ?? id);
      legend.push({ name, color: shades[index % shades.length]!, series: true });
      return {
        name,
        type: "bar",
        stack: "grid",
        barWidth,
        itemStyle: { color: shades[index % shades.length] },
        data: hours.map((row) => [row.start + step / 2, rows.get(row.start) ?? 0]),
      };
    });
    const top = Math.max(...hours.map((row) => row.max ?? 0));
    const markLines: Record<string, unknown>[] = [];
    const markPoints: Record<string, unknown>[] = [];
    let ceilingMax = 0;
    this.markers = [];
    this.stripUnit = "";
    if (hourly) {
      // D3: the limit across the whole day - the ceiling's statistics where they
      // exist, the ceiling now elsewhere - in kWh per hour, as the bars are.
      const ceilingRows = e.ceiling ? ((ceilingStats as Record<string, StatRow[]>)[e.ceiling] ?? []) : [];
      const now = e.ceiling ? Number(hass.states[e.ceiling]?.state) : Number.NaN;
      const byHour = new Map(ceilingRows.filter((row) => row.mean != null).map((row) => [row.start, row.mean!]));
      const fallback = Number.isFinite(now) ? now : null;
      const limitAt = (t: number) => byHour.get(t) ?? fallback;
      const points = hours.map((row) => [row.start, limitAt(row.start)] as [number, number | null]);
      if (points.some(([, v]) => v !== null)) {
        const last = points[points.length - 1]!;
        ceilingMax = Math.max(...points.map(([, v]) => v ?? 0));
        const name = labels.limit ?? "";
        series.push({
          name,
          type: "line",
          step: "end",
          symbol: "none",
          lineStyle: { type: "dashed", width: 1.5, color: error },
          endLabel: { show: true, formatter: fill(labels.limit_value_kwh ?? "{kw}", { kw: f.kw.format(ceilingMax) }), color: muted, fontSize: 11, align: "right", offset: [-4, -10] },
          data: [...points, [Math.max(last[0] + step, end), last[1]]],
        });
        legend.push({ name, color: error, swatch: "line", series: true });
      }
      // The month's third-highest day: a thin amber line (D3).
      const third = [...ranking].sort((a, b) => b[1] - a[1])[2];
      if (third) {
        markLines.push({
          yAxis: third[1],
          lineStyle: { color: warning, width: 1, type: "dashed" },
          label: { formatter: fill(labels.third_threshold ?? "{kw}", { kw: f.number.format(third[1]) }), position: "insideStartTop", color: warning, fontSize: 11 },
        });
        legend.push({ name: labels.top3_threshold ?? "", color: warning, swatch: "line" });
      }
      // The day's highest hour: an amber dot 4 px above its bar, named beside it.
      const best = hours.reduce((a, b) => ((b.max ?? 0) > (a.max ?? 0) ? b : a));
      markPoints.push({
        coord: [best.start + step / 2, best.max],
        symbol: "circle",
        symbolSize: 7,
        symbolOffset: [0, -8],
        itemStyle: { color: warning },
        label: { show: true, position: "right", formatter: fill(labels.highest_hour ?? "{kwh}", { kwh: f.number.format(best.max ?? 0) }), color: muted, fontSize: 11 },
      });
      // The price strip: the forecast's slots, else hourly means without a first partial hour.
      const priceSlots: TimelineSlot[] = covered
        ? forecast
            .map((slot) => ({ start: Date.parse(slot.start), end: Date.parse(slot.end), price: Number(slot.total), estimated: slot.confidence !== "known" }))
            .filter((slot) => slot.end > start && slot.start < end)
            .map((slot) => ({ ...slot, hours: (slot.end - slot.start) / HOUR_MS, ceilingKw: null, baselineKw: null, productionKw: null, loadKw: {}, loadKwh: {}, holdKw: {} }))
        : (() => {
            const rows = e.price ? ((priceStats as Record<string, StatRow[]>)[e.price] ?? []) : [];
            const first = rows[0];
            // A statistic with no row the hour before began inside its first hour: that mean is partial.
            const kept = first && first.start >= start ? rows.slice(1) : rows.filter((row) => row.start >= start);
            return kept.map((row) => ({
              start: row.start, end: row.end, hours: 1, price: row.mean ?? null, estimated: false,
              ceilingKw: null, baselineKw: null, productionKw: null, loadKw: {}, loadKwh: {}, holdKw: {},
            }));
          })();
      if (priceSlots.length) {
        series.push(this.strip(priceRuns(priceSlots), f.number));
        this.stripUnit = f.unit;
        legend.push({ name: this.priceName(), color: this.css("--primary-color", "#03a9f4"), swatch: "strip", series: true });
      }
      const now2 = Date.now();
      if (now2 >= start && now2 < end) this.markers.push({ at: now2, kind: "now", text: labels.now ?? "" });
    } else {
      // A longer range: an amber dot over each day that counts.
      const peakRows = e.window_used ? ((peaks as Record<string, StatRow[]>)[e.window_used] ?? []) : [];
      const dayPeaks: DayPeak[] = [...ranking, ...peakRows.filter((row) => row.max != null).map((row) => [dayKey(row.start, zone), row.max!] as DayPeak)];
      const counting = countingDays(dayPeaks);
      for (const row of hours) {
        if (counting.has(dayKey(row.start, zone))) {
          markPoints.push({ coord: [row.start + step / 2, row.max], symbol: "circle", symbolSize: 6, symbolOffset: [0, -6], itemStyle: { color: warning }, label: { show: false } });
        }
      }
    }
    // The marks ride on the top source, so they sit over the stack.
    const lastSource = sources.length - 1;
    if (series[lastSource]) {
      series[lastSource] = { ...series[lastSource], markPoint: { silent: true, data: markPoints }, markLine: { silent: true, symbol: "none", data: markLines } };
    }
    const scale = niceScale(Math.max(top * 1.15, ceilingMax * 1.2));
    const hasStrip = series.some((item) => item.type === "custom");
    const dayLabel = new Intl.DateTimeFormat(hass.locale.language, { day: "numeric", month: "short", timeZone: zone });
    const frame = this.frame(start, end, scale, "kWh", hourly ? 2 * HOUR_MS : 86_400_000 * Math.max(1, Math.ceil((end - start) / 86_400_000 / 10)), (value) => (hourly ? f.clock.format(value) : dayLabel.format(value)), hasStrip);
    this.chart!.setOption(
      {
        animation: false,
        textStyle: { color: text, fontFamily: this.css("--ha-font-family-body", "Roboto, sans-serif") },
        legend: { show: false, data: legend.filter((item) => item.series).map((item) => item.name) },
        ...frame,
        tooltip: {
          ...frame.tooltip,
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: this.css("--divider-color", "rgba(0,0,0,.12)") } },
          valueFormatter: (value: number) => `${f.number.format(value)} kWh`,
        },
        series,
      },
      { notMerge: true },
    );
    for (const name of this.off) this.chart!.dispatchAction({ type: "legendUnSelect", name });
    this.chart!.resize();
    this.markersOnFinish();
    this.drawMarkers();
    this.drawLegend(legend);
  }
}
