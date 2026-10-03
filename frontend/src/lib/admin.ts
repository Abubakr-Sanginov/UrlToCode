import { HTTP_BACKEND_URL } from "../config";

/**
 * The admin screen's own small client.
 *
 * The token is held in sessionStorage rather than localStorage: it opens
 * every account on the server, so closing the tab should close it too. It
 * is never put into the app's settings or bundled - it is typed in by the
 * person who owns it.
 */
const TOKEN_KEY = "utc.admin-token";

export interface AdminAccount {
  id: number;
  email: string;
  tier: string;
  createdAt: number;
  dailyActions: number;
  maxProjects: number;
  usedGenerate: number;
  usedEdit: number;
  projects: number;
  overrideDailyActions: number | null;
  overrideMaxProjects: number | null;
  note: string;
}

export function adminToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function rememberAdminToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function forgetAdminToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

async function call<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${HTTP_BACKEND_URL}${path}`, {
    ...init,
    headers: {
      "X-Admin-Token": token,
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(
      typeof detail.detail === "string" ? detail.detail : "That did not work."
    );
  }
  return (await response.json()) as T;
}

export function fetchAccounts(
  token: string
): Promise<{ accounts: AdminAccount[]; tiers: Record<string, Record<string, number>> }> {
  return call("/api/admin/accounts", token);
}

export function saveLimits(
  token: string,
  accountId: number,
  limits: { dailyActions: number | null; maxProjects: number | null; note: string }
): Promise<AdminAccount> {
  return call(`/api/admin/accounts/${accountId}/limits`, token, {
    method: "POST",
    body: JSON.stringify(limits),
  });
}

export function resetLimits(token: string, accountId: number): Promise<AdminAccount> {
  return call(`/api/admin/accounts/${accountId}/limits`, token, { method: "DELETE" });
}

export function setTier(
  token: string,
  accountId: number,
  tier: string
): Promise<AdminAccount> {
  return call(`/api/admin/accounts/${accountId}/tier`, token, {
    method: "POST",
    body: JSON.stringify({ tier }),
  });
}
