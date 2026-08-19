// The slice of Home Assistant's frontend objects the cards read. HA's own
// types are not a published package; these are the fields used, nothing more.

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, unknown>;
  last_changed: string;
  last_updated: string;
}

export interface HomeAssistant {
  states: Record<string, HassEntity | undefined>;
  language: string;
  locale: { language: string; time_zone: "local" | "server" | string };
  config: { time_zone: string };
  themes: { darkMode: boolean };
  callWS<T>(message: Record<string, unknown>): Promise<T>;
}

/** The time zone HA shows times in: the browser's, or the server's (HA's own "time zone" setting). */
export function timeZone(hass: HomeAssistant): string | undefined {
  return hass.locale.time_zone === "local" ? undefined : hass.config.time_zone;
}

/** A number from an entity state, `null` when it is unknown or unavailable. */
export function numeric(entity: HassEntity | undefined): number | null {
  if (!entity) return null;
  const value = Number(entity.state);
  return entity.state === "" || !Number.isFinite(value) ? null : value;
}

/** Open an entity's more-info dialog, as every built-in card does on tap. */
export function moreInfo(element: HTMLElement, entityId: string): void {
  element.dispatchEvent(
    new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }),
  );
}

/** Read a theme variable off the element, with a fallback for a theme that lacks it. */
export function cssVar(element: Element, name: string, fallback: string): string {
  return getComputedStyle(element).getPropertyValue(name).trim() || fallback;
}
