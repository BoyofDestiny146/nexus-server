"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import Image from "next/image";
import {
  Users, Cpu, Shield, Settings, LogOut, HeartPulse, BookOpen, Sparkles,
  Building2, Plus, ChevronDown,
} from "lucide-react";
import { clearSession } from "@/lib/api";
import { useAuthedUser } from "./RequireAuth";
import { classNames } from "@/lib/format";
import {
  CLIENTS_NAV,
  defaultClientsNavOpen,
  isClientsSectionPath,
  isCreateClientPath,
  isOrganizationClientsView,
  isUnassignedClientsView,
  toggleClientsNavOpen,
} from "@/lib/clientsNav";

const MAIN_NAV = [
  { href: "/devices", label: "Devices", icon: Cpu },
  { href: "/personalities", label: "Agent Personality", icon: Sparkles },
  { href: "/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/health", label: "System Status", icon: HeartPulse },
];

function NavLink({
  href,
  label,
  icon: Icon,
  active,
  nested = false,
}: {
  href: string;
  label: string;
  icon?: typeof Users;
  active: boolean;
  nested?: boolean;
}) {
  return (
    <Link
      href={href}
      className={classNames(
        "flex items-center gap-3 rounded-card text-[14px] tracking-tight transition border",
        nested ? "pl-8 pr-3 py-1.5 text-[13px]" : "px-3 py-2",
        active
          ? "bg-white border-slate-line/80 text-slate-deep"
          : "text-slate hover:text-slate-deep hover:bg-bone-soft border-transparent",
      )}
    >
      {Icon ? (
        <Icon size={nested ? 14 : 16} strokeWidth={1.75} className={active ? "text-teal" : "text-slate-muted"} />
      ) : null}
      <span className="truncate">{label}</span>
    </Link>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const searchParams = useSearchParams();
  const { user } = useAuthedUser();
  const [clientsOpen, setClientsOpen] = useState(() => defaultClientsNavOpen(pathname));

  function logout() {
    clearSession();
    router.push("/login");
  }

  const orgFilter = searchParams.get("organization");
  const orgView = searchParams.get("view");
  const onClients = isClientsSectionPath(pathname);
  const onCreateClient = isCreateClientPath(pathname);
  const onOrganizationClients = isOrganizationClientsView(pathname, orgFilter, orgView);
  const onUnassigned = isUnassignedClientsView(pathname, orgFilter);
  const onAdminUsers = pathname === "/admins" || pathname.startsWith("/administration/users");
  const onAdminOrgs = pathname === "/organizations" || pathname.startsWith("/administration/organizations");

  useEffect(() => {
    if (onClients) setClientsOpen(true);
  }, [onClients]);

  return (
    <div className="min-h-screen flex bg-bone">
      <aside className="w-60 shrink-0 border-r border-slate-line/80 bg-bone flex flex-col sticky top-0 h-screen">
        <div className="px-6 pt-7 pb-6 border-b border-slate-line/70">
          <Link href="/patients" className="block" aria-label="Nexus home">
            <Image
              src="/nexus-logo.png"
              alt="Nexus"
              width={2046}
              height={769}
              className="w-full h-auto"
              priority
            />
          </Link>
        </div>

        <nav className="flex-1 py-4 px-3 overflow-y-auto" aria-label="Primary">
          <ul className="space-y-0.5">
            <li>
              <button
                type="button"
                aria-expanded={clientsOpen}
                aria-controls="clients-submenu"
                onClick={() => setClientsOpen((open) => toggleClientsNavOpen(open))}
                className={classNames(
                  "w-full flex items-center gap-3 rounded-card text-[14px] tracking-tight transition border px-3 py-2",
                  onClients
                    ? "bg-white border-slate-line/80 text-slate-deep"
                    : "text-slate hover:text-slate-deep hover:bg-bone-soft border-transparent",
                )}
              >
                <Users size={16} strokeWidth={1.75} className={onClients ? "text-teal" : "text-slate-muted"} />
                <span className="truncate flex-1 text-left">Clients</span>
                <ChevronDown
                  size={14}
                  strokeWidth={1.75}
                  className={classNames(
                    "shrink-0 text-slate-muted transition-transform",
                    clientsOpen && "rotate-180",
                  )}
                />
              </button>
              {clientsOpen && (
                <ul id="clients-submenu" className="space-y-0.5 mt-0.5">
                  <li>
                    <NavLink
                      href={CLIENTS_NAV.create}
                      label="Create New Client"
                      icon={Plus}
                      nested
                      active={onCreateClient}
                    />
                  </li>
                  <li>
                    <NavLink
                      href={CLIENTS_NAV.organizationClients}
                      label="Organization Clients"
                      icon={Building2}
                      nested
                      active={onOrganizationClients}
                    />
                  </li>
                  <li>
                    <NavLink
                      href={CLIENTS_NAV.unassigned}
                      label="Unassigned"
                      nested
                      active={onUnassigned}
                    />
                  </li>
                </ul>
              )}
            </li>
            {MAIN_NAV.map((n) => (
              <li key={n.href}>
                <NavLink
                  href={n.href}
                  label={n.label}
                  icon={n.icon}
                  active={pathname === n.href || pathname.startsWith(n.href + "/")}
                />
              </li>
            ))}
          </ul>

          {user?.role === "root" && (
            <div className="mt-4">
              <div className="px-3 pb-1 text-[10px] uppercase tracking-[0.14em] text-slate-muted">Administration</div>
              <ul className="space-y-0.5">
                <li>
                  <NavLink href="/admins" label="Portal Access" icon={Shield} nested active={onAdminUsers} />
                </li>
                <li>
                  <NavLink href="/organizations" label="Organizations" icon={Building2} nested active={onAdminOrgs} />
                </li>
              </ul>
            </div>
          )}

          <ul className="space-y-0.5 mt-2">
            <li>
              <NavLink
                href="/settings"
                label="Settings"
                icon={Settings}
                active={pathname === "/settings" || pathname.startsWith("/settings/")}
              />
            </li>
          </ul>
        </nav>

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
