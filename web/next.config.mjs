/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export — no Node runtime in production. Caddy serves the `out/`
  // dir at /careconnect/ alongside the FastAPI on /api/* and /ws/*.
  output: "export",
  basePath: "/careconnect",
  trailingSlash: true,
  images: { unoptimized: true },  // required for static export
  // Strict mode catches double-renders and async lifecycle bugs.
  reactStrictMode: true,
};
export default nextConfig;
