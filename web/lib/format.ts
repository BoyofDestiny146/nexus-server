/** Small formatting helpers shared across pages.
 *
 *  All absolute timestamps are rendered in the server's configured timezone
 *  (`America/Chicago` by default). See `lib/time.ts`. Rendering in
 *  browser-local TZ would mislead remote on-call admins.
 */

import { fargoDate, fargoDateTime, fargoDayLabel, fargoTime } from "./time";

const RTF = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const now = Date.now();
  const diffSec = Math.round((t - now) / 1000);
  const abs = Math.abs(diffSec);
  if (abs < 60)             return RTF.format(diffSec, "second");
  if (abs < 3600)           return RTF.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400)          return RTF.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 14)     return RTF.format(Math.round(diffSec / 86400), "day");
  if (abs < 86400 * 60)     return RTF.format(Math.round(diffSec / 86400 / 7), "week");
  // Older than ~2 months: show the absolute date in server TZ.
  return fargoDate(iso);
}

/** Time-of-day for chat bubbles etc. Always server TZ. */
export function shortTime(iso: string | null | undefined): string {
  return fargoTime(iso);
}

/** Day divider label ("Today" / "Yesterday" / weekday). Anchored on server TZ. */
export function dayLabel(iso: string): string {
  return fargoDayLabel(iso);
}

/** Long timestamp ("May 3, 2026, 3:42 PM") — server TZ. */
export function longTime(iso: string | null | undefined): string {
  return fargoDateTime(iso);
}

export function ageFromDob(dob: string | null | undefined): number | null {
  if (!dob) return null;
  const d = new Date(dob);
  if (Number.isNaN(d.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - d.getFullYear();
  const m = now.getMonth() - d.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < d.getDate())) age--;
  return age;
}

export function classNames(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(" ");
}
