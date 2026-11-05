import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  devIndicators: false,
  async redirects() {
    return [{ source: "/studio/assistant", destination: "/assistant", permanent: false }];
  },
};

export default nextConfig;
