"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiGet, apiPost, getCurrentUser, setSession, type User } from "@/lib/api";
import { Loader2, ArrowRight } from "lucide-react";

interface LoginResp {
  token: string;
  expire: number;
  expireAt: string;
  clientHash: string;
}

export default function LoginPage() {
  const router = useRouter();
  const usernameRef = useRef<HTMLInputElement>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If already signed in, hop straight to /patients.
  useEffect(() => {
    if (getCurrentUser()) router.replace("/patients");
    else usernameRef.current?.focus();
  }, [router]);

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      const tok = await apiPost<LoginResp>("/user/login", { username, password });
      // Re-issue a tiny localStorage write before the user fetch so the
      // /user/info call carries the bearer.
      window.localStorage.setItem("careconnect.token", tok.token);
      const me = await apiGet<User>("/user/info");
      setSession(tok.token, me);
      router.replace("/patients");
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Unable to reach the server.";
      setError(msg.toLowerCase().includes("incorrect") || msg === "Unauthorized"
        ? "Username or password is incorrect."
        : msg);
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen relative grid place-items-center px-4 paper-grid">
      {/* Watermark — large soft serif behind the card */}
      <div
        aria-hidden
        className="pointer-events-none select-none absolute inset-0 grid place-items-center overflow-hidden"
      >
        <div className="font-display tracking-display text-slate-line/50 text-[22vw] leading-none whitespace-nowrap -rotate-[1deg] -translate-y-2">
          careconnect
        </div>
      </div>

      <div className="relative w-full max-w-md">
        <div className="card p-9 bg-white">
          <div className="kicker mb-3">Staff console</div>
          <h1 className="display-2 text-slate-deep">Sign in</h1>
          <p className="mt-2 text-[14px] text-slate-muted leading-relaxed">
            Local install. Your session is held only on this device.
          </p>

          <form onSubmit={submit} className="mt-7 space-y-5">
            <div>
              <label htmlFor="username" className="label">Username</label>
              <input
                id="username"
                ref={usernameRef}
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="input"
                disabled={busy}
                required
              />
            </div>
            <div>
              <label htmlFor="password" className="label">Password</label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input"
                disabled={busy}
                required
              />
            </div>

            {error && (
              <div
                role="alert"
                className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2"
              >
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={busy || !username || !password}
              className="btn-primary w-full py-2.5"
            >
              {busy ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-[11px] uppercase tracking-[0.16em] text-slate-muted">
          careconnect — local install · LAN only
        </p>
      </div>
    </div>
  );
}
