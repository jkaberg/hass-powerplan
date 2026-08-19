// `powerplan-timeline-card` (D12 §5.2): the next 24–48 hours - the price, each
// appliance's planned power stacked per slot, the capacity limit and the rest
// of the house - on ECharts at HA's own major version, themed from HA's CSS
// variables.

import type { EChartsCoreOption, ECharts } from "echarts/core";

import { cssVar, type HomeAssistant, timeZone } from "./ha";
import {
  estimatedRanges,
  type PlanSlot,
  type PriceSlot,
  steps,
  type TimelineSlot,
  timelineSlots,
} from "./transforms";

interface TimelineConfig {
  entry_id: string;
  hours?: number;
  loads: Array<{ id: string; name: string }>;
  entities: { plan: string; price_forecast: string };
  show?: string[];
  currency?: string;
  labels?: Record<string, string>;
}

/** HA's graph palette (`--graph-color-n`), with its defaults where a theme sets none. */
const PALETTE = [
  "#4269d0", "#f4bd4a", "#ff725c", "#6cc5b0", "#a463f2",
  "#ff8ab7", "#9c6b4e", "#97bbf5", "#01ab63", "#9498a0",
];
/** Redraw at least this often, so the "now" marker moves with no new data. */
const NOW_STEP_MS = 5 * 60_000;

export class PowerplanTimelineCard extends HTMLElement {
  private config?: TimelineConfig;
  private hassRef?: HomeAssistant;
  private chart?: ECharts;
  private chartEl?: HTMLDivElement;
  private emptyEl?: HTMLDivElement;
  private key: unknown[] = [];
  private resize?: ResizeObserver;

  public setConfig(config: TimelineConfig): void {
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
    // HA replaces a state object only when it changes: redraw on a new plan,
    // a new curve, a theme or language switch, or the clock's next step.
    const key = [
      hass.states[config.entities.plan],
      hass.states[config.entities.price_forecast],
      hass.themes.darkMode,
      hass.language,
      Math.floor(Date.now() / NOW_STEP_MS),
    ];
    if (key.every((part, index) => part === this.key[index])) return;
    this.key = key;
    void this.render();
  }

  public connectedCallback(): void {
    this.resize = new ResizeObserver(() => this.chart?.resize());
    this.resize.observe(this);
    if (this.hassRef) {
      this.key = [];
      this.hass = this.hassRef;
    }
  }

  public disconnectedCallback(): void {
    this.resize?.disconnect();
    this.chart?.dispose();
    this.chart = undefined;
  }

  public getCardSize(): number {
    return 6;
  }

  public getGridOptions(): Record<string, number> {
    return { columns: 12, rows: 6, min_rows: 4, min_columns: 6 };
  }

  private shell(): void {
    if (this.chartEl) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; height: 100%; }
        ha-card { height: 100%; box-sizing: border-box; padding: 8px 8px 4px; }
        .chart { width: 100%; height: 100%; min-height: 220px; }
        .empty { padding: 16px; color: var(--secondary-text-color); }
      </style>
      <ha-card><div class="empty" hidden></div><div class="chart"></div></ha-card>`;
    this.chartEl = root.querySelector(".chart") as HTMLDivElement;
    this.emptyEl = root.querySelector(".empty") as HTMLDivElement;
  }

  private async render(): Promise<void> {
    const hass = this.hassRef;
    const config = this.config;
    if (!hass || !config) return;
    this.shell();
    const plan = hass.states[config.entities.plan];
    const prices = hass.states[config.entities.price_forecast];
    const labels = config.labels ?? {};
    const slots = timelineSlots(
      (prices?.attributes.slots as PriceSlot[] | undefined) ?? [],
      (plan?.attributes.slots as PlanSlot[] | undefined) ?? [],
      Number(plan?.attributes.window_min ?? 60),
      config.loads.map((load) => load.id),
      Date.now(),
      config.hours ?? 24,
    );
    const empty = slots.length === 0;
    this.emptyEl!.hidden = !empty;
    this.emptyEl!.textContent = labels.no_plan ?? "";
    this.chartEl!.hidden = empty;
    if (empty) return;
    if (!this.chart) {
      const { echarts } = await import("./chart");
      if (this.chart || !this.isConnected) return;
      this.chart = echarts.init(this.chartEl!, undefined, { renderer: "canvas" });
    }
    this.chart.setOption(this.option(hass, config, slots), { notMerge: true });
  }

  private option(
    hass: HomeAssistant,
    config: TimelineConfig,
    slots: TimelineSlot[],
  ): EChartsCoreOption {
    const labels = config.labels ?? {};
    const show = new Set(config.show ?? ["price", "plan", "ceiling", "baseline"]);
    const text = cssVar(this, "--primary-text-color", "#212121");
    const muted = cssVar(this, "--secondary-text-color", "#727272");
    const divider = cssVar(this, "--divider-color", "rgba(0,0,0,.12)");
    const locale = hass.locale.language;
    const zone = timeZone(hass);
    const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone: zone });
    const day = new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: zone });
    const kw = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
    const money = new Intl.NumberFormat(locale, { maximumFractionDigits: 2, minimumFractionDigits: 2 });
    const currency = config.currency ?? "";
    const now = Date.now();

    const series: Record<string, unknown>[] = [];
    if (show.has("plan")) {
      config.loads.forEach((load, index) => {
        series.push({
          name: load.name,
          type: "bar",
          stack: "plan",
          yAxisIndex: 0,
          barCategoryGap: "10%",
          itemStyle: { color: cssVar(this, `--graph-color-${index + 1}`, PALETTE[index % PALETTE.length]!) },
          data: slots.map((slot) => [(slot.start + slot.end) / 2, slot.loadKw[load.id] ?? 0]),
        });
      });
    }
    if (show.has("baseline")) {
      series.push({
        name: labels.baseline ?? "baseline",
        type: "line",
        step: "end",
        symbol: "none",
        yAxisIndex: 0,
        lineStyle: { width: 0 },
        areaStyle: {
          color: cssVar(this, "--energy-grid-consumption-color", "#488fc2"),
          opacity: 0.4,
        },
        data: steps(slots, (slot) => slot.baselineKw),
      });
    }
    if (show.has("production") && slots.some((slot) => slot.productionKw !== null)) {
      series.push({
        name: "production",
        type: "line",
        step: "end",
        symbol: "none",
        yAxisIndex: 0,
        lineStyle: { width: 0 },
        areaStyle: { color: cssVar(this, "--energy-solar-color", "#ff9800"), opacity: 0.4 },
        data: steps(slots, (slot) => slot.productionKw),
      });
    }
    if (show.has("ceiling")) {
      series.push({
        name: labels.ceiling ?? "ceiling",
        type: "line",
        step: "end",
        symbol: "none",
        yAxisIndex: 0,
        lineStyle: { type: "dashed", width: 2, color: cssVar(this, "--error-color", "#db4437") },
        itemStyle: { color: cssVar(this, "--error-color", "#db4437") },
        data: steps(slots, (slot) => slot.ceilingKw),
      });
    }
    series.push({
      name: labels.price ?? "price",
      type: "line",
      step: "end",
      symbol: "none",
      yAxisIndex: 1,
      lineStyle: { width: 2, color: cssVar(this, "--primary-color", "#03a9f4") },
      itemStyle: { color: cssVar(this, "--primary-color", "#03a9f4") },
      data: steps(slots, (slot) => slot.price),
      markArea: {
        silent: true,
        itemStyle: { color: muted, opacity: 0.12 },
        label: { color: muted, position: "insideTop", fontSize: 10 },
        data: estimatedRanges(slots).map(([start, end]) => [
          { xAxis: start, name: labels.estimated ?? "" },
          { xAxis: end },
        ]),
      },
      markLine: {
        silent: true,
        symbol: "none",
        lineStyle: { color: muted, type: "solid", width: 1 },
        label: { formatter: labels.now ?? "", color: muted, position: "end" },
        data: now >= slots[0]!.start ? [{ xAxis: now }] : [],
      },
    });

    const bySlot = new Map(slots.map((slot) => [(slot.start + slot.end) / 2, slot]));
    const find = (value: number) =>
      bySlot.get(value) ?? slots.find((slot) => slot.start <= value && value < slot.end);

    return {
      animation: false,
      textStyle: { color: text },
      grid: { left: 8, right: 8, top: 28, bottom: 36, containLabel: true },
      legend: {
        bottom: 0,
        type: "scroll",
        textStyle: { color: text },
        icon: "roundRect",
        itemWidth: 12,
        itemHeight: 8,
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: Array<{ axisValue: number }>) => {
          const slot = params[0] ? find(params[0].axisValue) : undefined;
          if (!slot) return "";
          const rows = [`<b>${day.format(slot.start)} ${clock.format(slot.start)}–${clock.format(slot.end)}</b>`];
          let kwh = 0;
          for (const load of config.loads) {
            const value = slot.loadKwh[load.id] ?? 0;
            if (value <= 0) continue;
            kwh += value;
            rows.push(`${load.name}: ${kw.format(value)} kWh`);
          }
          if (slot.baselineKw !== null) rows.push(`${labels.baseline ?? ""}: ${kw.format(slot.baselineKw)} kW`);
          if (slot.ceilingKw !== null) rows.push(`${labels.ceiling ?? ""}: ${kw.format(slot.ceilingKw)} kW`);
          if (slot.price !== null) {
            const estimated = slot.estimated ? ` (${labels.estimated ?? ""})` : "";
            rows.push(`${labels.price ?? ""}: ${money.format(slot.price)} ${currency}/kWh${estimated}`);
            if (kwh > 0) rows.push(`≈ ${money.format(kwh * slot.price)} ${currency}`);
          }
          return rows.join("<br>");
        },
      },
      xAxis: {
        type: "time",
        min: slots[0]!.start,
        max: slots[slots.length - 1]!.end,
        axisLine: { lineStyle: { color: divider } },
        axisLabel: {
          color: muted,
          hideOverlap: true,
          formatter: (value: number) => clock.format(value),
        },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: "value",
          name: "kW",
          nameTextStyle: { color: muted },
          axisLabel: { color: muted },
          splitLine: { lineStyle: { color: divider } },
        },
        {
          type: "value",
          name: currency ? `${currency}/kWh` : "",
          nameTextStyle: { color: muted },
          axisLabel: { color: muted },
          splitLine: { show: false },
        },
      ],
      series,
    };
  }
}
