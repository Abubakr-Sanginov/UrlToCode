import { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAccount } from "../../hooks/useAccount";

/**
 * Keeps the app behind a sign-in.
 *
 * Nothing behind this renders until the server has said who is asking, so a
 * signed-out visitor never sees the editor flash up before being sent away.
 * They are sent to the welcome page instead of being shown a dialog over the
 * app: that page is where the product is explained and where signing in or
 * registering starts.
 *
 * The query string travels with them. A failed sign-in at GitHub or Google
 * comes back to `/?signInError=...`, and the welcome page is the one that
 * turns that into a message.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { signedIn, loading } = useAccount();
  const location = useLocation();

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas text-foreground">
        <span className="font-mono text-xs text-muted-foreground">Loading…</span>
      </div>
    );
  }

  if (!signedIn) {
    return <Navigate to={`/welcome${location.search}`} replace />;
  }

  return <>{children}</>;
}
