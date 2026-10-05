import { useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { LuArrowRight, LuCheck, LuMinus } from "react-icons/lu";
import { SignInDialog } from "../components/SignInDialog";
import { DemoPlayer, type DemoChapter } from "../components/welcome/DemoPlayer";
import { useAccount } from "../hooks/useAccount";
import { adoptSessionFromAddress } from "../lib/accounts";
import { usePageTheme } from "../hooks/usePageTheme";
import { useAccountUi } from "../store/account-ui-store";

/**
 * Each step starts where it starts in the demo video, so the chapter list
 * beside the player can jump straight to it and can follow along while it
 * plays.
 *
 * The text says what to look at in the frame. What the feature is called is
 * on the step list further down; repeating it here would say nothing the
 * frame does not show better.
 */
const CHAPTERS: readonly DemoChapter[] = [
  {
    at: 4,
    title: "Paste any address",
    text: "A URL bar at the top takes the address of a site you can open without logging in. Nothing to upload and no zip file — the page is read where it already lives.",
  },
  {
    at: 10,
    title: "A real browser reads the site",
    text: "Watch the queue fill as the crawler opens each page in Chromium, follows internal links, and saves the images and fonts it meets along the way.",
  },
  {
    at: 40,
    title: "The files arrive one by one",
    text: "Each section becomes a file you can open and read. The clone is written as it goes, not handed over at the end as a single download.",
  },
  {
    at: 67.5,
    title: "Original and clone, side by side",
    text: "Two live previews with the scores underneath. Anything that came out wrong is named, so you know what to fix rather than hunting for it.",
  },
] as const;

/** What actually happens, in the order it happens. It is a sequence, which
 *  is why these are numbered — the number is the step, not decoration. */
const STEPS = [
  {
    title: "The crawl",
    body: "Chromium opens the address and visits the pages it links to, up to a limit you set. It records the markup, the stylesheets, the images and the fonts as they are served, so the clone is built from what the site really returns rather than from a description of it.",
    note: "Up to 10 pages, or as many as you ask for within the plan's limit.",
  },
  {
    title: "The rebuild",
    body: "Each page becomes React and Tailwind, split into components you can actually edit. The output is a project with a package.json, not a screenshot or an HTML dump dressed up as code.",
    note: "Stack is yours to pick — React, Vue, plain HTML, or a custom setup.",
  },
  {
    title: "The comparison",
    body: "Every page is rendered again at desktop and mobile widths and measured against the original capture. You get a score per viewport and a list of the pages that drifted.",
    note: "Below the fidelity threshold, a page is flagged for repair rather than quietly kept.",
  },
  {
    title: "The repair",
    body: "Ask for a repair on a page that scored badly and it is rebuilt against the original pixels until it passes or runs out of attempts. A failed attempt does not use up your daily run.",
    note: "Refused work is refunded, so a mistake on our side costs you nothing.",
  },
] as const;

const INCLUDED = [
  "The full cloned project as files, not a link that expires",
  "Original and clone side by side, at two viewport widths",
  "Project history you can reopen and keep working on",
  "Telegram notifications when a long clone finishes",
];

const NOT_INCLUDED = [
  "Nothing behind a login, and nothing a crawler is not allowed to read",
  "No payment details at all on the free tier",
  "A daily limit, which is the honest reason the free tier is free",
];

const PLANS = [
  {
    tier: "Free",
    price: "0",
    unit: "",
    lines: ["1 project", "1 clone or repair a day", "No card, no trial that ends"],
  },
  {
    tier: "Starter",
    price: "360",
    unit: "Stars",
    lines: ["10 projects", "25 clones a day", "Priority queue"],
  },
  {
    tier: "Pro",
    price: "1080",
    unit: "Stars",
    lines: ["50 projects", "Unlimited daily runs", "Visual comparison included"],
  },
  {
    tier: "Studio",
    price: "3240",
    unit: "Stars",
    lines: ["Unlimited projects", "Team seats", "Shared history"],
  },
] as const;

const QUESTIONS = [
  {
    q: "Do I keep what the clone produces?",
    a: "Yes. The files are yours to read, edit and run on your own machine. Nothing is watermarked and nothing stops working if you stop paying.",
  },
  {
    q: "What happens to a site that is behind a login?",
    a: "It is not crawled, and it is not asked to be. The crawler reads what anyone could read by opening the address, which is also the only way to stay inside somebody else's site.",
  },
  {
    q: "Why is there a daily limit?",
    a: "Because a real browser opening a real site is the expensive part, and the free tier is not funded by ads. One run a day is enough to find out whether this is useful to you.",
  },
  {
    q: "How long does a clone take?",
    a: "A single page is usually under a minute. A ten-page crawl runs longer, and you can leave — you are told when it is done, in the app and in Telegram.",
  },
  {
    q: "Why Telegram Stars?",
    a: "Because there is no card to enter and no merchant account to lose. The payment happens inside Telegram, and the bot delivers the invoice.",
  },
] as const;

/**
 * The public front door at /welcome.
 *
 * The one page a signed-out visitor can see. The demo is the centre of it:
 * the chapters sit beside the video and follow it, so the explanation and
 * the footage cannot drift apart. Everything else on the page is either a
 * step in that process, or an honest statement of what the product does and
 * does not do.
 */
export default function WelcomePage() {
  usePageTheme();
  const navigate = useNavigate();
  const { signedIn, account } = useAccount();
  const isSignInOpen = useAccountUi((state) => state.isSignInOpen);
  const openSignIn = useAccountUi((state) => state.openSignIn);

  // Sent here by a failed GitHub or Google sign-in: the dialog is what shows
  // the message the provider's round trip left on the address.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("signInError")) {
      openSignIn();
    }
  }, [openSignIn]);

  // A successful sign-in comes back with the session in the address rather
  // than in a cookie, and this page asks who is signed in straight away —
  // so it is taken first.
  useEffect(() => {
    adoptSessionFromAddress();
  }, []);

  return (
    <div className="relative min-h-dvh overflow-x-hidden bg-canvas text-foreground">
      {/* One soft green glow behind the hero; the only decoration on the page. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[36rem] bg-[radial-gradient(ellipse_at_top,hsl(var(--brand)/0.18),transparent_65%)]"
      />

      <header className="relative mx-auto flex w-full max-w-6xl items-center justify-between px-5 py-5">
        <Link to="/welcome" className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-brand font-mono text-sm font-bold text-brand-foreground">
            &gt;_
          </span>
          <span className="text-base font-semibold tracking-tight">UrlToCode</span>
        </Link>

        <nav className="flex items-center gap-2">
          {signedIn ? (
            <button
              type="button"
              onClick={() => navigate("/")}
              className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
            >
              Open the app
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={openSignIn}
                className="rounded-lg px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
              >
                Sign in
              </button>
              <button
                type="button"
                onClick={openSignIn}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
              >
                Start a clone
              </button>
            </>
          )}
        </nav>
      </header>

      <main className="relative">
        {/* ---------------------------------------------------------------
            Hero. Left-aligned rather than centred: the demo below is a wide
            asymmetric thing, and a centred column above it makes the two look
            unrelated. The headline is the only serif on the page — it reads
            as a page of documentation rather than a pitch.
        ---------------------------------------------------------------- */}
        <section className="mx-auto grid w-full max-w-6xl gap-12 px-5 pb-20 pt-10 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:gap-16">
          <div className="max-w-xl">
            <h1 className="font-display text-5xl leading-[1.05] tracking-tight text-balance sm:text-6xl">
              Give us an address. Get back the files.
            </h1>
            <p className="mt-6 max-w-lg text-lg leading-8 text-foreground-muted">
              UrlToCode opens a real site in a real browser, reads every page
              it can reach, and writes the whole thing back out as a project
              you can run and edit. Not a screenshot, and not a paragraph
              describing what was there.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={openSignIn}
                className="inline-flex items-center gap-2 rounded-lg bg-brand px-5 py-3 text-sm font-medium text-brand-foreground transition-colors hover:bg-brand/90"
              >
                Clone a site
                <LuArrowRight className="h-4 w-4" aria-hidden="true" />
              </button>
              <a
                href="#demo"
                className="inline-flex items-center gap-2 rounded-lg border border-border px-5 py-3 text-sm font-medium text-foreground transition-colors hover:bg-accent"
              >
                Watch the 90 seconds
              </a>
            </div>

            <p className="mt-5 text-sm text-muted-foreground">
              One clone a day on the free tier. No card.
            </p>
          </div>

          {/* The transformation, drawn rather than promised. Not an input,
              because an input that pretends to clone something would be a
              lie — this is a picture of what comes out. */}
          <div className="self-end rounded-xl border border-border bg-card p-5 shadow-raised">
            <p className="font-mono text-xs text-muted-foreground">
              you paste
            </p>
            <p className="mt-2 truncate rounded-md bg-background px-3 py-2 font-mono text-sm text-foreground">
              https://any-site.example/pricing
            </p>
            <div className="my-3 flex items-center gap-3" aria-hidden="true">
              <span className="h-px flex-1 bg-border" />
              <LuArrowRight className="h-3.5 w-3.5 text-brand" />
              <span className="h-px flex-1 bg-border" />
            </div>
            <p className="font-mono text-xs text-muted-foreground">you get</p>
            <ul className="mt-2 space-y-1 font-mono text-sm text-foreground">
              <li>src/</li>
              <li className="pl-4">App.tsx</li>
              <li className="pl-4">components/Hero.tsx</li>
              <li className="pl-4">components/PricingTable.tsx</li>
              <li className="pl-4">index.css</li>
              <li>package.json</li>
            </ul>
          </div>
        </section>

        {/* ---------------------------------------------------------------
            The demo. The chapter list is a transcript that follows playback,
            not a set of cards explaining the video somewhere else.
        ---------------------------------------------------------------- */}
        <section id="demo" className="border-y border-border bg-background/60">
          <div className="mx-auto w-full max-w-6xl px-5 py-20">
            <h2 className="font-display text-3xl tracking-tight">
              Ninety seconds, start to finish
            </h2>
            <p className="mt-3 max-w-2xl text-foreground-muted">
              The whole path: address in, files out. Click a step to jump to
              it, or just let it play — the list keeps its place.
            </p>
            <div className="mt-10">
              <DemoPlayer chapters={CHAPTERS} />
            </div>
          </div>
        </section>

        {/* The process. A sequence, so it is numbered, and laid out as a rail
            rather than as four identical cards. */}
        <section className="mx-auto w-full max-w-6xl px-5 py-20">
          <h2 className="font-display text-3xl tracking-tight">
            What happens when you press Clone
          </h2>
          <p className="mt-3 max-w-2xl text-foreground-muted">
            Four steps, in this order. The demo above is the same four steps
            with the waiting removed.
          </p>

          <ol className="mt-12 border-t border-border">
            {STEPS.map((step, index) => (
              <li
                key={step.title}
                className="grid gap-4 border-b border-border py-8 lg:grid-cols-[3rem_minmax(0,16rem)_minmax(0,1fr)] lg:gap-10"
              >
                <span className="font-mono text-sm text-brand">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <h3 className="text-lg font-medium tracking-tight">
                  {step.title}
                </h3>
                <div>
                  <p className="max-w-2xl leading-7 text-foreground-muted">
                    {step.body}
                  </p>
                  <p className="mt-3 text-sm text-muted-foreground">
                    {step.note}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* What you get, and what you do not. The second list is the one
            people are actually owed. */}
        <section className="border-y border-border bg-background/60">
          <div className="mx-auto grid w-full max-w-6xl gap-12 px-5 py-20 lg:grid-cols-2 lg:gap-20">
            <div>
              <h2 className="font-display text-3xl tracking-tight">
                What you get
              </h2>
              <ul className="mt-8 space-y-4">
                {INCLUDED.map((line) => (
                  <li key={line} className="flex gap-3 leading-7">
                    <LuCheck
                      className="mt-1 h-4 w-4 shrink-0 text-brand"
                      aria-hidden="true"
                    />
                    <span className="text-foreground-muted">{line}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h2 className="font-display text-3xl tracking-tight">
                What it does not do
              </h2>
              <ul className="mt-8 space-y-4">
                {NOT_INCLUDED.map((line) => (
                  <li key={line} className="flex gap-3 leading-7">
                    <LuMinus
                      className="mt-1 h-4 w-4 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <span className="text-foreground-muted">{line}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* Plans. A plain table rather than four cards with different corner
            radii: these are numbers, and numbers read better in columns. */}
        <section className="mx-auto w-full max-w-6xl px-5 py-20">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 className="font-display text-3xl tracking-tight">Plans</h2>
              <p className="mt-3 max-w-xl text-foreground-muted">
                Paid with Telegram Stars, inside Telegram. There is no card
                anywhere in this.
              </p>
            </div>
          </div>

          <div className="mt-10 overflow-x-auto">
            <table className="w-full min-w-[42rem] border-collapse text-left">
              <thead>
                <tr className="border-b border-border">
                  <th className="py-3 pr-6 font-medium text-muted-foreground">
                    Plan
                  </th>
                  <th className="py-3 pr-6 font-medium text-muted-foreground">
                    Price
                  </th>
                  <th className="py-3 font-medium text-muted-foreground">
                    What it allows
                  </th>
                </tr>
              </thead>
              <tbody>
                {PLANS.map((plan) => (
                  <tr key={plan.tier} className="border-b border-border last:border-b-0">
                    <td className="py-5 pr-6 align-top font-medium">
                      {plan.tier}
                    </td>
                    <td className="py-5 pr-6 align-top">
                      <span className="font-display text-2xl tabular-nums">
                        {plan.price}
                      </span>
                      {plan.unit && (
                        <span className="ml-1.5 text-sm text-muted-foreground">
                          {plan.unit}
                        </span>
                      )}
                    </td>
                    <td className="py-5 align-top">
                      <ul className="space-y-1.5 text-sm text-foreground-muted">
                        {plan.lines.map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="border-t border-border bg-background/60">
          <div className="mx-auto w-full max-w-3xl px-5 py-20">
            <h2 className="font-display text-3xl tracking-tight">
              Questions people ask first
            </h2>
            <dl className="mt-10 space-y-0">
              {QUESTIONS.map((item) => (
                <div key={item.q} className="border-b border-border py-6 last:border-b-0">
                  <dt className="text-base font-medium">{item.q}</dt>
                  <dd className="mt-2 max-w-2xl leading-7 text-foreground-muted">
                    {item.a}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {!signedIn && (
          <section className="mx-auto w-full max-w-6xl px-5 py-24">
            <div className="flex flex-col items-start justify-between gap-8 border-t border-border pt-12 lg:flex-row lg:items-end">
              <h2 className="max-w-xl font-display text-4xl leading-tight tracking-tight">
                Paste an address and see what comes back.
              </h2>
              <button
                type="button"
                onClick={openSignIn}
                className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-brand px-6 py-3 text-sm font-medium text-brand-foreground transition-colors hover:bg-brand/90"
              >
                Start a clone
                <LuArrowRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </section>
        )}
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-2 px-5 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <span>UrlToCode</span>
          <span className="font-mono">
            {signedIn && account ? account.email : "Sign in with Google or GitHub"}
          </span>
        </div>
      </footer>

      {isSignInOpen && <SignInDialog />}
    </div>
  );
}