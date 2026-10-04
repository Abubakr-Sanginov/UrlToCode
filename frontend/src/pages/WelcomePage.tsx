import { useEffect, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  LuArrowRight,
  LuCode,
  LuGlobe,
  LuColumns,
  LuPlay,
  LuTerminal,
} from "react-icons/lu";
import { SignInDialog } from "../components/SignInDialog";
import { useAccount } from "../hooks/useAccount";
import { usePageTheme } from "../hooks/usePageTheme";
import { useAccountUi } from "../store/account-ui-store";

const DEMO_SRC = "/demo/urltocode-demo.mp4";
const DEMO_POSTER = "/demo/urltocode-demo-poster.jpg";

/**
 * Where each step starts in the demo video, in seconds, so the chapter
 * buttons can jump straight to the part they describe.
 */
const CHAPTERS = [
  { at: 4, icon: LuGlobe, title: "Paste a URL", text: "Drop in any public site and hit Clone." },
  { at: 10, icon: LuTerminal, title: "It crawls the real site", text: "A real browser opens every page, embed and legal link." },
  { at: 40, icon: LuCode, title: "Real code, file by file", text: "The clone builds up section by section, ready to run." },
  { at: 67.5, icon: LuColumns, title: "Compare with the original", text: "Clone on the left, the original on the right." },
] as const;

/**
 * The public front door at /welcome.
 *
 * It is the one page a signed-out visitor can see. It explains the product
 * with the demo video and offers the two ways in: sign in, or register. A
 * visitor who is already signed in is offered the app instead of the forms.
 */
export default function WelcomePage() {
  usePageTheme();
  const navigate = useNavigate();
  const { signedIn, account } = useAccount();
  const isSignInOpen = useAccountUi((state) => state.isSignInOpen);
  const openSignIn = useAccountUi((state) => state.openSignIn);
  const openRegister = useAccountUi((state) => state.openRegister);
  
  const videoRef = useRef<HTMLVideoElement>(null);

  // Sent here by a failed GitHub or Google sign-in: the dialog is what shows
  // the message the provider's round trip left on the address.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("signInError")) {
      openSignIn();
    }
  }, [openSignIn]);

  function jumpTo(seconds: number) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seconds;
    void video.play().catch(() => {
      // Autoplay can be refused; the controls are right there to press.
    });
    video.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div className="relative min-h-dvh overflow-x-hidden bg-canvas text-foreground">
      {/* One soft green glow behind the hero; the only decoration. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[36rem] bg-[radial-gradient(ellipse_at_top,hsl(var(--brand)/0.18),transparent_65%)]"
      />

      <header className="relative mx-auto flex w-full max-w-6xl items-center justify-between px-5 py-5">
        <Link to="/welcome" className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-brand font-mono text-sm font-bold text-brand-foreground">
            &gt;_
          </span>
          <span className="text-base font-semibold tracking-tight">UrltoCode</span>
        </Link>

        <nav className="flex items-center gap-2">
          {signedIn ? (
            <>
              <Link
                to="/profile"
                className="hidden rounded-lg px-3 py-2 text-sm text-foreground-muted transition-colors hover:text-foreground sm:block"
              >
                {account?.email}
              </Link>
              <Link
                to="/"
                className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
              >
                Open the app
              </Link>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={openSignIn}
                className="rounded-lg px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
              >
                Sign in
              </button>
              <button
                type="button"
                onClick={openRegister}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
              >
                Get started
              </button>
            </>
          )}
        </nav>
      </header>

      <main className="relative mx-auto w-full max-w-6xl px-5 pb-24">
        <section className="mx-auto max-w-3xl pt-12 text-center sm:pt-20">
          <p className="font-mono text-sm text-brand">$ url --to-code</p>
          <h1 className="mt-5 text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
            Any website.
            <br />
            <span className="text-brand">Working code.</span>
          </h1>
          <p className="mx-auto mt-6 max-w-xl text-base text-foreground-muted sm:text-lg">
            Paste a link. An AI crawler opens the real site, walks every page
            and rebuilds it as clean code you can run, edit and download.
          </p>

          <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
            {signedIn ? (
              <button
                type="button"
                onClick={() => navigate("/")}
                className="inline-flex items-center gap-2 rounded-lg bg-brand px-6 py-3 text-sm font-medium text-brand-foreground"
              >
                Open the app
                <LuArrowRight className="h-4 w-4" aria-hidden="true" />
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={openRegister}
                  className="inline-flex items-center gap-2 rounded-lg bg-brand px-6 py-3 text-sm font-medium text-brand-foreground"
                >
                  Create a free account
                  <LuArrowRight className="h-4 w-4" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={openSignIn}
                  className="rounded-lg border border-border px-6 py-3 text-sm font-medium text-foreground transition-colors hover:bg-accent"
                >
                  Sign in
                </button>
              </>
            )}
          </div>
          {!signedIn && (
            <p className="mt-4 text-xs text-muted-foreground">
              Free: one project and one clone a day. No card.
            </p>
          )}
        </section>

        <section className="mx-auto mt-14 max-w-5xl sm:mt-20" aria-label="Demo video">
          <div className="overflow-hidden rounded-xl border border-border bg-card shadow-raised">
            <video
              ref={videoRef}
              className="block aspect-video w-full bg-black"
              src={DEMO_SRC}
              poster={DEMO_POSTER}
              controls
              playsInline
              preload="metadata"
            >
              Your browser cannot play this video.
            </video>
          </div>
          <p className="mt-3 flex items-center justify-center gap-2 text-xs text-muted-foreground">
            <LuPlay className="h-3 w-3" aria-hidden="true" />
            90 seconds: a real clone, from URL to finished code
          </p>
        </section>

        <section className="mx-auto mt-16 max-w-5xl" aria-label="How it works">
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {CHAPTERS.map(({ at, icon: Icon, title, text }, index) => (
              <li key={title}>
                <button
                  type="button"
                  onClick={() => jumpTo(at)}
                  className="h-full w-full rounded-xl border border-border bg-card p-5 text-left transition-colors hover:border-brand-border hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="flex items-center justify-between">
                    <Icon className="h-5 w-5 text-brand" aria-hidden="true" />
                    <span className="font-mono text-xs text-muted-foreground">
                      0{index + 1}
                    </span>
                  </span>
                  <span className="mt-4 block text-sm font-semibold">{title}</span>
                  <span className="mt-1.5 block text-sm leading-6 text-foreground-muted">
                    {text}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {!signedIn && (
          <section className="mx-auto mt-20 max-w-3xl rounded-2xl border border-border bg-card px-6 py-12 text-center">
            <h2 className="text-2xl font-semibold tracking-tight">
              paste a URL. get the code.
            </h2>
            <p className="mt-3 text-sm text-foreground-muted">
              Sign in to start your first clone.
            </p>
            <button
              type="button"
              onClick={openRegister}
              className="mt-6 inline-flex items-center gap-2 rounded-lg bg-brand px-6 py-3 text-sm font-medium text-brand-foreground"
            >
              Get started
              <LuArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </section>
        )}
      </main>

      {isSignInOpen && <SignInDialog />}
    </div>
  );
}
