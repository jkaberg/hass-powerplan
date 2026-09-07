// One appliance row's status, as the appliances card and its dialog show it
// (D12 §5.12 R1, R2). Pure: `plan_status` and the plan in, a pill out.
//
// Why a mapping and a debounce: `plan_status` is the engine's truth each tick,
// and on the live house it flapped - TV-stua changed 86 times in 6 h one
// day, `running_plan` ⇄ `paused_peak` ⇄ `waiting` every few minutes - and
// `manual_override` was raised with `reason_key: already_at`, the device
// already where the plan wants it. The card shows a status only after it has
// held for `DEBOUNCE_MS`; availability and the household's own changes show
// at once.

export type Kind = "running" | "planned" | "waiting" | "paused" | "holding" | "manual" | "idle" | "unavailable";

export interface StatusView {
  kind: Kind;
  /** The pill's text; "" draws no pill. */
  label: string;
  /** Why, for a paused or manual row: shown in the lane. */
  reason?: string;
}

export const DEBOUNCE_MS = 90_000;

/** Row order: what is happening now first, what needs nothing last. */
export const KIND_ORDER: readonly Kind[] = ["running", "waiting", "paused", "planned", "holding", "manual", "idle", "unavailable"];

/**
 * `plan_status` (D8 §5.16's twelve states) → what the row says.
 *
 * `hasRunNow` and `hasFutureRun` come from the plan's slots: a load the plan
 * runs this slot reads "running" even while the device still ramps, and one
 * waiting for a planned run reads "planned". `holding` is a thermal load the
 * plan leaves at its setpoint now, drawing its standing loss (D-0501): not idle.
 */
export function rawStatus(
  state: string | undefined,
  attributes: Record<string, unknown>,
  hasRunNow: boolean,
  hasFutureRun: boolean,
  labels: Record<string, string>,
  holding = false,
  perKwh: (value: number) => string = String,
): StatusView {
  // The integration's own 90 s hold, where it publishes one (D-0497).
  if (typeof attributes.display_status === "string" && state !== "unavailable" && state !== "unknown") {
    state = attributes.display_status;
  }
  if (state === undefined || state === "unavailable" || state === "unknown" || state === "device_unavailable") {
    return { kind: "unavailable", label: labels.status_unavailable ?? "" };
  }
  // A hand on the dial is one the integration did not cause. `already_at` is
  // the device already where the plan wants it: the plan, not a person.
  if (state === "manual_override" && attributes.reason_key !== "already_at") {
    return { kind: "manual", label: labels.status_manual ?? "", reason: labels.reason_manual ?? "" };
  }
  if (state === "not_controlled") {
    return { kind: "manual", label: labels.status_not_controlled ?? "", reason: labels.reason_not_controlled ?? "" };
  }
  if (state === "observing") {
    return { kind: "manual", label: labels.status_observing ?? "", reason: labels.reason_observing ?? "" };
  }
  if (state === "run_now") return { kind: "running", label: labels.status_forced ?? "" };
  if (state === "charging") return { kind: "running", label: labels.status_charging ?? "" };
  if (state === "paused_peak") {
    return { kind: "paused", label: labels.status_paused ?? "", reason: labels.reason_paused ?? "" };
  }
  if (state === "running_plan" || hasRunNow) return { kind: "running", label: labels.status_running ?? "" };
  if (hasFutureRun) {
    return { kind: "planned", label: labels.status_planned ?? "", reason: whyReason(attributes, labels, perKwh) };
  }
  if (holding) return { kind: "holding", label: labels.status_holding ?? "" };
  if (state === "waiting") {
    return { kind: "waiting", label: labels.status_waiting ?? "", reason: whyReason(attributes, labels, perKwh) };
  }
  return { kind: "idle", label: "" };
}

/**
 * D13 §7: the party whose price makes the wait worth it, in the household's words -
 * «Venter til 22:00 - nettleien er 13 øre lavere da». Undefined without one.
 */
export function whyReason(
  attributes: Record<string, unknown>,
  labels: Record<string, string>,
  perKwh: (value: number) => string = String,
): string | undefined {
  const templates: Record<string, string | undefined> = {
    grid: labels.why_grid,
    supplier: labels.why_supplier,
    state: labels.why_state,
  };
  const party = attributes.why_party;
  const template = typeof party === "string" ? templates[party] : undefined;
  if (!template) return undefined;
  const difference = Number(attributes.why_difference);
  return template
    .replace("{time}", String(attributes.why_until ?? ""))
    .replace("{difference}", Number.isFinite(difference) ? perKwh(difference) : "");
}

interface Memo {
  shown: StatusView;
  candidate: StatusView;
  since: number;
}

const same = (a: StatusView, b: StatusView) => a.kind === b.kind && a.label === b.label;

/** Keeps each row's shown status steady; one per card. */
export class StatusDebouncer {
  private memo = new Map<string, Memo>();

  public view(id: string, next: StatusView, now = Date.now()): StatusView {
    const memo = this.memo.get(id);
    if (!memo) {
      this.memo.set(id, { shown: next, candidate: next, since: now });
      return next;
    }
    // At once: availability, and a change the household made (the control select).
    const immediate =
      next.kind === "unavailable" ||
      memo.shown.kind === "unavailable" ||
      (next.kind === "manual") !== (memo.shown.kind === "manual") ||
      same(next, memo.shown);
    if (immediate) {
      memo.shown = next;
      memo.candidate = next;
      memo.since = now;
      return next;
    }
    if (!same(next, memo.candidate)) {
      memo.candidate = next;
      memo.since = now;
    } else if (now - memo.since >= DEBOUNCE_MS) {
      memo.shown = next;
    }
    return memo.shown;
  }

  /** The earliest instant a held change becomes visible, to schedule a redraw. */
  public nextFlip(): number | null {
    let due: number | null = null;
    for (const memo of this.memo.values()) {
      if (same(memo.candidate, memo.shown)) continue;
      const at = memo.since + DEBOUNCE_MS;
      due = due === null ? at : Math.min(due, at);
    }
    return due;
  }
}
