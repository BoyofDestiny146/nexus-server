/**
 * Server timezone helpers.
 *
 * The dashboard renders timestamps in the server's configured timezone —
 * not the admin's browser TZ — so an on-call from another timezone sees
 * consistent times that match the device logs.
 *
 * Use these helpers EVERYWHERE chat rows, session rows, assessment rows, or
 * device "last seen" stamps are rendered for human consumption. Do NOT call
 * `Date#toLocaleString` etc. directly.
 */

export const SERVER_TZ = "America/Chicago";
/** @deprecated Use SERVER_TZ */
export const FARGO_TZ = SERVER_TZ;

function safeDate(input: string | number | Date | null | undefined): Date | null {
  if (input == null) return null;
  const d = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(d.getTime())) return null;
  return d;
}

/** Time-of-day, e.g. "3:42 PM" — server TZ. */
export function fargoTime(input: string | number | Date | null | undefined): string {
  const d = safeDate(input);
  if (!d) return "";
  return d.toLocaleString("en-US", {
    timeZone: SERVER_TZ,
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

/** Long timestamp, e.g. "May 3, 2026, 3:42 PM" — server TZ. */
export function fargoDateTime(input: string | number | Date | null | undefined): string {
  const d = safeDate(input);
  if (!d) return "";
  return d.toLocaleString("en-US", {
    timeZone: SERVER_TZ,
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

/** Date only, e.g. "May 3, 2026" — server TZ. */
export function fargoDate(input: string | number | Date | null | undefined): string {
  const d = safeDate(input);
  if (!d) return "";
  return d.toLocaleDateString("en-US", {
    timeZone: SERVER_TZ,
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Friendly "Today" / "Yesterday" / weekday label — anchored on server TZ. */
export function fargoDayLabel(input: string | number | Date | null | undefined): string {
  const d = safeDate(input);
  if (!d) return "";
  // Compare day-strings in server TZ — not browser-local — so "Today" lines
  // up with what the device thinks the date is.
  const fmt = (x: Date) =>
    x.toLocaleDateString("en-US", {
      timeZone: SERVER_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  const now = new Date();
  const today = fmt(now);
  const yesterday = fmt(new Date(now.getTime() - 86_400_000));
  const thisDay = fmt(d);
  if (thisDay === today) return "Today";
  if (thisDay === yesterday) return "Yesterday";
  return d.toLocaleDateString("en-US", {
    timeZone: SERVER_TZ,
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}
