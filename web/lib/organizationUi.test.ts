import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { EMPTY_CLIENT_DRAFT, draftFromAgent } from "./clientForm.ts";
import { UNASSIGNED_ORGANIZATION_ID } from "./types.ts";
import {
  CLIENTS_NAV,
  defaultClientsNavOpen,
  isClientsSectionPath,
  isCreateClientPath,
  isOrganizationBrowseView,
  isOrganizationClientsView,
  isUnassignedClientsView,
  ORGANIZATION_CLIENTS_VIEW,
  toggleClientsNavOpen,
} from "./clientsNav.ts";

const here = dirname(fileURLToPath(import.meta.url));

test("new clients default to Unassigned organization", () => {
  assert.equal(EMPTY_CLIENT_DRAFT.organizationId, "");
});

test("draftFromAgent preloads saved organizationId", () => {
  const draft = draftFromAgent({
    agentName: "Jane",
    botName: null,
    dob: null,
    age: null,
    condition: null,
    tags: [],
    escalationPhrases: [],
    topicsToAvoid: [],
    personaOverride: null,
    personalityId: "sys_witty_tech_sidekick",
    organizationId: "org-north",
  });
  assert.equal(draft.organizationId, "org-north");
});

test("old top CLIENTS heading is gone and All Clients is not in the sidebar", () => {
  const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
  assert.doesNotMatch(shell, /uppercase tracking-\[0\.14em\] text-slate-muted">Clients</);
  assert.doesNotMatch(shell, /All Clients/);
  assert.doesNotMatch(shell, /orgs\.map/);
  assert.doesNotMatch(shell, /setOrgs/);
  assert.doesNotMatch(shell, /apiGet<\{ organizations/);
});

test("Clients exists as a primary expandable nav item with the approved submenu", () => {
  const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
  assert.match(shell, />Clients</);
  assert.match(shell, /aria-expanded=\{clientsOpen\}/);
  assert.match(shell, /aria-controls="clients-submenu"/);
  assert.match(shell, /id="clients-submenu"/);
  assert.match(shell, /toggleClientsNavOpen/);
  assert.match(shell, /Create New Client/);
  assert.match(shell, /Organization Clients/);
  assert.match(shell, /Unassigned/);
  assert.match(shell, /href=\{CLIENTS_NAV\.create\}/);
  assert.match(shell, /href=\{CLIENTS_NAV\.organizationClients\}/);
  assert.match(shell, /href=\{CLIENTS_NAV\.unassigned\}/);
});

test("Clients submenu indentation matches Administration children", () => {
  const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
  assert.match(shell, /nested \? "pl-8 pr-3 py-1\.5 text-\[13px\]"/);
  assert.match(shell, /label="Create New Client"[\s\S]*nested/);
  assert.match(shell, /label="Organization Clients"[\s\S]*nested/);
  assert.match(shell, /label="Unassigned"[\s\S]*nested/);
  assert.match(shell, /label="Portal Access"[\s\S]*nested/);
  assert.match(shell, /label="Organizations"[\s\S]*nested/);
  assert.match(shell, /w-60 shrink-0/);
});

test("Administration Portal Access and Organizations nav still works", () => {
  const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
  const admins = readFileSync(join(here, "../app/admins/page.tsx"), "utf8");
  assert.match(shell, /Administration/);
  assert.match(shell, /label="Portal Access"/);
  assert.doesNotMatch(shell, /label="Users"/);
  assert.match(shell, /href="\/admins"/);
  assert.match(shell, /href="\/organizations"/);
  assert.match(shell, /label="Organizations"/);
  assert.match(admins, /title="Portal Access"/);
  assert.match(admins, /Manage staff and administrators who can access the Nexus portal/);
  assert.match(admins, /apiGet<AdminSummary\[]>\("\/admin\/users"\)/);
});

test("Nexus logo remains in the sidebar header", () => {
  const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
  assert.match(shell, /nexus-logo\.png/);
  assert.match(shell, /alt="Nexus"/);
  assert.doesNotMatch(shell, /Local install/i);
  assert.doesNotMatch(shell, />\s*careconnect\s*</);
});

test("Clients nav helpers expand, collapse, and route to existing workflows", () => {
  assert.equal(CLIENTS_NAV.create, "/patients/new");
  assert.equal(CLIENTS_NAV.organizationClients, `/patients?view=${ORGANIZATION_CLIENTS_VIEW}`);
  assert.equal(CLIENTS_NAV.unassigned, `/patients?organization=${UNASSIGNED_ORGANIZATION_ID}`);
  assert.equal(UNASSIGNED_ORGANIZATION_ID, "unassigned");

  assert.equal(isClientsSectionPath("/patients"), true);
  assert.equal(isClientsSectionPath("/patients/new"), true);
  assert.equal(isClientsSectionPath("/devices"), false);
  assert.equal(isCreateClientPath("/patients/new"), true);
  assert.equal(isCreateClientPath("/patients"), false);

  assert.equal(isUnassignedClientsView("/patients", "unassigned"), true);
  assert.equal(isUnassignedClientsView("/patients", "org-1"), false);
  assert.equal(isUnassignedClientsView("/patients", null), false);

  assert.equal(isOrganizationBrowseView("/patients", null, "organizations"), true);
  assert.equal(isOrganizationBrowseView("/patients", "org-1", "organizations"), false);
  assert.equal(isOrganizationClientsView("/patients", null, "organizations"), true);
  assert.equal(isOrganizationClientsView("/patients", "org-1", null), true);
  assert.equal(isOrganizationClientsView("/patients", "unassigned", null), false);
  assert.equal(isOrganizationClientsView("/patients", null, null), false);

  assert.equal(defaultClientsNavOpen("/patients"), true);
  assert.equal(defaultClientsNavOpen("/devices"), false);
  assert.equal(toggleClientsNavOpen(true), false);
  assert.equal(toggleClientsNavOpen(false), true);
});

test("Organization Clients view exposes org selection and Unassigned still filters", () => {
  const patients = readFileSync(join(here, "../app/patients/page.tsx"), "utf8");
  const profile = readFileSync(join(here, "../components/clientForm/ProfileFields.tsx"), "utf8");
  assert.match(patients, /organizationId=/);
  assert.match(patients, /view === ORGANIZATION_CLIENTS_VIEW|isOrganizationBrowseView/);
  assert.match(patients, /aria-label="Organization selection"/);
  assert.match(patients, /Choose an organization to see the clients assigned to it/);
  assert.match(patients, /\/agent\/list\?organizationId=/);
  assert.match(patients, /UNASSIGNED_ORGANIZATION_ID/);
  assert.match(profile, /Organization/);
});

test("sidebar cleanup does not rewrite personality, voice, knowledge, assessment, or Revel gates", () => {
  const personality = readFileSync(join(here, "../app/personalities/page.tsx"), "utf8");
  const knowledge = readFileSync(join(here, "../app/knowledge/page.tsx"), "utf8");
  const envExample = readFileSync(join(here, "../../deploy/.env.example"), "utf8");
  assert.match(personality, /Agent Personality|Personalit/);
  assert.match(knowledge, /Knowledge/);
  assert.match(envExample, /^REVEL_EXECUTE_ENABLED=false$/m);
});
