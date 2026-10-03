/**
 * Buying a plan in Stars.
 *
 * The server decides who is buying and what it costs; this only asks for a
 * link and hands it to Telegram. Nothing here decides whether a payment
 * worked - that arrives on the webhook, and the plan changes when it lands.
 */
import { HTTP_BACKEND_URL } from "../config";
import { telegramWebApp } from "./telegram";

export interface TariffTier {
  tier: string;
  dollars: number;
  stars: number;
  net: number;
}

export interface Invoice {
  url: string;
  tier: string;
  stars: number;
  dollars: number;
}

export interface Tariff {
  tiers: TariffTier[];
  commission: number;
}

export interface Payments {
  chargeId: string;
  tier: string;
  stars: number;
  createdAt: number;
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body && typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    // Not JSON. The status line will have to do.
  }
  return `${fallback} (${response.status})`;
}

export async function tariff(): Promise<Tariff> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/telegram/tariff`);
  if (!response.ok) throw new Error(await readError(response, "Could not load the plans."));
  return (await response.json()) as Tariff;
}

export async function myPayments(): Promise<Payments[]> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/telegram/payments`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error(await readError(response, "Could not load payments."));
  const body = (await response.json()) as { payments: Payments[] };
  return body.payments;
}

/**
 * Ask for a payment link, then open Telegram's own payment sheet.
 *
 * The signed blob Telegram put in the page goes along in a header. A
 * Mini App is an ordinary web page, so "I would like to pay" proves nothing
 * on its own - what identifies the buyer is what Telegram signed.
 *
 * Returns false rather than throwing when this is not a Mini App or there is
 * no payment sheet to open, so the caller can say why rather than looking
 * broken.
 */
export async function buyPlan(tier: string): Promise<boolean> {
  const app = telegramWebApp();
  if (!app || typeof app.openInvoiceLink !== "function") return false;

  const response = await fetch(`${HTTP_BACKEND_URL}/api/telegram/invoice`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Auth": app.initData,
    },
    credentials: "include",
    body: JSON.stringify({ tier }),
  });
  if (!response.ok) throw new Error(await readError(response, "Could not start the payment."));

  const invoice = (await response.json()) as Invoice;
  // Telegram's sheet, not ours. A payment screen drawn by this app would
  // be a payment screen that could be edited.
  app.openInvoiceLink(invoice.url);
  return true;
}

/**
 * The project this was opened to show, if it was.
 *
 * `/start project_<id>` from the bot lands here. The value came out of a
 * Telegram message and this page is open to anyone with a link, so the
 * server checks whether that project is actually theirs before showing it -
 * this only says what was asked for.
 */
export function startProject(): string | null {
  const app = telegramWebApp();
  const param = app?.initDataUnsafe?.start_param;
  if (!param) return null;
  const prefix = "project_";
  if (!param.startsWith(prefix)) return null;
  const runId = param.slice(prefix.length);
  // Telegram allows 64 characters; anything longer was not put there by us.
  return runId.length > 0 && runId.length <= 40 ? runId : null;
}