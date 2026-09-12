// Stable, human status for one appliance row (iteration 4).
//
// Iteration 4 changes: the status is shown as plain secondary text ("Går · 23,8 → 24 °C"), like a
// tile's state line, instead of a coloured pill. New kind "holding": a heating load that keeps
// its temperature (hold_kwh in the current slot) without a run that PowerPlan moved.
// The 90 s debounce from iteration 3 stays: planstatus sensors flap every few minutes.

import type { HassEntity } from "./r3-util";

export type Kind = "running" | "holding" | "planned" | "waiting" | "paused" | "manual" | "idle" | "unavailable";

export interface StatusView {
  kind: Kind;
  word: string;       // first word of the state line; "" = none
  reason?: string;    // replaces the detail for paused/manual/off
}

export const DEBOUNCE_MS = 90_000;

export const STATUS_LABELS: Record<string, Record<string, string>> = {
  nb: {
    running: "Går", charging: "Lader", forced: "Tvunget på", holding: "Holder", planned: "Planlagt", waiting: "Venter",
    paused: "Strupet", manual: "Manuell", off: "Av", unavailable: "Utilgjengelig",
    paused_reason: "pause for å holde effekttrinnet", manual_reason: "styres fra enheten", off_reason: "slått av i PowerPlan",
    deadline: "frist {time}", target: "mål {v}", next: "Neste {time}",
  },
  en: {
    running: "Running", charging: "Charging", forced: "Forced on", holding: "Holding", planned: "Planned", waiting: "Waiting",
    paused: "Throttled", manual: "Manual", off: "Off", unavailable: "Unavailable",
    paused_reason: "paused to protect the capacity step", manual_reason: "controlled by the device", off_reason: "turned off in PowerPlan",
    deadline: "due {time}", target: "target {v}", next: "Next {time}",
  },
};

/** Pure mapping, no debounce. */
export function rawStatus(
  status: HassEntity | undefined,
  control: HassEntity | undefined,
  o: { runNow: boolean; futureRun: boolean; holdNow: boolean },
  L: Record<string, string>,
): StatusView {
  if (!status || status.state === "unavailable" || status.state === "unknown") {
    return { kind: "unavailable", word: L.unavailable };
  }
  const ctl = control?.attributes?.effective ?? control?.state;
  if (ctl === "off") return { kind: "manual", word: L.off, reason: L.off_reason };
  if (ctl === "observe" || ctl === "delegated") return { kind: "manual", word: L.manual, reason: L.manual_reason };
  if (ctl === "force") return { kind: "running", word: L.forced };

  const a = status.attributes ?? {};
  const s: string = a.display_status ?? status.state;   // backend-debounced status when published (status_guard.py)
  if (s === "paused_peak" || a.shed_reason) return { kind: "paused", word: L.paused, reason: L.paused_reason };
  // "already_at" = the device already is where the plan wants it; that is not a manual override.
  if (s === "manual_override" && a.reason_key && a.reason_key !== "already_at") {
    return { kind: "manual", word: L.manual, reason: L.manual_reason };
  }
  if (s === "charging") return { kind: "running", word: L.charging };
  if (o.runNow || s.startsWith("running")) return { kind: "running", word: L.running };
  if (s === "waiting") return { kind: "waiting", word: L.waiting };
  if (o.holdNow) return { kind: "holding", word: L.holding };
  if (o.futureRun) return { kind: "planned", word: L.planned };
  return { kind: "idle", word: "" };
}

interface Memo { shown: StatusView; cand: StatusView; since: number }

/** Keeps the displayed status steady. One instance per card. */
export class StatusDebouncer {
  private memo = new Map<string, Memo>();

  view(id: string, next: StatusView, now = Date.now()): StatusView {
    const m = this.memo.get(id);
    if (!m) {
      this.memo.set(id, { shown: next, cand: next, since: now });
      return next;
    }
    // Immediate: availability, anything the user caused via the control select, or no real change.
    const immediate = next.kind === "unavailable" || m.shown.kind === "unavailable" ||
      (next.kind === "manual") !== (m.shown.kind === "manual") || (next.kind === m.shown.kind && next.word === m.shown.word);
    if (immediate) {
      m.shown = next; m.cand = next; m.since = now;
      return next;
    }
    if (next.kind !== m.cand.kind || next.word !== m.cand.word) {
      m.cand = next; m.since = now;
    } else if (now - m.since >= DEBOUNCE_MS) {
      m.shown = next;
    }
    return m.shown;
  }

  nextFlip(): number | null {
    let t: number | null = null;
    for (const m of this.memo.values()) {
      if (m.cand.kind !== m.shown.kind || m.cand.word !== m.shown.word) {
        const due = m.since + DEBOUNCE_MS;
        t = t === null ? due : Math.min(t, due);
      }
    }
    return t;
  }
}
