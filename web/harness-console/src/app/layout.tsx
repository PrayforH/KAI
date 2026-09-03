import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "@assistant-ui/react-ui/styles/index.css";
import "@assistant-ui/react-ui/styles/markdown.css";
import "./styles.css";
import "./codex-theme.css";
import "./weknora-theme.css";
import "./web-codex.css";
import { PRODUCT_DESCRIPTION, PRODUCT_NAME } from "../components/product-brand";

export const metadata: Metadata = {
  title: {
    default: PRODUCT_NAME,
    template: `%s · ${PRODUCT_NAME}`,
  },
  description: PRODUCT_DESCRIPTION,
  applicationName: PRODUCT_NAME,
};

export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfcfb" },
    { media: "(prefers-color-scheme: dark)", color: "#101010" },
  ],
};

const colorModeScript = `
(() => {
  const key = "agent-harness-color-mode";
  try {
    const stored = window.localStorage.getItem(key);
    const mode = stored === "light" || stored === "dark"
      ? stored
      : window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    document.documentElement.dataset.colorMode = mode;
    document.documentElement.style.colorScheme = mode;
  } catch {
    document.documentElement.dataset.colorMode = "dark";
    document.documentElement.style.colorScheme = "dark";
  }
})();
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="zh-CN"
      data-theme="codex-theme-v1"
      data-product-ui="xushu"
      data-color-mode="dark"
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: colorModeScript }} />
      </head>
      <body className="codex-theme-v1">
        <a className="skip-link" href="#main-content">
          跳到主要内容
        </a>
        {children}
      </body>
    </html>
  );
}
