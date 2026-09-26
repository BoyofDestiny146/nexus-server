/**
 * Tiny fetch wrapper for the careconnect FastAPI backend.
 *
 * - Same-origin: Caddy proxies /api/* and /ws/* to the FastAPI service.
 * - Auth: JWT in localStorage as `careconnect.token` (set on login,
 *   cleared on 401).
 * - Response envelope is `{code, msg, data}`. We unwrap on success
 *   (code === 0) and throw `ApiError(code, msg)` otherwise.
 */
import { shouldClearSessionOn401 } from "@/lib/revelDiscover";

const TOKEN_KEY = "careconnect.token";
const USER_KEY = "careconnect.user";

export interface User {
  id: number;
  username: string;
  superAdmin: 0 | 1 | 2;
  role: "viewer" | "admin" | "root";
  status: number;
}

export class ApiError extends Error {
  constructor(public code: number, message: string) {
    super(message);
  }
}

function token(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setSession(t: string, u: User) {
  localStorage.setItem(TOKEN_KEY, t);
  localStorage.setItem(USER_KEY, JSON.stringify(u));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function getCurrentUser(): User | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.body ? { "Content-Type": "application/json" } : {}),
    ...(init.headers as Record<string, string> | undefined),
  };
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;

  const url = path.startsWith("/api/") || path.startsWith("/ws/") ? path : `/api${path.startsWith("/") ? "" : "/"}${path}`;
  const r = await fetch(url, { ...init, headers });
  let body: { code?: number; msg?: string; data?: T; error?: string; detail?: string } | null = null;
  try { body = await r.json(); } catch {}
  if (!body || typeof body.code !== "number") {
    const msg =
      (body && (body.msg || body.error || (typeof body.detail === "string" ? body.detail : undefined))) ||
      `unexpected ${r.status}`;
    throw new ApiError(r.status, String(msg));
  }
  if (body.code !== 0) {
    if (
      body.code === 401 &&
      typeof window !== "undefined" &&
      shouldClearSessionOn401(path, body.msg)
    ) {
      clearSession();
    }
    throw new ApiError(body.code, body.msg || body.error || `unexpected ${r.status}`);
  }
  return body.data as T;
}

// Convenience wrappers
export const apiGet = <T = unknown>(path: string) => api<T>(path);
export const apiPost = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined });
export const apiPut = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "PUT", body: body !== undefined ? JSON.stringify(body) : undefined });
export const apiPatch = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined });
export const apiDelete = <T = unknown>(path: string) => api<T>(path, { method: "DELETE" });

/** Multipart POST/PUT — do not set Content-Type so the browser supplies the boundary. */
export async function apiForm<T = unknown>(
  path: string,
  form: FormData,
  method: "POST" | "PUT" = "POST",
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  const url = path.startsWith("/api/") || path.startsWith("/ws/") ? path : `/api${path.startsWith("/") ? "" : "/"}${path}`;
  const r = await fetch(url, { method, headers, body: form });
  let body: { code?: number; msg?: string; data?: T; error?: string; detail?: string } | null = null;
  try { body = await r.json(); } catch {}
  if (!body || typeof body.code !== "number") {
    const msg =
      (body && (body.msg || body.error || (typeof body.detail === "string" ? body.detail : undefined))) ||
      `unexpected ${r.status}`;
    throw new ApiError(r.status, String(msg));
  }
  if (body.code !== 0) {
    if (
      body.code === 401 &&
      typeof window !== "undefined" &&
      shouldClearSessionOn401(path, body.msg)
    ) {
      clearSession();
    }
    throw new ApiError(body.code, body.msg || body.error || `unexpected ${r.status}`);
  }
  return body.data as T;
}

export async function apiDownload(path: string, filename: string): Promise<void> {
  const blob = await apiBinary(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Raw binary fetch — used for endpoints that return audio/wav blobs
 * (e.g. POST /api/voice/preview). Throws ApiError on non-2xx.
 */
export async function apiBinary(
  path: string,
  init: RequestInit = {},
): Promise<Blob> {
  const headers: Record<string, string> = {
    Accept: "*/*",
    ...(init.body ? { "Content-Type": "application/json" } : {}),
    ...(init.headers as Record<string, string> | undefined),
  };
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;

  const url = path.startsWith("/api/") ? path : `/api${path.startsWith("/") ? "" : "/"}${path}`;
  const r = await fetch(url, { ...init, headers });
  const ct = (r.headers.get("content-type") || "").toLowerCase();
  // Preview errors are CareConnect envelopes (HTTP 200, code !== 0) or raw
  // JSON 500s from TTS. Never treat those as a WAV blob.
  if (ct.includes("application/json")) {
    let j: { code?: number; msg?: string; error?: string; detail?: string } = {};
    try { j = await r.json(); } catch { /* ignore */ }
    const msg = j.msg || j.error || (typeof j.detail === "string" ? j.detail : "") || `unexpected ${r.status}`;
    throw new ApiError(typeof j.code === "number" ? j.code : r.status, msg);
  }
  if (!r.ok) {
    let msg = `unexpected ${r.status}`;
    try {
      const j: { msg?: string; error?: string; detail?: string } = await r.json();
      msg = j.msg || j.error || (typeof j.detail === "string" ? j.detail : "") || msg;
    } catch { /* ignore */ }
    throw new ApiError(r.status, msg);
  }
  return r.blob();
}

// Build a wss:// URL with token in query string. Browsers can't set headers on WS.
export function wsUrl(path: string): string {
  const t = token();
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  const sep = path.includes("?") ? "&" : "?";
  return `${proto}://${host}${path}${sep}token=${encodeURIComponent(t ?? "")}`;
}
