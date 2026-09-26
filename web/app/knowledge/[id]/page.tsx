// Server component thin wrapper. Required for `output: 'export'` because
// dynamic routes need `generateStaticParams`, which can't live in the same
// file as a "use client" client component.
//
// Same static-detail strategy as Client/Patient detail (`/patients/_/`):
// Next only prerenders a dummy `id: "_"`. Caddy's try_files serves that
// HTML for any `/careconnect/knowledge/{id}/` URL. The real id is read
// from window.location.pathname in KnowledgeWorkspaceClient.

import KnowledgeWorkspacePage from "./KnowledgeWorkspaceClient";

export function generateStaticParams() {
  return [{ id: "_" }];
}

export const dynamicParams = true;

export default function Page() {
  return <KnowledgeWorkspacePage />;
}
