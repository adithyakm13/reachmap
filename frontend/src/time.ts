// A time the runner wrote, as a person says it ("5 minutes ago"), with the exact text kept for the title of the element. No date library:
// the runner writes ISO 8601 and the browser parses it. A text that is not a time is returned as it is (the caller shows it as content).

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;

export function parseTime(text: string): number | null {
  const ms = Date.parse(text);
  return Number.isNaN(ms) ? null : ms;
}

function plural(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
}

/** How long before `now` a time was, in the largest whole unit; null when `text` is not a time. */
export function relative(text: string, now: number = Date.now()): string | null {
  const ms = parseTime(text);
  if (ms === null) {
    return null;
  }
  const seconds = Math.round((now - ms) / 1000);
  if (seconds < 0) {
    return "in the future";
  }
  if (seconds < 45) {
    return "just now";
  }
  if (seconds < HOUR) {
    return plural(Math.max(1, Math.round(seconds / MINUTE)), "minute");
  }
  if (seconds < DAY) {
    return plural(Math.round(seconds / HOUR), "hour");
  }
  if (seconds < 30 * DAY) {
    return plural(Math.round(seconds / DAY), "day");
  }
  return new Date(ms).toISOString().slice(0, 10);
}

/** Seconds of a run as a short text: `1.5 s`, `2 min 5 s`. */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "";
  }
  if (seconds < 60) {
    return `${Math.round(seconds * 10) / 10} s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
}
