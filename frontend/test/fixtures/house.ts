// The reference house, captured 2026-09-26 at 19:30 Europe/Oslo (D9 §5.16): every state the cards read,
// the plan and price curves as captured (`plan.json`, `price.json`), the TV-stua floor's last 24 hours
// (`tv-stua.json`), and a month of statistics. The house's own statistics begin on 23 Sep, so the month
// before it is generated from a fixed seed around the three days the tariff counted (13, 17 and 21 Sep).

import plan from "./plan.json";
import price from "./price.json";
import tvStua from "./tv-stua.json";

export const NOW = new Date("2026-09-26T17:30:00Z");
export const ZONE = "Europe/Oslo";
const SITE = "nygardsvegen_6";

type Attrs = Record<string, unknown>;
export interface State { entity_id: string; state: string; attributes: Attrs; last_changed: string; last_updated: string }

const at = "2026-09-26T17:29:41+00:00";
const st = (entity_id: string, state: string | number, attributes: Attrs = {}): State =>
  ({ entity_id, state: String(state), attributes, last_changed: at, last_updated: at });

/** Each appliance: its subentry id, entity slug, name, D4 type, colour (HA's palette by position) and state. */
export const LOADS = [
  { id: "01M37FJ30PGSK06TC2ZNA4JEXX", slug: "garasje_billader", name: "Billader", kind: "ev", status: "done", cost: 13.83, saved: 1.29, shifted: 16.08, energy: 34.09 },
  { id: "01M37FMNCJC8PNNHH17N8CJ3GY", slug: "vaskerom_varmtvannsbereder", name: "Varmtvannsbereder", kind: "water_heater", status: "waiting", cost: 4.29, saved: 0.54, shifted: 4.255, energy: 24.18, comfort: [48, 45, 45] },
  { id: "01M37FPXZQH2FHQC7E1Z00SEA8", slug: "bad_1_etasje_gulvvarme_bad_1_etasje", name: "Gulvvarme bad 1. etasje", kind: "floor_heating", status: "idle", cost: 1.87, saved: -0.04, shifted: 0.751, energy: 4.4, comfort: [24.2, 24, 23] },
  { id: "01M37FR5MQ78354HAY56RMEVD5", slug: "bad_2_etasje_gulvvarme_bad_2_etasje", name: "Gulvvarme bad 2. etasje", kind: "floor_heating", status: "running_plan", cost: 0.41, saved: 0.01, shifted: 0.218, energy: 1.09, comfort: [23.6, 24, 23] },
  { id: "01M37FSXZAK7ABD93BDXAHYGJ6", slug: "inngang_gulvvarme_inngang", name: "Gulvvarme inngang", kind: "floor_heating", status: "running_plan", cost: 3.12, saved: 0, shifted: 1.449, energy: 8.32, comfort: [23.1, 23.5, 22] },
  { id: "01M37FVRR0JZVXYXB5EG3366WZ", slug: "kjokken_gulvvarme_kjokken", name: "Gulvvarme kjøkken", kind: "floor_heating", status: "idle", cost: 0, saved: 0, shifted: 0, energy: 0.5, comfort: [23.4, 22, 20] },
  { id: "01M37FWWZZXSH2A53QQDX3XVGZ", slug: "stua_gulvvarme_stua", name: "Gulvvarme stua", kind: "floor_heating", status: "idle", cost: 0, saved: 0, shifted: 0, energy: 0.002, comfort: [23.1, 22, 20] },
  { id: "01M37FY02NSFWJ11N8JTF6QA10", slug: "tv_stua_gulvvarme_tv_stua", name: "Gulvvarme TV-stua", kind: "floor_heating", status: "running_plan", cost: 0.27, saved: 0, shifted: 0.134, energy: 0.58, comfort: [21.6, 22, 20] },
  { id: "01M37G4MPGA6BYB45PTR1SSFGQ", slug: "inngang_varmepumpe_1_etasje", name: "Varmepumpe 1 etasje", kind: "heat_pump", status: "idle", cost: 3.86, saved: -0.03, shifted: 0.315, energy: 10.92, comfort: [23, 21, 18] },
  { id: "01M37G5F0C4JPRTJFAT1TNDGP2", slug: "stua_ac_2_etasje", name: "AC 2 etasje", kind: "heat_pump", status: "idle", cost: 5.49, saved: -0.02, shifted: 0.339, energy: 15.98, comfort: [23, 21, 17] },
] as const;

export const PALETTE = ["#4269d0", "#f4bd4a", "#ff725c", "#6cc5b0", "#a463f2", "#ff8ab7", "#9c6b4e", "#97bbf5", "#01ab63", "#094bad"];
export const color = (i: number) => PALETTE[i % PALETTE.length]!;

export const E = {
  plan: `sensor.${SITE}_planlagt_forbruk`,
  price: `sensor.${SITE}_strompris_na`,
  price_forecast: `sensor.${SITE}_priser_kjent_til`,
  fixed_price_savings: `sensor.${SITE}_fixed_price_savings`,
  refresh: `button.${SITE}_hent_priser_pa_nytt`,
  window_used: `sensor.${SITE}_forbruk_denne_timen`,
  window_projected: `sensor.${SITE}_forventet_forbruk_denne_timen`,
  ceiling: `sensor.${SITE}_mal_denne_timen`,
  allowance: `sensor.${SITE}_tilgjengelig_effekt_na`,
  stage: `sensor.${SITE}_styringsniva`,
  peak_warning: `binary_sensor.${SITE}_effektvarsel`,
  next_peak_warning: `sensor.${SITE}_neste_risikotime`,
  metric: `sensor.${SITE}_effektgrunnlag_denne_perioden`,
  level: `sensor.${SITE}_effekttrinn_denne_maneden`,
  projected_level: `sensor.${SITE}_forventet_effekttrinn`,
  advice: `sensor.${SITE}_anbefaling`,
  target: `select.${SITE}_mal_for_effekttrinn`,
  cost: `sensor.${SITE}_kostnad_denne_maneden`,
  savings: `sensor.${SITE}_beregnet_besparelse_denne_maneden`,
  deviations: `sensor.${SITE}_avvik_denne_maneden`,
  meter_health: `sensor.${SITE}_malerstatus`,
  grid: "sensor.stromforbruk_totalt",
} as const;

export const loadEntity = (slug: string, key: "planstatus" | "kostnad_denne_maneden" | "besparelse_denne_maneden" | "energi_totalt" | "styring" | "temp") =>
  key === "styring" ? `select.${slug}_styring` : key === "temp" ? `sensor.${slug}_temperatur` : `sensor.${slug}_${key}`;

const STATUS_OPTIONS = ["device_unavailable", "manual_override", "run_now", "not_controlled", "observing", "no_car", "charging", "done", "paused_peak", "idle", "running_plan", "waiting"];
const byLoad = plan.by_load as Record<string, { next_start: string | null; planned_kwh: number; cost: string; strategy: string }>;

/** The day profile the ledger would publish for September (D11 §5.12): an example, the house has 1,5 days. */
export const DAY_PROFILE = (() => {
  const cf = [0.9, 0.8, 0.8, 0.8, 0.8, 0.9, 1.3, 2.1, 2.0, 1.5, 1.3, 1.3, 1.3, 1.3, 1.4, 1.6, 2.2, 3.1, 3.4, 3.0, 2.6, 2.0, 1.5, 1.1];
  const delta = [0.7, 0.7, 0.4, 0.2, 0, 0, 0, -0.2, -0.2, 0, 0, 0, 0, 0, 0, -0.1, -0.3, -0.7, -0.9, -0.7, -0.4, 0, 0.7, 0.8];
  const days = 20;
  return {
    month: "2026-09",
    kwh: cf.map((v, i) => +((v + delta[i]!) * days).toFixed(3)),
    cf_kwh: cf.map((v) => +(v * days).toFixed(3)),
    days,
  };
})();

export function states(): Record<string, State> {
  const s: State[] = [
    st(E.plan, 12.435, { slots: plan.slots, by_load: plan.by_load, window_min: plan.window_min, unit_of_measurement: "kWh" }),
    st(E.price, 0.8779, { next: 0.8779, min_today: 0.7379, max_today: 0.8779, mean_today: 0.8312, confidence: "known", unit_of_measurement: "NOK/kWh" }),
    st(E.price_forecast, "2026-09-27T22:00:00+00:00", { slots: price.slots, area: price.area, vat: price.vat, fixed_price: price.fixed_price, credit: price.credit, device_class: "timestamp" }),
    st(E.fixed_price_savings, 1137.6, { today: 38.02, kwh: 1360.7, today_kwh: 55.6, unit_of_measurement: "NOK", device_class: "monetary" }),
    st(E.refresh, "unknown"),
    st(E.window_used, 0.392, { t_rem_min: 52, unit_of_measurement: "kWh" }),
    st(E.window_projected, 2.747, { expected_kwh: 2.498, unit_of_measurement: "kWh" }),
    st(E.ceiling, 9.7, { reason: "flat target", unit_of_measurement: "kWh" }),
    st(E.allowance, 10.294, { unit_of_measurement: "kW" }),
    st(E.stage, 0),
    st(E.peak_warning, "off"),
    st(E.next_peak_warning, "unknown"),
    st(E.metric, 8.97, { unit_of_measurement: "kW" }),
    st(E.level, "5–10 kW", {
      steps: [[0, 2, 131], [2, 5, 233], [5, 10, 397], [10, 15, 585], [15, 20, 775], [20, 25, 964], [25, 50, 1655], [50, null, 2599]]
        .map(([from, to, fee]) => ({ name: to === null ? `over ${from} kW` : `${from}–${to} kW`, from_kw: from, to_kw: to, fee: `${fee}.00 NOK` })),
      metric_kw: 8.969816744109266, fee: "397.00 NOK",
    }),
    st(E.projected_level, "5–10 kW", { metric_kw: 8.969816744109266 }),
    st(E.advice, "step_headroom", {
      items: [
        { key: "top_entries", entries: [["2026-09-17", 9.116366333852056], ["2026-09-21", 8.935046385973692], ["2026-09-13", 8.858037512502051]], n: 3 },
        { key: "step_headroom", to_next_kw: 1.0301832558907336, next_name: "10–15 kW", fee_delta: "188.000", currency: "NOK", level_name: "5–10 kW" },
        { key: "days_that_matter", days: 1, kw: 11.948587280174252, n: 3 },
      ],
    }),
    st(E.target, "step_2", { target_kw: null, lower_kw: 5, upper_kw: 10, fee: "397.00", currency: "NOK" }),
    st(E.cost, 471.8863872, {
      state_class: "total", last_reset: "2026-09-25T09:00:00+00:00", by_party: { grid: "412.83743136", supplier: "36.6272", state: "22.4217558400" },
      energy_cost: "74.89 NOK", export_credit: "0.00 NOK", capacity_fee: "397.00 NOK", partial: true, energy_since: "2026-09-25T09:00:00+00:00",
      unit_of_measurement: "NOK", device_class: "monetary",
    }),
    st(E.savings, 1.7483201, {
      state_class: "total", energy_savings: "1.75 NOK", capacity_savings: "0.00 NOK", counterfactual_cost: "473.63 NOK", kwh_shifted: 23.541,
      savings_confidence: "ok", capacity_step: "5–10 kW", capacity_step_without: "5–10 kW", metric_kw: 8.969816744109266,
      metric_kw_without: 9.334706499373267, price_paid: 0.7674, price_reference: 0.8228, kwh_counted: 31.575,
      day_profile: DAY_PROFILE, previous_day_profile: null, unit_of_measurement: "NOK", device_class: "monetary",
    }),
    st(E.deviations, 0, { comfort_min: {}, deadlines_missed: {}, over_windows: 0, windows: 34, month: "2026-09" }),
    st(E.meter_health, "ok", { options: ["ok", "degraded", "stale"] }),
    st(E.grid, 18234.5, { state_class: "total_increasing", unit_of_measurement: "kWh", device_class: "energy" }),
  ];
  LOADS.forEach((l) => {
    const p = byLoad[l.id]!;
    const comfort = "comfort" in l ? { current: l.comfort[0], target: l.comfort[1], floor: l.comfort[2], comfort_state: l.comfort[0] < l.comfort[1] ? "below_target" : "at_target" } : {};
    const deadline = l.kind === "ev" || l.kind === "water_heater" ? { deadline: "2026-09-27T04:00:00+00:00", deadline_time: "06:00" } : { deadline: null, deadline_time: "" };
    s.push(
      st(loadEntity(l.slug, "planstatus"), l.status, {
        options: STATUS_OPTIONS, granted_power: l.status === "running_plan" ? 1200 : 0, reason_key: "already_at", reason_params: {},
        next_start: p.next_start, next_run: p.next_start ? new Intl.DateTimeFormat("nb", { hour: "2-digit", minute: "2-digit", timeZone: ZONE }).format(new Date(p.next_start)) : "",
        planned_kwh: p.planned_kwh, cost: p.cost, plan_mode: "price", covered: true, coverage: 1, strategy: p.strategy, confidence: "known",
        display_status: l.status, device_class: "enum", ...deadline, ...comfort,
      }),
      st(loadEntity(l.slug, "kostnad_denne_maneden"), l.cost, { state_class: "total", unit_of_measurement: "NOK", device_class: "monetary" }),
      st(loadEntity(l.slug, "besparelse_denne_maneden"), l.saved, { state_class: "total", kwh_shifted: l.shifted, savings_confidence: "ok", unit_of_measurement: "NOK", device_class: "monetary" }),
      st(loadEntity(l.slug, "energi_totalt"), l.energy, { state_class: "total_increasing", unit_of_measurement: "kWh", device_class: "energy" }),
      st(loadEntity(l.slug, "styring"), "auto", { options: ["auto", "force", "off", "observe", "delegated"] }),
    );
    if ("comfort" in l) s.push(st(loadEntity(l.slug, "temp"), l.comfort[0], { unit_of_measurement: "°C", device_class: "temperature", state_class: "measurement" }));
  });
  return Object.fromEntries(s.map((x) => [x.entity_id, x]));
}

// ------------------------------------------------------------------ statistics

function rng(seed: number): () => number {
  let s = seed;
  return () => ((s = (s * 16807) % 2147483647) - 1) / 2147483646;
}

const HOUR = 3_600_000;
/** 1 Sep 2026 00:00 Europe/Oslo (CEST), and 1 Aug, where the generated statistics begin (H4 compares with it). */
const SEP1 = Date.parse("2026-08-31T22:00:00Z");
const AUG1 = Date.parse("2026-07-31T22:00:00Z");

/** The house's hourly energy for September to now: kWh per local hour, the three counted days pinned. */
export const HOURLY: Array<{ start: number; kwh: number }> = (() => {
  const r = rng(20260926);
  const base = [0.7, 0.6, 0.6, 0.6, 0.6, 0.7, 1.2, 2.3, 2.4, 1.6, 1.3, 1.2, 1.3, 1.2, 1.3, 1.6, 2.6, 3.6, 3.9, 3.3, 2.7, 2.0, 1.4, 1.0];
  const out: Array<{ start: number; kwh: number }> = [];
  for (let t = AUG1; t < NOW.getTime(); t += HOUR) {
    const day = t < SEP1 ? 0 : Math.floor((t - SEP1) / 86_400_000) + 1, hour = Math.round(((((t - SEP1) % 86_400_000) + 86_400_000) % 86_400_000) / HOUR);
    let kwh = base[hour]! * (0.78 + r() * 0.44);
    if ([22, 23, 0, 1].includes(hour) && r() < 0.7) kwh += 2.4 * (0.8 + r() * 0.4);
    if (hour >= 17 && hour <= 19 && r() < 0.22) kwh += 1.5 + r() * 2.6;
    kwh = Math.min(kwh, 7.4 + (day % 5) * 0.12);
    if (day === 13 && hour === 18) kwh = 8.858;
    if (day === 17 && hour === 17) kwh = 9.116;
    if (day === 21 && hour === 19) kwh = 8.935;
    out.push({ start: t, kwh: +kwh.toFixed(3) });
  }
  return out;
})();

export interface StatRow { start: number; end: number; change?: number; max?: number; mean?: number; sum?: number }

function bucket(period: string, t: number): number {
  if (period === "hour" || period === "5minute") return t;
  const local = new Date(t + 2 * HOUR); // CEST throughout September
  if (period === "day") return Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate()) - 2 * HOUR;
  return Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), 1) - 2 * HOUR;
}

/** `recorder/statistics_during_period` over the fixture: the grid and window sensors, costs and appliance energy. */
export function statistics(msg: { statistic_ids: string[]; start_time: string; end_time?: string; period: string; types: string[] }): Record<string, StatRow[]> {
  const from = Date.parse(msg.start_time), to = msg.end_time ? Date.parse(msg.end_time) : NOW.getTime();
  const hours = HOURLY.filter((h) => h.start >= from && h.start < to);
  const total = HOURLY.reduce((a, h) => a + h.kwh, 0);
  const out: Record<string, StatRow[]> = {};
  for (const id of msg.statistic_ids) {
    const per = (share: (h: { start: number; kwh: number }) => number, kind: "change" | "max") => {
      const rows = new Map<number, StatRow>();
      for (const h of hours) {
        const key = bucket(msg.period, h.start), v = share(h);
        const row = rows.get(key) ?? { start: key, end: key + (msg.period === "hour" ? HOUR : 86_400_000), change: 0, max: 0, mean: 0 };
        row.change! += v; row.max = Math.max(row.max!, v); row.mean = row.max;
        rows.set(key, row);
      }
      return [...rows.values()].map((row) => (kind === "max" ? { start: row.start, end: row.end, max: +row.max!.toFixed(3), mean: +row.mean!.toFixed(3) } : { start: row.start, end: row.end, change: +row.change!.toFixed(3) }));
    };
    const load = LOADS.find((l) => loadEntity(l.slug, "energi_totalt") === id);
    if (id === E.window_used) out[id] = per((h) => h.kwh, "max");
    else if (id === E.ceiling) out[id] = per(() => 9.7, "max");
    // The control level: 1 on the hours heavy enough that the ladder held loads back.
    else if (id === E.stage) out[id] = per((h) => (h.kwh > 7 ? 1 : 0), "max");
    else if (id === E.grid) out[id] = per((h) => h.kwh, "change");
    else if (load) out[id] = per((h) => (h.kwh * load.energy) / 180 * (total / 1800), "change");
    else if (id === E.cost) out[id] = per((h) => (h.start >= Date.parse("2026-09-25T09:00:00Z") ? h.kwh * 0.83 + (h.start === Date.parse("2026-09-25T09:00:00Z") ? 397 : 0) : 0), "change");
    else if (id === E.savings) out[id] = per((h) => (h.start >= Date.parse("2026-09-25T09:00:00Z") ? 0.04 : 0), "change");
    else if (id.endsWith("_kostnad_denne_maneden") || id.endsWith("_besparelse_denne_maneden")) {
      const l = LOADS.find((x) => id.includes(x.slug));
      const value = id.endsWith("_kostnad_denne_maneden") ? l?.cost ?? 0 : l?.saved ?? 0;
      const since = hours.filter((h) => h.start >= Date.parse("2026-09-25T09:00:00Z"));
      out[id] = per((h) => (since.includes(h) ? value / Math.max(1, since.length) : 0), "change");
    }
  }
  return out;
}

/** `history/history_during_period`: the TV-stua floor's temperature and plan status, as captured. */
export function history(msg: { entity_ids: string[]; start_time: string }): Record<string, Array<{ s: string; lu: number; a?: Attrs }>> {
  const t0 = Date.parse(tvStua.start), step = tvStua.step_min * 60_000;
  const out: Record<string, Array<{ s: string; lu: number }>> = {};
  for (const id of msg.entity_ids) {
    const load = LOADS.find((l) => id.startsWith(`sensor.${l.slug}_`));
    if (!load) continue;
    if (load.kind === "water_heater" && id.endsWith("_planstatus")) {
      // The tank's nightly run PowerPlan placed: 22:00–01:00 local (20–23 UTC), waiting the rest of the day.
      const rows: Array<{ s: string; lu: number }> = [];
      for (let t = Math.floor(Date.parse(msg.start_time) / 86_400_000) * 86_400_000; t < NOW.getTime(); t += 86_400_000) {
        rows.push({ s: "waiting", lu: t / 1000 }, { s: "running_plan", lu: (t + 20 * HOUR) / 1000 }, { s: "waiting", lu: (t + 23 * HOUR) / 1000 });
      }
      out[id] = rows.filter((row) => row.lu * 1000 >= Date.parse(msg.start_time) && row.lu * 1000 < NOW.getTime());
      continue;
    }
    const series = load.slug === "tv_stua_gulvvarme_tv_stua"
      ? (id.endsWith("_temperatur") ? tvStua.temp.map(String) : tvStua.state)
      : Array.from({ length: tvStua.temp.length }, (_, i) => (id.endsWith("_temperatur") ? String("comfort" in load ? load.comfort[0] - 0.3 + 0.3 * Math.sin(i / 6) : 0) : load.status));
    out[id] = series.map((s, i) => ({ s, lu: (t0 + i * step) / 1000 })).filter((row, i, rows) => i === 0 || row.s !== rows[i - 1]!.s);
  }
  return out;
}
