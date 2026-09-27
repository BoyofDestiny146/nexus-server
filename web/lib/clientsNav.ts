/** Same value as `UNASSIGNED_ORGANIZATION_ID` in types.ts — inlined so node tests need no path alias. */
const UNASSIGNED_ORGANIZATION_ID = "unassigned";

export const ORGANIZATION_CLIENTS_VIEW = "organizations";

export const CLIENTS_NAV = {
  create: "/patients/new",
  organizationClients: `/patients?view=${ORGANIZATION_CLIENTS_VIEW}`,
  unassigned: `/patients?organization=${UNASSIGNED_ORGANIZATION_ID}`,
} as const;

export function isClientsSectionPath(pathname: string): boolean {
  return pathname === "/patients" || pathname.startsWith("/patients/");
}

export function isCreateClientPath(pathname: string): boolean {
  return pathname === "/patients/new" || pathname.startsWith("/patients/new/");
}

export function isUnassignedClientsView(
  pathname: string,
  organization: string | null,
): boolean {
  return pathname === "/patients" && organization === UNASSIGNED_ORGANIZATION_ID;
}

export function isOrganizationBrowseView(
  pathname: string,
  organization: string | null,
  view: string | null,
): boolean {
  return (
    pathname === "/patients" &&
    !organization &&
    view === ORGANIZATION_CLIENTS_VIEW
  );
}

export function isOrganizationClientsView(
  pathname: string,
  organization: string | null,
  view: string | null,
): boolean {
  if (pathname !== "/patients") return false;
  if (organization === UNASSIGNED_ORGANIZATION_ID) return false;
  if (isOrganizationBrowseView(pathname, organization, view)) return true;
  return Boolean(organization);
}

export function toggleClientsNavOpen(open: boolean): boolean {
  return !open;
}

export function defaultClientsNavOpen(pathname: string): boolean {
  return isClientsSectionPath(pathname);
}
