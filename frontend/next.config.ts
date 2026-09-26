import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Section 200E: emit a self-contained server (`.next/standalone`) so the production image needs only the
  // traced runtime files -- no `node_modules` tree, no dev dependencies. `next dev` is unaffected.
  output: "standalone",
};

export default nextConfig;
