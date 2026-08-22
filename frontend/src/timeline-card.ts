// `powerplan-timeline-card` (D12 §5.2): the next 12–48 hours on one kW axis -
// each appliance's planned power stacked on the rest of the house, so a bar's
// top is the total the household compares with the dashed limit - and the
// price as a strip under the time axis. ECharts at HA's own major version,
// themed from HA's CSS variables. The rule numbers are D12 §5.2's. `mode:
// history` (D12 §5.7) draws the past instead, on the History view's picker:
// grid usage per hour or per day against the limit, the days that count.

import type { ECharts, EChartsCoreOption } from "echarts/core";

import { fetchStatistics, followPeriod, type Period, statisticsPeriod, type StatRow } from "./energy";
import { cssVar, type HomeAssistant, timeZone } from "./ha";
import {
  countingDays,
  type DayPeak,
  dayKey,
  estimatedRanges,
  legendItems,
  midnights,
  nextPlanned,
  niceScale,
  nthHighest,
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
  };
  show?: string[];
  currency?: string;
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
/** The price strip's height and the room under it for the time labels (rule 5). */
const STRIP_PX = 22;
const LABELS_PX = 22;
/** The y-axis labels' room at the left, for the bars' width. */
const AXIS_PX = 36;
const OFFSET = "\u0000offset";
const MARKS = "\u0000marks";

const fill = (text: string, values: Record<string, string>) =>
  text.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);

const escape = (value: string) =>
  value.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

export class PowerplanTimelineCard extends HTMLElement {
  private config?: TimelineConfig;
  private hassRef?: HomeAssistant;
  private chart?: ECharts;
  private els?: {
    toggle: HTMLDivElement;
    chart: HTMLDivElement;
    readout: HTMLDivElement;
    legend: HTMLDivElement;
    message: HTMLDivElement;
  };
  private key: unknown[] = [];
  private resize?: ResizeObserver;
  private width = 0;
  /** The household's toggle; never written to the config (rule 1). */
  private chosen?: number;
  private pinned?: number;
  private off = new Set<string>();
  private slots: TimelineSlot[] = [];

  private period?: Period;
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
      const width = entries[0]?.contentRect.width ?? 0;
      if (Math.abs(width - this.width) < 1) return;
      this.width = width;
      this.chart?.resize();
      if (this.config?.mode === "history") void this.renderHistory();
      else void this.render();
    });
    this.resize.observe(this);
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
  }

  public getCardSize(): number {
    return 7;
  }

  public getGridOptions(): Record<string, number> {
    return { columns: 12, rows: 7, min_rows: 5, min_columns: 6 };
  }

  private get single(): boolean {
    return this.config?.loads.length === 1 && Boolean(this.config.entities.deadline);
  }

  private get touch(): boolean {
    const coarse = typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches;
    return coarse || (this.width > 0 && this.width < (this.config?.narrow_width ?? 500));
  }

  private shell(): NonNullable<PowerplanTimelineCard["els"]> {
    if (this.els) return this.els;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; height: 100%; }
        ha-card { height: 100%; box-sizing: border-box; padding: 8px 12px 8px;
                  display: flex; flex-direction: column; gap: 4px; }
        .toggle { display: flex; justify-content: flex-end; gap: 4px; }
        .toggle button { font: inherit; font-size: 12px; min-width: 44px; min-height: 28px;
                         border: 1px solid var(--divider-color); border-radius: 14px; cursor: pointer;
                         background: none; color: var(--secondary-text-color); }
        .toggle button[aria-pressed="true"] { background: var(--primary-color);
                         border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        .plot { position: relative; flex: 1; min-height: 200px; }
        .chart { position: absolute; inset: 0; }
        .message { position: absolute; inset: 0 0 ${STRIP_PX + LABELS_PX}px; display: flex;
                   align-items: center; justify-content: center; text-align: center;
                   color: var(--secondary-text-color); pointer-events: none; }
        .readout { min-height: 42px; font-size: 13px; line-height: 1.5; color: var(--primary-text-color); }
        .readout .sub { color: var(--secondary-text-color); }
        .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px;
                  color: var(--primary-text-color); }
        .legend button { font: inherit; display: inline-flex; align-items: center; gap: 6px;
                         background: none; border: 0; padding: 2px 0; cursor: pointer; color: inherit; }
        .legend button[aria-pressed="false"] { opacity: 0.4; }
        .swatch { width: 12px; height: 8px; border-radius: 2px; flex: none; }
        .kwh { color: var(--secondary-text-color); }
      </style>
      <ha-card>
        <div class="toggle"></div>
        <div class="plot"><div class="chart"></div><div class="message" hidden></div></div>
        <div class="readout" hidden></div>
        <div class="legend"></div>
      </ha-card>`;
    this.els = {
      toggle: root.querySelector(".toggle") as HTMLDivElement,
      chart: root.querySelector(".chart") as HTMLDivElement,
      readout: root.querySelector(".readout") as HTMLDivElement,
      legend: root.querySelector(".legend") as HTMLDivElement,
      message: root.querySelector(".message") as HTMLDivElement,
    };
    return this.els;
  }

  private async render(): Promise<void> {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    const els = this.shell();
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
    const idle = !empty && this.single && legendItems(config.loads, this.slots).length === 0;
    els.message.hidden = !(empty || idle);
    els.message.textContent = empty ? (labels.no_plan ?? "") : idle ? (labels.no_run ?? "") : "";
    if (empty) {
      els.readout.hidden = true;
      els.legend.replaceChildren();
      return;
    }
    if (!this.chart) {
      const { echarts } = await import("./chart");
      if (this.chart || !this.isConnected) return;
      this.chart = echarts.init(els.chart, undefined, { renderer: "canvas" });
      this.chart.getZr().on("click", (event: { offsetX: number; offsetY: number }) => this.tap(event));
    }
    this.chart.setOption(this.option(hass, config, this.slots, hours), { notMerge: true });
    for (const name of this.off) this.chart.dispatchAction({ type: "legendUnSelect", name });
    this.drawLegend(config);
    this.drawReadout();
  }

  // ------------------------------------------------------------------ rule 1

  private drawToggle(hours: number): void {
    const options = this.config?.hours_options ?? [];
    const labels = this.config?.labels ?? {};
    const toggle = this.els!.toggle;
    toggle.hidden = options.length === 0;
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

  // ------------------------------------------------------------------ rule 9

  private drawLegend(config: TimelineConfig): void {
    const labels = config.labels ?? {};
    const show = this.show(config);
    const kwh = new Intl.NumberFormat(this.hassRef!.locale.language, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    const items: Array<{ name: string; color: string; extra?: string; dashed?: boolean }> = [];
    if (show.has("plan")) {
      for (const load of legendItems(config.loads, this.slots)) {
        items.push({ name: load.name, color: this.colorOf(load.id), extra: `${kwh.format(load.kwh)} kWh` });
      }
    }
    if (show.has("baseline")) {
      items.push({ name: labels.other_usage ?? "", color: cssVar(this, "--secondary-text-color", "#727272") });
    }
    if (show.has("ceiling") && this.slots.some((slot) => slot.ceilingKw !== null)) {
      items.push({ name: labels.limit ?? "", color: cssVar(this, "--error-color", "#db4437"), dashed: true });
    }
    items.push({ name: this.priceName(config), color: cssVar(this, "--primary-color", "#03a9f4") });
    this.els!.legend.replaceChildren(
      ...items.map((item) => {
        const button = document.createElement("button");
        button.setAttribute("aria-pressed", String(!this.off.has(item.name)));
        const swatch = item.dashed
          ? `border-top: 2px dashed ${item.color}; height: 0; border-radius: 0;`
          : `background: ${item.color};`;
        button.innerHTML = `<span class="swatch" style="${swatch}"></span>${escape(item.name)}${
          item.extra ? ` <span class="kwh">${escape(item.extra)}</span>` : ""
        }`;
        button.addEventListener("click", () => {
          if (this.off.has(item.name)) this.off.delete(item.name);
          else this.off.add(item.name);
          this.chart?.dispatchAction({ type: "legendToggleSelect", name: item.name });
          button.setAttribute("aria-pressed", String(!this.off.has(item.name)));
        });
        return button;
      }),
    );
  }

  // ------------------------------------------------------------ rules 10, 11

  private tap(event: { offsetX: number; offsetY: number }): void {
    if (!this.chart || !this.touch) return;
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
    const slot =
      this.slots.find((s) => s.start === this.pinned) ?? nextPlanned(this.slots, ids) ?? this.slots[0];
    if (!slot) {
      els.readout.replaceChildren();
      return;
    }
    const [head, sub] = this.readoutLines(slotReadout(slot, ids));
    els.readout.innerHTML = `<div>${escape(head)}</div><div class="sub">${escape(sub)}</div>`;
  }

  /** Two lines: "ons 22:00–22:15 · Sum 9,83 av 10 kW" and "4 apparater · 0,73 kr/kWh · ≈ 0,89 kr". */
  private readoutLines(readout: Readout): [string, string] {
    const { labels = {}, currency = "" } = this.config!;
    const f = this.formats();
    const when = `${f.day.format(readout.start)} ${f.clock.format(readout.start)}–${f.clock.format(readout.end)}`;
    const sum =
      readout.limitKw === null
        ? `${f.kw.format(readout.sumKw)} kW`
        : fill(labels.sum_of_limit ?? "{sum} / {limit} kW", {
            sum: f.kw.format(readout.sumKw),
            limit: f.kw.format(readout.limitKw),
          });
    const parts = [fill(labels.appliance_count ?? "{n}", { n: String(readout.count) })];
    if (readout.price !== null) {
      const estimated = readout.estimated ? ` (${labels.estimated_short ?? ""})` : "";
      parts.push(`${f.money.format(readout.price)} ${currency}/kWh${estimated}`);
    }
    if (readout.cost !== null && readout.cost > 0) {
      parts.push(fill(labels.slot_cost ?? "≈ {cost} {currency}", { cost: f.money.format(readout.cost), currency }));
    }
    return [`${when} · ${sum}`, parts.join(" · ")];
  }

  // ---------------------------------------------------------------- the chart

  private show(config: TimelineConfig): Set<string> {
    return new Set(config.show ?? ["plan", "baseline", "ceiling", "price"]);
  }

  private priceName(config: TimelineConfig): string {
    return fill(config.labels?.price_strip ?? "{currency}/kWh", { currency: config.currency ?? "" });
  }

  private colorOf(id: string): string {
    const loads = this.config!.loads;
    const index = loads.findIndex((load) => load.id === id);
    const load = loads[index];
    return load?.color ?? cssVar(this, `--graph-color-${index + 1}`, PALETTE[index % PALETTE.length]!);
  }

  private formats() {
    const hass = this.hassRef!;
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    return {
      zone,
      clock: new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: zone }),
      day: new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: zone }),
      midnight: new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", timeZone: zone }),
      kw: new Intl.NumberFormat(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 }),
      money: new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    };
  }

  private option(
    hass: HomeAssistant,
    config: TimelineConfig,
    slots: TimelineSlot[],
    hours: number,
  ): EChartsCoreOption {
    const labels = config.labels ?? {};
    const show = this.show(config);
    const single = this.single;
    const ids = config.loads.map((load) => load.id);
    const f = this.formats();
    const text = cssVar(this, "--primary-text-color", "#212121");
    const muted = cssVar(this, "--secondary-text-color", "#727272");
    const divider = cssVar(this, "--divider-color", "rgba(0,0,0,.12)");
    const surface = cssVar(this, "--card-background-color", "#fff");
    const error = cssVar(this, "--error-color", "#db4437");
    const warning = cssVar(this, "--warning-color", "#ffa600");
    const primary = cssVar(this, "--primary-color", "#03a9f4");
    const background = cssVar(this, "--primary-background-color", "#fafafa");
    const start = slots[0]!.start;
    const end = slots[slots.length - 1]!.end;
    const now = Date.now();
    const narrow = this.width > 0 && this.width < (config.narrow_width ?? 500);

    // Rule 2: the bar's width is the slot's less a 2 px gap (1 px under 6 px).
    const plotPx = Math.max(100, (this.width || 600) - AXIS_PX - 24);
    const slotPx = (plotPx * (slots[0]!.end - slots[0]!.start)) / (end - start);
    const barWidth = Math.max(1, slotPx - (slotPx < 6 ? 1 : 2));
    const drawBaseline = show.has("baseline") && (!single || config.show?.includes("baseline"));
    const drawCeiling = show.has("ceiling") && (!single || config.show?.includes("ceiling"));

    // Rule 3: one axis with the limit inside it.
    const stacks = slots.map((slot) => stackOffsets(slot, ids));
    const tops = stacks.map((stack) => (drawBaseline ? stack.total : stack.total - stack.offset));
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
    if (show.has("production") && slots.some((slot) => slot.productionKw !== null)) {
      series.push({
        name: "production",
        type: "line",
        step: "end",
        symbol: "none",
        lineStyle: { width: 0 },
        areaStyle: { color: cssVar(this, "--energy-solar-color", "#ff9800"), opacity: 0.4 },
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

    // Rules 6, 7, 12 and the estimated band: marks on a series of their own.
    const marks: Record<string, unknown>[] = [];
    if (now >= start && now < end) {
      marks.push({
        xAxis: now,
        lineStyle: { color: text, width: 1.5, type: "solid" },
        label: {
          formatter: labels.now ?? "",
          position: "insideEndTop",
          color: background,
          backgroundColor: text,
          padding: [2, 6],
          borderRadius: 3,
          fontSize: 11,
        },
      });
    }
    for (const midnight of midnights(start, end, f.zone)) {
      marks.push({
        xAxis: midnight,
        lineStyle: { color: divider, width: 1, type: "solid" },
        label: { formatter: f.midnight.format(midnight), position: "insideEndTop", color: muted, fontSize: 11 },
      });
    }
    const deadline = single ? this.deadline(hass, config) : null;
    if (deadline !== null && deadline > start && deadline <= end) {
      const nearEdge = ((end - deadline) / (end - start)) * plotPx < 100;
      marks.push({
        xAxis: deadline,
        lineStyle: { color: warning, width: 1.5, type: "dashed" },
        label: {
          formatter: fill(labels.deadline ?? "{time}", { time: f.clock.format(deadline) }),
          position: nearEdge ? "insideEndBottom" : "insideEndTop",
          color: warning,
          fontSize: 11,
        },
      });
    }
    series.push({
      name: MARKS,
      type: "line",
      data: [],
      silent: true,
      markLine: { silent: true, symbol: "none", animation: false, data: marks },
      markArea: {
        silent: true,
        itemStyle: { color: muted, opacity: 0.03 },
        label: { color: muted, position: "insideTop", fontSize: 10 },
        data: estimatedRanges(slots).map(([from, to]) => [
          { xAxis: from, name: labels.estimated_prices ?? "" },
          { xAxis: to },
        ]),
      },
    });

    // Rule 5: the price strip, a grid of its own sharing the time axis.
    series.push(this.strip(priceRuns(slots), primary, muted, text, f.money));

    const find = (value: number) => slots.find((slot) => slot.start <= value && value < slot.end);
    const every = (narrow ? 6 : 3) * HOUR_MS;
    return {
      animation: false,
      textStyle: { color: text },
      legend: { show: false, data: series.map((s) => s.name).filter((name) => name !== OFFSET && name !== MARKS) },
      grid: [
        { left: 4, right: 8, top: 22, bottom: STRIP_PX + LABELS_PX + 2, containLabel: true },
        { left: 4, right: 8, bottom: 0, height: STRIP_PX, containLabel: true },
      ],
      tooltip: {
        show: !this.touch,
        trigger: "axis",
        triggerOn: this.touch ? "click" : "mousemove",
        axisPointer: { type: "line", lineStyle: { color: divider } },
        formatter: (params: Array<{ axisValue: number }>) => {
          const slot = params[0] ? find(params[0].axisValue) : undefined;
          return slot ? this.tooltip(slot) : "";
        },
      },
      xAxis: [
        {
          type: "time",
          min: start,
          max: end,
          minInterval: every,
          maxInterval: every,
          axisLine: { lineStyle: { color: divider } },
          axisTick: { show: false },
          axisLabel: { color: muted, hideOverlap: true, formatter: (value: number) => f.clock.format(value) },
          splitLine: { show: false },
        },
        { type: "time", gridIndex: 1, min: start, max: end, show: false },
      ],
      yAxis: [
        {
          type: "value",
          name: "kW",
          min: 0,
          max: scale.max,
          interval: scale.step,
          nameTextStyle: { color: muted, align: "left", padding: [0, 0, 0, -20] },
          axisLabel: { color: muted, formatter: (value: number) => f.kw.format(value) },
          splitLine: { lineStyle: { color: divider, type: "dashed" } },
        },
        { type: "value", gridIndex: 1, min: 0, max: 1, show: false },
      ],
      series,
    };
  }

  private strip(
    runs: PriceRun[],
    primary: string,
    muted: string,
    text: string,
    money: Intl.NumberFormat,
  ): Record<string, unknown> {
    const hatch = this.hatch(muted);
    return {
      name: this.priceName(this.config!),
      type: "custom",
      xAxisIndex: 1,
      yAxisIndex: 1,
      silent: true,
      data: runs.map((run) => [run.start, run.end, run.price, run.alpha, run.estimated ? 1 : 0]),
      renderItem: (
        _params: unknown,
        api: { value(i: number): number; coord(point: number[]): number[] },
      ) => {
        const [x0, y0] = api.coord([api.value(0), 1]);
        const [x1, y1] = api.coord([api.value(1), 0]);
        const width = Math.max(0, x1! - x0! - 1);
        const shape = { x: x0!, y: y0!, width, height: y1! - y0! };
        const children: Record<string, unknown>[] = [
          { type: "rect", shape, style: { fill: withAlpha(primary, api.value(3)) } },
        ];
        if (api.value(4) && hatch) children.push({ type: "rect", shape, style: { fill: hatch } });
        if (width >= 34) {
          children.push({
            type: "text",
            style: {
              text: money.format(api.value(2)),
              x: x0! + width / 2,
              y: y0! + (y1! - y0!) / 2,
              align: "center",
              verticalAlign: "middle",
              fontSize: 11,
              fill: text,
            },
          });
        }
        return { type: "group", children };
      },
    };
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
      `<span style="display:inline-block;width:8px;height:8px;border-radius:4px;background:${color};margin-right:6px"></span>`;
    const rows = [
      `<b>${escape(`${f.day.format(slot.start)} ${f.clock.format(slot.start)}–${f.clock.format(slot.end)}`)}</b>`,
    ];
    for (const load of config.loads) {
      const kw = slot.loadKw[load.id] ?? 0;
      if (kw > 0) rows.push(`${dot(this.colorOf(load.id))}${escape(load.name)}: ${f.kw.format(kw)} kW`);
    }
    if (slot.baselineKw !== null && !this.single) {
      rows.push(`${escape(labels.other_usage ?? "")}: ${f.kw.format(slot.baselineKw)} kW`);
    }
    const [head, sub] = this.readoutLines(readout);
    rows.push('<hr style="border:0;border-top:1px solid var(--divider-color);margin:4px 0">');
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
    els.toggle.hidden = true;
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
    const extra = grain === "hour" ? [e.ceiling, e.price].filter((id): id is string => Boolean(id)) : [];
    const [usage, marks, peaks] = await Promise.all([
      fetchStatistics(hass, period, sources, ["change"], grain),
      fetchStatistics(hass, period, extra, ["mean"], "hour"),
      e.window_used ? fetchStatistics(hass, grain === "hour" ? month : period, [e.window_used], ["max"], "day") : Promise.resolve({}),
    ]);
    if (this.period !== period || !this.isConnected) return;
    const starts = [...new Set(sources.flatMap((id) => (usage[id] ?? []).map((row) => row.start)))].sort((a, b) => a - b);
    const empty = starts.length === 0;
    els.chart.hidden = empty;
    els.message.hidden = !empty;
    els.message.textContent = empty ? withoutDate(labels.collecting ?? "") : "";
    els.legend.replaceChildren();
    if (empty) return;
    if (!this.chart) {
      const { echarts } = await import("./chart");
      if (this.chart || !this.isConnected) return;
      this.chart = echarts.init(els.chart, undefined, { renderer: "canvas" });
    }
    const f = this.formats();
    const step = grain === "hour" ? 3_600_000 : 86_400_000;
    const start = period.start.getTime();
    const end = period.end.getTime();
    const muted = cssVar(this, "--secondary-text-color", "#727272");
    const divider = cssVar(this, "--divider-color", "rgba(0,0,0,.12)");
    const error = cssVar(this, "--error-color", "#db4437");
    const warning = cssVar(this, "--warning-color", "#ffa600");
    const grid = cssVar(this, "--energy-grid-consumption-color", "#488fc2");
    const shades = [grid, withAlpha(grid, 0.6), withAlpha(grid, 0.35)];
    const plotPx = Math.max(100, (this.width || 600) - AXIS_PX - 24);
    const barWidth = Math.max(1, (plotPx * step) / (end - start) - 2);
    const totals = new Map<number, number>();
    const series: Record<string, unknown>[] = sources.map((id, index) => {
      const rows = new Map((usage[id] ?? []).map((row) => [row.start, row.change ?? 0]));
      for (const t of starts) totals.set(t, (totals.get(t) ?? 0) + (rows.get(t) ?? 0));
      return {
        name: String(hass.states[id]?.attributes.friendly_name ?? id),
        type: "bar",
        stack: "grid",
        barWidth,
        itemStyle: { color: shades[index % shades.length] },
        data: starts.map((t) => [t + step / 2, rows.get(t) ?? 0]),
      };
    });
    const peakRows = (e.window_used ? (peaks as Record<string, StatRow[]>)[e.window_used] : undefined) ?? [];
    const dayPeaks: DayPeak[] = peakRows.filter((row) => row.max != null).map((row) => [dayKey(row.start, zone), row.max!]);
    const top = Math.max(...totals.values());
    const markLines: Record<string, unknown>[] = [];
    const markPoints: Record<string, unknown>[] = [];
    let ceilingMax = 0;
    if (grain === "hour") {
      // One day: the limit, the month's third-highest day and the day's highest hour.
      const ceiling = e.ceiling ? (marks[e.ceiling] ?? []) : [];
      if (ceiling.length) {
        ceilingMax = Math.max(...ceiling.map((row) => row.mean ?? 0));
        series.push({
          name: labels.limit ?? "",
          type: "line",
          step: "end",
          symbol: "none",
          lineStyle: { type: "dashed", width: 1.5, color: error },
          endLabel: { show: true, formatter: fill(labels.limit_value ?? "{kw} kW", { kw: f.kw.format(ceilingMax) }), color: muted, fontSize: 11, align: "right", offset: [-4, -10] },
          data: [...ceiling.map((row) => [row.start, row.mean]), [ceiling[ceiling.length - 1]!.end, ceiling[ceiling.length - 1]!.mean]],
        });
      }
      const third = nthHighest(dayPeaks.filter(([day]) => day !== dayKey(start, zone)));
      if (third) {
        markLines.push({
          yAxis: third[1],
          lineStyle: { color: warning, width: 1, type: "dashed" },
          label: { formatter: fill(labels.third_threshold ?? "{kw}", { kw: f.money.format(third[1]) }), position: "insideStartTop", color: warning, fontSize: 11 },
        });
      }
      const [bestAt, best] = [...totals.entries()].reduce((a, b) => (b[1] > a[1] ? b : a));
      markPoints.push({
        coord: [bestAt + step / 2, best],
        symbol: "circle",
        symbolSize: 7,
        itemStyle: { color: warning },
        label: { show: true, position: "top", formatter: fill(labels.highest_hour ?? "{kwh}", { kwh: f.money.format(best) }), color: muted, fontSize: 11 },
      });
      const prices = e.price ? (marks[e.price] ?? []) : [];
      if (prices.length) {
        const slots = prices.map((row) => ({
          start: row.start, end: row.end, hours: 1, price: row.mean ?? null, estimated: false,
          ceilingKw: null, baselineKw: null, productionKw: null, loadKw: {}, loadKwh: {},
        })) as TimelineSlot[];
        series.push(this.strip(priceRuns(slots), cssVar(this, "--primary-color", "#03a9f4"), muted, cssVar(this, "--primary-text-color", "#212121"), f.money));
      }
    } else {
      // A longer range: an amber dot over each day that counts.
      const counting = countingDays(dayPeaks);
      for (const t of starts) {
        if (counting.has(dayKey(t, zone))) {
          markPoints.push({ coord: [t + step / 2, totals.get(t)], symbol: "circle", symbolSize: 6, symbolOffset: [0, -6], itemStyle: { color: warning }, label: { show: false } });
        }
      }
    }
    // The marks ride on the top source, so they sit over the stack.
    const last = sources.length - 1;
    if (series[last]) {
      series[last] = { ...series[last], markPoint: { silent: true, data: markPoints }, markLine: { silent: true, symbol: "none", data: markLines } };
    }
    const scale = niceScale(Math.max(top * 1.15, ceilingMax * 1.2));
    const hasStrip = series.some((item) => item.type === "custom");
    this.chart.setOption(
      {
        animation: false,
        grid: [
          { left: 4, right: 8, top: 22, bottom: (hasStrip ? STRIP_PX : 0) + LABELS_PX + 2, containLabel: true },
          { left: 4, right: 8, bottom: 0, height: hasStrip ? STRIP_PX : 0, containLabel: true },
        ],
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: divider } },
          valueFormatter: (value: number) => `${f.money.format(value)} kWh`,
        },
        xAxis: [
          {
            type: "time",
            min: start,
            max: end,
            axisLine: { lineStyle: { color: divider } },
            axisTick: { show: false },
            axisLabel: {
              color: muted,
              hideOverlap: true,
              formatter: (value: number) => (grain === "hour" ? f.clock.format(value) : new Intl.DateTimeFormat(hass.locale.language, { day: "numeric", month: "short", timeZone: zone }).format(value)),
            },
          },
          { type: "time", gridIndex: 1, min: start, max: end, show: false },
        ],
        yAxis: [
          {
            type: "value",
            name: "kWh",
            min: 0,
            max: scale.max,
            interval: scale.step,
            nameTextStyle: { color: muted },
            axisLabel: { color: muted, formatter: (value: number) => f.kw.format(value) },
            splitLine: { lineStyle: { color: divider, type: "dashed" } },
          },
          { type: "value", gridIndex: 1, min: 0, max: 1, show: false },
        ],
        series,
      },
      { notMerge: true },
    );
  }
}
