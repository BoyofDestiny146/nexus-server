"use client";

import Link from "next/link";
import { ShieldOff } from "lucide-react";
import { useAuthedUser } from "./RequireAuth";
import { EmptyState } from "./EmptyState";

/** Render `children` only if the current user is the root admin. Otherwise
 *  show a friendly forbidden state with a link back to /patients. */
export function RequireRoot({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuthedUser();
  if (!ready) return null;
  if (!user || user.role !== "root") {
    return (
      <EmptyState
        icon={ShieldOff}
        title="Root only"
        body="Managing administrators is restricted to the root account. Ask your root admin to grant you access if you need this view."
        action={
          <Link href="/patients" className="btn-secondary">
            Back to clients
          </Link>
        }
      />
    );
  }
  return <>{children}</>;
}
