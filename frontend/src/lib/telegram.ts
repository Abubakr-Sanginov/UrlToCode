/**
 * Being inside Telegram, and telling this app that it is.
 *
 * Telegram puts a script on the page that exposes what it knows about the
 * person looking at it. None of that is worth trusting on its own - a Mini
 * App is an ordinary web page and would be free to claim it was opened by
 * somebody else. So what this file does is deliberately small: it hands the
 * signed blob to the server and lets the server decide.
 */

import { HTTP_BACKEND_URL } from "../config";

export interface TelegramWebApp {
  initData: string;
  initDataUnsafe?: {
    user?: { id: number; first_name?: string; username?: string };
    start_param?: string;
  };
  colorScheme?: "light" | "dark";
  ready(): void;
  expand(): void;
  close(): void;
  openLink(url: string): void;
  BackButton?: { show(): void; hide(): void; onClick(cb: () => void): void };
  MainButton?: {
    setText(text: string): void;
    onClick(cb: () => void): void;
    hide(): void;
  };
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

let cached: TelegramWebApp | null | undefined;

/** The Mini App handle, or null when this page is not inside Telegram. */
export function telegramWebApp(): TelegramWebApp | null {
  if (cached !== undefined) return cached;
  // `window` is absent when this runs outside a browser at all - a test,
  // or a server rendering the page. Neither is inside Telegram, and asking
  // the question has to be safe to ask in both.
  if (typeof window === "undefined") return null;
  cached = window.Telegram?.WebApp ?? null;
  return cached;
}

export function isInsideTelegram(): boolean {
  const app = telegramWebApp();
  // initData is the part Telegram signed. An empty string is what the page
  // gets outside Telegram, and is also what a page serving its own script
  // could post - so it counts as "not inside" here and is verified on the
  // server regardless.
  return Boolean(app && app.initData);
}

/**
 * Ask the server who this is.
 *
 * The blob is sent once and the answer is the session cookie the server
 * sets, so everything after this works like any other page load. Returning
 * null rather than throwing matters: a person who opens the app in a normal
 * browser should get the ordinary sign-in, not an error about Telegram.
 */
export async function signInThroughTelegram(): Promise<boolean> {
  const app = telegramWebApp();
  if (!app || !app.initData) return false;

  try {
    const response = await fetch(`${HTTP_BACKEND_URL}/api/auth/telegram`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The session cookie rides along on its own. It is HttpOnly, so this
      // page cannot read it, and it does not need to: somebody already
      // signed in here is recognised by the cookie reaching the server.
      credentials: "include",
      body: JSON.stringify({
        initData: app.initData,
        chatId: app.initDataUnsafe?.user?.id ?? null,
      }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Ready the Mini App window.
 *
 * Called once at startup. Without `ready()` Telegram keeps showing its own
 * loading clock over an app that has actually finished, which reads as a
 * hang.
 */
export function prepareTelegram(): void {
  const app = telegramWebApp();
  if (!app) return;
  app.ready();
  app.expand();
}