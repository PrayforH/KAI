import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  devIndicators: false,
  async redirects() {
    return [
      { source: "/studio", destination: "/studio/agents", permanent: false },
      { source: "/automation", destination: "/studio/automation", permanent: false },
      { source: "/knowledge/:path*", destination: "/studio/knowledge/:path*", permanent: false },
      { source: "/studio/assistant", destination: "/assistant", permanent: false },
    ];
  },
};

export default nextConfig;
