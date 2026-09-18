"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser, type User } from "@/lib/api";

export function useAuthedUser(): { user: User | null; ready: boolean } {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setUser(getCurrentUser());
    setReady(true);
  }, []);
  return { user, ready };
}

/** Wrapper component: redirects to /login if unauthed. Renders nothing
 *  until the auth check has resolved (avoids flash of protected content). */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { user, ready } = useAuthedUser();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  if (!ready || !user) {
    return (
      <div className="min-h-screen grid place-items-center text-slate-muted text-[12px] uppercase tracking-[0.12em]">
        verifying session…
      </div>
    );
  }
  return <>{children}</>;
}
