import type { ReactNode } from "react";
import { StudioUnifiedShell } from "../../components/agent-studio/studio-unified-shell";
import { AuthProvider } from "../../components/auth-provider";

export default function StudioLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <StudioUnifiedShell>{children}</StudioUnifiedShell>
    </AuthProvider>
  );
}
