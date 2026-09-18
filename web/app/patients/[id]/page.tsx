// Server component thin wrapper.  Required for `output: 'export'` because
// dynamic routes need `generateStaticParams`, which can't live in the same
// file as a "use client" client component.  The actual UI is in
// PatientDetailClient.tsx (sibling, "use client"); we hand control to it
// once Next has matched the dynamic segment.

import PatientDetailClient from "./PatientDetailClient";

// Returning a single dummy entry satisfies `output: 'export'` requirement
// to enumerate all dynamic params at build time. The real id is read
// client-side from `useParams()` inside PatientDetailClient.
export function generateStaticParams() {
  return [{ id: "_" }];
}

export const dynamicParams = true;

export default function Page() {
  return <PatientDetailClient />;
}
