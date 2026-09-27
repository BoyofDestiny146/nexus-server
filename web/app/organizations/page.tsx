"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Plus, Pencil, Loader2, Building2 } from "lucide-react";
import { apiDelete, apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import type { Organization } from "@/lib/types";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireRoot } from "@/components/RequireRoot";
import { Modal } from "@/components/Modal";
import { ToastProvider, useToast } from "@/components/Toast";
import { EmptyState } from "@/components/EmptyState";

const EMPTY_DRAFT = {
  name: "",
  mainContactName: "",
  mainContactEmail: "",
  mainContactPhone: "",
  addressLine1: "",
  addressLine2: "",
  city: "",
  state: "",
  postalCode: "",
  country: "",
  notes: "",
};

type Draft = typeof EMPTY_DRAFT;

function fromOrg(row: Organization): Draft {
  return {
    name: row.name || "",
    mainContactName: row.mainContactName || "",
    mainContactEmail: row.mainContactEmail || "",
    mainContactPhone: row.mainContactPhone || "",
    addressLine1: row.addressLine1 || "",
    addressLine2: row.addressLine2 || "",
    city: row.city || "",
    state: row.state || "",
    postalCode: row.postalCode || "",
    country: row.country || "",
    notes: row.notes || "",
  };
}

function OrganizationsView() {
  const toast = useToast();
  const [rows, setRows] = useState<Organization[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [edit, setEdit] = useState<Organization | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<Organization | null>(null);

  const reload = useCallback(async () => {
    try {
      const data = await apiGet<{ organizations: Organization[] }>("/organizations?includeInactive=true");
      setRows(data.organizations || []);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load organizations.");
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  function openCreate() {
    setDraft(EMPTY_DRAFT);
    setCreateOpen(true);
  }

  async function openEdit(row: Organization) {
    try {
      const full = await apiGet<Organization>(`/organizations/${row.id}`);
      setEdit(full);
      setDraft(fromOrg(full));
      setDetail(full);
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Could not load organization.", "error");
    }
  }

  async function save(create: boolean) {
    if (busy || !draft.name.trim()) return;
    setBusy(true);
    try {
      const body = {
        name: draft.name.trim(),
        mainContactName: draft.mainContactName.trim() || null,
        mainContactEmail: draft.mainContactEmail.trim() || null,
        mainContactPhone: draft.mainContactPhone.trim() || null,
        addressLine1: draft.addressLine1.trim() || null,
        addressLine2: draft.addressLine2.trim() || null,
        city: draft.city.trim() || null,
        state: draft.state.trim() || null,
        postalCode: draft.postalCode.trim() || null,
        country: draft.country.trim() || null,
        notes: draft.notes.trim() || null,
      };
      if (create) {
        await apiPost("/organizations", body);
        toast.push("Organization created.", "success");
        setCreateOpen(false);
      } else if (edit) {
        await apiPut(`/organizations/${edit.id}`, body);
        toast.push("Organization saved.", "success");
        setEdit(null);
      }
      await reload();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Could not save organization.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function deactivate(row: Organization) {
    try {
      await apiPost(`/organizations/${row.id}/deactivate`);
      toast.push(`${row.name} deactivated. Clients stay linked.`, "success");
      await reload();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Could not deactivate.", "error");
    }
  }

  async function remove(row: Organization) {
    try {
      await apiDelete(`/organizations/${row.id}`);
      toast.push(`${row.name} deleted.`, "success");
      await reload();
    } catch (e) {
      toast.push(
        e instanceof ApiError ? e.message : "Could not delete. Reassign clients or deactivate instead.",
        "error",
      );
    }
  }

  return (
    <>
      <PageHeader
        kicker="Administration"
        title="Organizations"
        subtitle="Facilities and customer groups. Clients stay Unassigned until you link them. Deactivate instead of deleting an organization that still has clients."
        actions={
          <button type="button" className="btn-primary" onClick={openCreate}>
            <Plus size={16} /> Create organization
          </button>
        }
      />
      <section className="px-8 md:px-12 py-8">
        {error && (
          <div className="card p-6 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[14px] mb-6">
            {error}
          </div>
        )}
        {!rows && !error && <div className="card p-8 skeleton h-48" />}
        {rows && rows.length === 0 && (
          <EmptyState
            icon={Building2}
            title="No organizations yet"
            body="Create a facility or customer group, then assign clients from the Profile step."
            action={
              <button type="button" className="btn-primary" onClick={openCreate}>
                <Plus size={16} /> Create organization
              </button>
            }
          />
        )}
        {rows && rows.length > 0 && (
          <div className="card overflow-hidden">
            <table className="w-full text-[14px]">
              <thead>
                <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                  <th className="text-left font-medium px-5 py-3">Name</th>
                  <th className="text-left font-medium px-5 py-3">Contact</th>
                  <th className="text-left font-medium px-5 py-3">Status</th>
                  <th className="text-left font-medium px-5 py-3">Clients</th>
                  <th className="text-right font-medium px-5 py-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-slate-line/50 last:border-b-0">
                    <td className="px-5 py-4">
                      <div className="text-slate-deep">{row.name}</div>
                      <div className="text-[12px] text-slate-muted">
                        {[row.city, row.state].filter(Boolean).join(", ") || "—"}
                      </div>
                    </td>
                    <td className="px-5 py-4 text-slate-muted">
                      <div>{row.mainContactName || "—"}</div>
                      <div className="text-[12px]">{row.mainContactEmail || row.mainContactPhone || ""}</div>
                    </td>
                    <td className="px-5 py-4">
                      {row.status === "active"
                        ? <span className="chip-teal">active</span>
                        : <span className="chip">inactive</span>}
                    </td>
                    <td className="px-5 py-4">
                      <Link
                        href={`/patients?organization=${encodeURIComponent(row.id)}`}
                        className="text-teal-deep hover:underline"
                      >
                        {row.clientCount ?? 0}
                      </Link>
                    </td>
                    <td className="px-5 py-4 text-right">
                      <div className="inline-flex gap-2">
                        <button type="button" className="btn-ghost text-[12px]" onClick={() => void openEdit(row)}>
                          <Pencil size={12} /> Edit
                        </button>
                        {row.status === "active" && (
                          <button type="button" className="btn-ghost text-[12px]" onClick={() => void deactivate(row)}>
                            Deactivate
                          </button>
                        )}
                        <button type="button" className="btn-ghost text-[12px] text-risk-urgent" onClick={() => void remove(row)}>
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <OrgFormModal
        open={createOpen}
        title="Create organization"
        draft={draft}
        setDraft={setDraft}
        busy={busy}
        onClose={() => !busy && setCreateOpen(false)}
        onSave={() => void save(true)}
      />
      <OrgFormModal
        open={!!edit}
        title="Edit organization"
        draft={draft}
        setDraft={setDraft}
        busy={busy}
        clients={detail?.clients}
        onClose={() => !busy && setEdit(null)}
        onSave={() => void save(false)}
      />
    </>
  );
}

function OrgFormModal({
  open,
  title,
  draft,
  setDraft,
  busy,
  onClose,
  onSave,
  clients,
}: {
  open: boolean;
  title: string;
  draft: Draft;
  setDraft: (d: Draft) => void;
  busy: boolean;
  onClose: () => void;
  onSave: () => void;
  clients?: Organization["clients"];
}) {
  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft({ ...draft, [key]: value });
  }
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="workspace"
      footer={
        <>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={busy || !draft.name.trim()} onClick={onSave}>
            {busy && <Loader2 size={14} className="animate-spin" />}
            Save
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div className="md:col-span-2">
          <label className="label" htmlFor="org-name">Organization name</label>
          <input id="org-name" className="input" value={draft.name} onChange={(e) => set("name", e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="org-contact">Main contact name</label>
          <input id="org-contact" className="input" value={draft.mainContactName} onChange={(e) => set("mainContactName", e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="org-email">Email</label>
          <input id="org-email" type="email" className="input" value={draft.mainContactEmail} onChange={(e) => set("mainContactEmail", e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="org-phone">Phone</label>
          <input id="org-phone" className="input" value={draft.mainContactPhone} onChange={(e) => set("mainContactPhone", e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="org-country">Country</label>
          <input id="org-country" className="input" value={draft.country} onChange={(e) => set("country", e.target.value)} />
        </div>
        <div className="md:col-span-2">
          <label className="label" htmlFor="org-a1">Address</label>
          <input id="org-a1" className="input" value={draft.addressLine1} onChange={(e) => set("addressLine1", e.target.value)} />
        </div>
        <div className="md:col-span-2">
          <label className="label" htmlFor="org-a2">Address line 2</label>
          <input id="org-a2" className="input" value={draft.addressLine2} onChange={(e) => set("addressLine2", e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="org-city">City</label>
          <input id="org-city" className="input" value={draft.city} onChange={(e) => set("city", e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="org-state">State</label>
          <input id="org-state" className="input" value={draft.state} onChange={(e) => set("state", e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="org-zip">Postal code</label>
          <input id="org-zip" className="input" value={draft.postalCode} onChange={(e) => set("postalCode", e.target.value)} />
        </div>
        <div className="md:col-span-2">
          <label className="label" htmlFor="org-notes">Notes</label>
          <textarea id="org-notes" rows={3} className="input" value={draft.notes} onChange={(e) => set("notes", e.target.value)} />
        </div>
        {clients && (
          <div className="md:col-span-2 border-t border-slate-line/70 pt-4">
            <div className="kicker mb-2">Clients</div>
            {clients.length === 0 ? (
              <p className="text-[13px] text-slate-muted">No clients linked yet.</p>
            ) : (
              <ul className="space-y-1">
                {clients.map((c) => (
                  <li key={c.id}>
                    <Link href={`/patients/${c.id}`} className="text-[14px] text-teal-deep hover:underline">
                      {c.agentName || c.id}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

export default function OrganizationsPage() {
  return (
    <RequireAuth>
      <ToastProvider>
        <AppShell>
          <RequireRoot>
            <OrganizationsView />
          </RequireRoot>
        </AppShell>
      </ToastProvider>
    </RequireAuth>
  );
}
