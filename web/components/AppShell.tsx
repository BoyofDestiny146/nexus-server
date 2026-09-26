"use client";

import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import {
  Users, Cpu, Shield, Settings, LogOut, Activity, HeartPulse, BookOpen,
} from "lucide-react";
import { clearSession } from "@/lib/api";
import { useAuthedUser } from "./RequireAuth";
import { classNames } from "@/lib/format";

const NAV = [
  { href: "/patients", label: "Clients",       icon: Users },
  { href: "/devices",  label: "Devices",        icon: Cpu },
  { href: "/knowledge", label: "Knowledge",     icon: BookOpen },
  { href: "/health",   label: "System Status",  icon: HeartPulse },
  { href: "/admins",   label: "Admins",          icon: Shield, rootOnly: true },
  { href: "/settings", label: "Settings",        icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const { user } = useAuthedUser();

  function logout() {
    clearSession();
    router.push("/login");
  }

  return (
    <div className="min-h-screen flex bg-bone">
      {/* Sidebar — narrow, hairline-bordered, monolithic. */}
      <aside className="w-60 shrink-0 border-r border-slate-line/80 bg-bone flex flex-col sticky top-0 h-screen">
        <div className="px-6 pt-7 pb-6 border-b border-slate-line/70">
          <Link href="/patients" className="block">
            <div className="font-display text-[28px] tracking-display leading-none text-slate-deep">
              careconnect
            </div>
            <div className="mt-1.5 text-[10px] uppercase tracking-[0.18em] text-slate-muted flex items-center gap-1.5">
              <Activity size={10} /> Local install
            </div>
          </Link>
        </div>

        <nav className="flex-1 py-4 px-3" aria-label="Primary">
          <ul className="space-y-0.5">
            {NAV.filter((n) => !n.rootOnly || user?.role === "root").map((n) => {
              const active = pathname === n.href || pathname.startsWith(n.href + "/");
              const Icon = n.icon;
              return (
                <li key={n.href}>
                  <Link
                    href={n.href}
                    className={classNames(
                      "flex items-center gap-3 px-3 py-2 rounded-card text-[14px] tracking-tight transition",
                      active
                        ? "bg-white border border-slate-line/80 text-slate-deep"
                        : "text-slate hover:text-slate-deep hover:bg-bone-soft border border-transparent",
                    )}
                  >
                    <Icon size={16} strokeWidth={1.75}
                          className={active ? "text-teal" : "text-slate-muted"} />
                    {n.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* User chip */}
        <div className="px-3 py-4 border-t border-slate-line/70">
          <div className="px-3 py-3 rounded-card border border-slate-line/70 bg-white">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-full bg-teal-tint text-teal-deep grid place-items-center text-[11px] font-semibold tracking-tight">
                {user?.username.slice(0, 2).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] tracking-tight text-slate-deep truncate">
                  {user?.username}
                </div>
                <div className="text-[10px] uppercase tracking-[0.14em] text-slate-muted">
                  {user?.role}
                </div>
              </div>
            </div>
            <button
              onClick={logout}
              className="mt-2.5 w-full flex items-center justify-center gap-2 text-[12px] text-slate-muted hover:text-slate-deep transition py-1.5 rounded border border-slate-line/60 hover:border-slate-line"
            >
              <LogOut size={12} /> Sign out
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 min-w-0">{children}</main>
    </div>
  );
}

/** A standardized page header used across the dashboard.  The serif "title"
 *  is the human anchor; the sans "kicker" above it tells you which section
 *  of the app you're in. */
export function PageHeader({
  kicker,
  title,
  subtitle,
  actions,
}: {
  kicker?: string;
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="px-8 md:px-12 pt-10 pb-8 border-b border-slate-line/70">
      <div className="flex items-end justify-between gap-6">
        <div>
          {kicker && <div className="kicker mb-2">{kicker}</div>}
          <h1 className="display-1 text-slate-deep">{title}</h1>
          {subtitle && (
            <p className="mt-3 text-[14px] text-slate-muted max-w-2xl leading-relaxed">
              {subtitle}
            </p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>
    </header>
  );
}
