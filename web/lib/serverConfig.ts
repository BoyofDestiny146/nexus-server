/**
 * Single source of truth for all server-side URLs used in the frontend.
 *
 * Build-time env vars (set in your .env.local or CI environment):
 *
 *   NEXT_PUBLIC_SERVER_BASE   Base URL for the careconnect server.
 *                             Default: https://haizel.online
 *
 *   NEXT_PUBLIC_OTA_URL       OTA firmware update URL for W1-A (XiaoZhi path).
 *                             Default: https://haizel.online/xiaozhi/ota/
 *
 * WebSocket base is derived from SERVER_BASE (https → wss).
 */

const SERVER_BASE =
  process.env.NEXT_PUBLIC_SERVER_BASE?.replace(/\/$/, "") ?? "https://haizel.online";

/** OTA URL for W1-A XiaoZhi firmware configuration.
 *  Devices reach OTA on the dedicated ota. host that the Cloudflare tunnel routes
 *  straight to xiaozhi-server (bypassing Caddy). */
export const OTA_URL =
  process.env.NEXT_PUBLIC_OTA_URL ?? "https://ota.nexus.warehouse-13.biz/xiaozhi/ota/";

/** WebSocket base — used by useLiveChat for the /ws/* routes. */
export const WS_BASE =
  SERVER_BASE.replace(/^https:/, "wss:").replace(/^http:/, "ws:");

/**
 * Returns the URL to show in the device-setup panel for a given firmware type.
 * Only XiaoZhi (W1-A) is supported now that W1-B is removed from the UI.
 */
export function deviceSetupUrl(): string {
  return OTA_URL;
}
