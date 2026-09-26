import KnowledgeWorkspacePage from "./KnowledgeWorkspaceClient";

export function generateStaticParams() {
  return [{ id: "_" }];
}

export const dynamicParams = true;

export default function Page() {
  return <KnowledgeWorkspacePage />;
}
