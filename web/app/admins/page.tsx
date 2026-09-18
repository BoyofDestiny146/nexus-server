"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Plus, Trash2, KeyRound, Shield, ShieldCheck, Loader2,
} from "lucide-react";
import { apiDelete, apiGet, apiPost, apiPut, ApiError, getCurrentUser } from "@/lib/api";
import type { AdminSummary, AgentSummary } from "@/lib/types";
import { classNames, relativeTime } from "@/lib/format";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireRoot } from "@/components/RequireRoot";
import { Modal } from "@/components/Modal";
import { ToastProvider, useToast } from "@/components/Toast";

interface PagedAgents {
  list: AgentSummary[];
  total: number;
}

function AdminsView() {
  const me = getCurrentUser();
  const toast = useToast();

  const [admins, setAdmins] = useState<AdminSummary[] | null>(null);
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [scopeOpen, setScopeOpen] = useState<AdminSummary | null>(null);
  const [resetOpen, setResetOpen] = useState<AdminSummary | null>(null);
  const [deleteOpen, setDeleteOpen] = useState<AdminSummary | null>(null);

  const reload = useCallback(async () => {
    try {
      const [a, g] = await Promise.all([
        apiGet<AdminSummary[]>("/admin/users"),
        apiGet<PagedAgents>("/agent/all?page=1&limit=200"),
      ]);
      setAdmins(a);
      setAgents(g.list ?? []);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load admins.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const rootCount = (admins ?? []).filter((a) => a.role === 2 && a.status === 1).length;

  return (
    <>
      <PageHeader
        kicker="Access"
        title="Administrators"
        subtitle="Add other staff to this dashboard. Scope an admin to specific clients to limit what they can see."
        actions={
          <button onClick={() => setCreateOpen(true)} className="btn-primary">
            <Plus size={16} /> Add admin
          </button>
        }
      />

      <section className="px-8 md:px-12 py-8">
        {error && (
          <div className="card p-6 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[14px] mb-6">
            {error}
          </div>
        )}

        {loading && !admins && <div className="card p-8 skeleton h-64" />}

        {admins && (
          <div className="card overflow-hidden">
            <table className="w-full text-[14px]">
              <thead>
                <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                  <th className="text-left font-medium px-5 py-3">Username</th>
                  <th className="text-left font-medium px-5 py-3">Role</th>
                  <th className="text-left font-medium px-5 py-3">Status</th>
                  <th className="text-left font-medium px-5 py-3">Scope</th>
                  <th className="text-left font-medium px-5 py-3">Created</th>
                  <th className="text-right font-medium px-5 py-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {admins.map((a) => {
                  const isSelf = a.id === me?.id;
                  const isOnlyRoot = a.role === 2 && rootCount === 1;
                  return (
                    <tr key={a.id} className="border-b border-slate-line/50 last:border-b-0">
                      <td className="px-5 py-4">
                        <div className="flex items-center gap-3">
                          <div className="w-7 h-7 rounded-full bg-teal-tint text-teal-deep grid place-items-center text-[11px] font-semibold tracking-tight">
                            {a.username.slice(0, 2).toUpperCase()}
                          </div>
                          <div>
                            <div className="text-slate-deep tracking-tight">{a.username}</div>
                            {isSelf && <div className="text-[11px] text-slate-muted">you</div>}
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <RoleBadge role={a.roleName} />
                      </td>
                      <td className="px-5 py-4">
                        {a.status === 1
                          ? <span className="chip-teal">active</span>
                          : <span className="chip">disabled</span>}
                      </td>
                      <td className="px-5 py-4 text-slate-muted">
                        {a.role === 2 ? (
                          <span className="text-slate-deep">All clients</span>
                        ) : (
                          <button
                            onClick={() => setScopeOpen(a)}
                            className="text-teal-deep hover:underline text-[13px]"
                          >
                            {a.scopedAgentCount} {a.scopedAgentCount === 1 ? "client" : "clients"}
                          </button>
                        )}
                      </td>
                      <td className="px-5 py-4 text-slate-muted text-[12px]">
                        {a.createDate ? relativeTime(a.createDate) : "—"}
                      </td>
                      <td className="px-5 py-4">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => setResetOpen(a)}
                            className="btn-ghost text-[12px]"
                            title="Reset password"
                          >
                            <KeyRound size={12} /> Reset
                          </button>
                          <button
                            onClick={() => setDeleteOpen(a)}
                            disabled={isSelf || isOnlyRoot}
                            className="btn-ghost text-[12px] disabled:opacity-30"
                            title={isSelf ? "Cannot delete yourself" : isOnlyRoot ? "Cannot delete the only root" : "Delete"}
                          >
                            <Trash2 size={12} /> Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {createOpen && (
        <CreateAdminModal
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          agents={agents ?? []}
          onCreated={(name) => {
            setCreateOpen(false);
            reload();
            toast.push(`${name} created.`, "success");
          }}
        />
      )}

      {scopeOpen && (
        <ScopeModal
          open={!!scopeOpen}
          onClose={() => setScopeOpen(null)}
          admin={scopeOpen}
          agents={agents ?? []}
          onSaved={() => {
            setScopeOpen(null);
            reload();
            toast.push("Client scope updated.", "success");
          }}
        />
      )}

      {resetOpen && (
        <ResetPasswordModal
          open={!!resetOpen}
          onClose={() => setResetOpen(null)}
          admin={resetOpen}
          onDone={() => {
            setResetOpen(null);
            toast.push("Password updated.", "success");
          }}
        />
      )}

      {deleteOpen && (
        <DeleteConfirm
          open={!!deleteOpen}
          onClose={() => setDeleteOpen(null)}
          admin={deleteOpen}
          onDone={() => {
            setDeleteOpen(null);
            reload();
            toast.push("Admin disabled.", "info");
          }}
        />
      )}
    </>
  );
}

function RoleBadge({ role }: { role: "viewer" | "admin" | "root" }) {
  if (role === "root") return (
    <span className="inline-flex items-center gap-1.5 chip-teal">
      <ShieldCheck size={11} /> Root
    </span>
  );
  if (role === "admin") return (
    <span className="inline-flex items-center gap-1.5 chip">
      <Shield size={11} /> Admin
    </span>
  );
  return <span className="chip">{role}</span>;
}

function CreateAdminModal({
  open, onClose, agents, onCreated,
}: {
  open: boolean; onClose: () => void; agents: AgentSummary[];
  onCreated: (name: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<1 | 2>(1);
  const [scoped, setScoped] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggle(id: string) {
    setScoped((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (busy) return;
    if (password.length < 8) { setErr("Password must be at least 8 characters."); return; }
    setBusy(true); setErr(null);
    try {
      await apiPost("/admin/users", {
        username,
        password,
        role,
        scopedAgentIds: role === 2 ? [] : Array.from(scoped),
      });
      onCreated(username);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not create admin.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open} onClose={onClose} title="Add admin" size="lg"
      footer={
        <>
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={() => submit()} disabled={busy || !username || !password} className="btn-primary">
            {busy ? <Loader2 size={14} className="animate-spin" /> : null}
            Create admin
          </button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="u" className="label">Username</label>
            <input id="u" className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
          </div>
          <div>
            <label htmlFor="p" className="label">Password</label>
            <input id="p" type="password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
            <div className="helper">Minimum 8 characters.</div>
          </div>
        </div>

        <div>
          <label className="label">Role</label>
          <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={() => setRole(1)}
              className={classNames("text-left card p-3 transition", role === 1 ? "border-teal/50 bg-teal-tint/40" : "card-hover")}>
              <div className="flex items-center gap-2 text-slate-deep">
                <Shield size={14} /> <span className="font-medium">Admin</span>
              </div>
              <div className="text-[12px] text-slate-muted mt-1">Sees only assigned clients.</div>
            </button>
            <button type="button" onClick={() => setRole(2)}
              className={classNames("text-left card p-3 transition", role === 2 ? "border-teal/50 bg-teal-tint/40" : "card-hover")}>
              <div className="flex items-center gap-2 text-slate-deep">
                <ShieldCheck size={14} /> <span className="font-medium">Root</span>
              </div>
              <div className="text-[12px] text-slate-muted mt-1">Sees all clients. Promotes itself.</div>
            </button>
          </div>
        </div>

        {role === 1 && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="label !mb-0">Client scope</label>
              <span className="text-[11px] text-slate-muted num">
                {scoped.size} selected
              </span>
            </div>
            <div className="border border-slate-line/70 rounded-card max-h-56 overflow-y-auto divide-y divide-slate-line/50">
              {agents.length === 0 && <div className="p-4 text-[13px] text-slate-muted">No clients yet.</div>}
              {agents.map((a) => {
                const checked = scoped.has(a.id);
                return (
                  <label key={a.id} className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-bone-soft transition">
                    <input type="checkbox" checked={checked} onChange={() => toggle(a.id)} className="accent-teal" />
                    <span className="flex-1 text-slate-deep text-[14px]">{a.agentName}</span>
                    <span className="font-mono text-[10px] text-slate-muted">{a.id.slice(0, 10)}…</span>
                  </label>
                );
              })}
            </div>
          </div>
        )}

        {err && (
          <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
            {err}
          </div>
        )}
      </form>
    </Modal>
  );
}

function ScopeModal({
  open, onClose, admin, agents, onSaved,
}: {
  open: boolean; onClose: () => void; admin: AdminSummary; agents: AgentSummary[]; onSaved: () => void;
}) {
  const [scoped, setScoped] = useState<Set<string>>(new Set(admin.scopedAgentIds ?? []));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function toggle(id: string) {
    setScoped((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      await apiPut(`/admin/users/${admin.id}`, { scopedAgentIds: Array.from(scoped) });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save scope.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open} onClose={onClose} title={`Manage scope · ${admin.username}`} size="lg"
      footer={
        <>
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={save} disabled={busy} className="btn-primary">
            {busy && <Loader2 size={14} className="animate-spin" />} Save scope
          </button>
        </>
      }
    >
      <p className="text-[13px] text-slate-muted leading-relaxed mb-4">
        Tick the clients <span className="font-medium text-slate-deep">{admin.username}</span> should see. Root admins always see everyone.
      </p>
      <div className="border border-slate-line/70 rounded-card max-h-72 overflow-y-auto divide-y divide-slate-line/50">
        {agents.map((a) => {
          const checked = scoped.has(a.id);
          return (
            <label key={a.id} className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-bone-soft transition">
              <input type="checkbox" checked={checked} onChange={() => toggle(a.id)} className="accent-teal" />
              <span className="flex-1 text-slate-deep text-[14px]">{a.agentName}</span>
              <span className="font-mono text-[10px] text-slate-muted">{a.id.slice(0, 10)}…</span>
            </label>
          );
        })}
      </div>
      {err && (
        <div className="mt-4 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
          {err}
        </div>
      )}
    </Modal>
  );
}

function ResetPasswordModal({
  open, onClose, admin, onDone,
}: {
  open: boolean; onClose: () => void; admin: AdminSummary; onDone: () => void;
}) {
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (pw.length < 8) { setErr("Minimum 8 characters."); return; }
    if (pw !== confirm) { setErr("Passwords don't match."); return; }
    setBusy(true); setErr(null);
    try {
      await apiPost(`/admin/users/${admin.id}/reset-password`, { password: pw });
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not reset.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open} onClose={onClose} title={`Reset password · ${admin.username}`} size="sm"
      footer={
        <>
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={() => submit()} disabled={busy || !pw || !confirm} className="btn-primary">
            {busy && <Loader2 size={14} className="animate-spin" />} Set password
          </button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label htmlFor="np" className="label">New password</label>
          <input id="np" type="password" className="input" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
        </div>
        <div>
          <label htmlFor="cp" className="label">Confirm</label>
          <input id="cp" type="password" className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        {err && (
          <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
            {err}
          </div>
        )}
      </form>
    </Modal>
  );
}

function DeleteConfirm({
  open, onClose, admin, onDone,
}: {
  open: boolean; onClose: () => void; admin: AdminSummary; onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function submit() {
    setBusy(true); setErr(null);
    try {
      await apiDelete(`/admin/users/${admin.id}`);
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not disable.");
      setBusy(false);
    }
  }
  return (
    <Modal
      open={open} onClose={onClose} title="Disable admin?" size="sm"
      footer={
        <>
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={submit} disabled={busy} className="btn-danger">
            {busy && <Loader2 size={14} className="animate-spin" />} Disable {admin.username}
          </button>
        </>
      }
    >
      <p className="text-[14px] text-slate-deep leading-relaxed">
        <span className="font-medium">{admin.username}</span> will no longer be able to sign in. Their account stays in the database for audit purposes; you can reactivate it from the database directly if needed.
      </p>
      {err && (
        <div className="mt-4 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
          {err}
        </div>
      )}
    </Modal>
  );
}

export default function AdminsPage() {
  return (
    <RequireAuth>
      <ToastProvider>
        <AppShell>
          <RequireRoot>
            <AdminsView />
          </RequireRoot>
        </AppShell>
      </ToastProvider>
    </RequireAuth>
  );
}
