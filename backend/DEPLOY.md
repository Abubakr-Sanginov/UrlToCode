# Two addresses: the site on Vercel, the bot on Railway

The site is a static bundle. The bot is a server with a database, a
browser, and a public address Telegram has to be able to reach. They are
split across two hosts, and that split has exactly one consequence worth
understanding before anything is deployed.

## The thing that breaks

The session is a cookie. A cookie marked `SameSite=lax` — which is what
this app used to send — is **withheld by the browser from every
cross-site request**. Put the site on `urltocode.vercel.app` and the
backend on `urltocode.up.railway.app` and the browser stops sending the
cookie to every `/api` call. Nobody is signed in, and nothing appears in
any log, because the request arrives perfectly well formed — anonymous.

The fix is `SameSite=None; Secure`, and it is applied
[here](../backend/routes/accounts.py) by looking at one thing: whether the
backend is configured to allow any `https://` origin.

```python
def _cross_site() -> bool:
    return any(origin.startswith("https://") for origin in config.CORS_ALLOWED_ORIGINS)
```

Local development allows only `http://localhost:5173`, so the cookie stays
`lax` and unsecured and login works on a laptop. A deployment whose allowed
origins include Vercel's `https://` address gets `None; Secure` and works
across the split. One setting decides both the CORS policy and the cookie,
because two settings for one fact is how a deployment ends up allowing a
frontend in and then withholding its cookie from it.

> `Secure` means the cookie will not be sent over plain http. That is not a
> caveat, it is the mechanism: both addresses must be https, and both are.

## Vercel — the site

Connect the repository, then set the **Root Directory** to `frontend`.
[vercel.json](../frontend/vercel.json) supplies the build command, the
output directory, and the SPA rewrite that `BrowserRouter` needs.

Set these as environment variables in Vercel:

```
VITE_HTTP_BACKEND_URL = https://<railway-domain>
VITE_WS_BACKEND_URL   = wss://<railway-domain>
```

They are read at build time by [config.ts](../frontend/src/config.ts), so
changing them means a **rebuild**, not just a restart. `http` and `ws` in
the old values will be mixed-content-blocked by the browser on an https
page — the request fails silently in the console.

## Railway — the bot and the API

One service, root directory `backend`. The Dockerfile is picked up as it
stands; it listens on `${PORT}` because Railway chooses a port per
deployment and health-checks that one.

Set as service variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | the Neon connection string |
| `SESSION_SECRET` | a long random string, generated **for this deployment** |
| `CORS_ALLOWED_ORIGINS` | `https://<your-vercel-domain>` — this one is not optional, it is what makes the cookie cross-site |
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | anything you invent; Telegram sends it back and it is checked |
| `TELEGRAM_MINI_APP_URL` | `https://<your-vercel-domain>` — the address the button opens |
| `OPENAI_API_KEY` or equivalents | one model provider is enough |

Then point the webhook at Railway, from a machine that can reach
Telegram:

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d url="https://<railway-domain>/api/telegram/webhook" \
  -d secret_token="$TELEGRAM_WEBHOOK_SECRET"
```

Check it answered:

```bash
curl "https://<railway-domain>/api/telegram/status"
```

### A volume, or the clones disappear

Cloning writes into `backend/clone_runs/`. On a PaaS host that directory is
part of the container, and the next deployment replaces it — everybody's
saved projects are gone, and the sharing links 410 with them. Attach a
volume mounted at `/app/clone_runs`.

## After the split, check these four things

In this order, because the first failure hides the rest:

1. **Open the site while signed in and watch the Network tab.** `/api/me`
   must be 200. If it is 200 with a null account, `CORS_ALLOWED_ORIGINS`
   is wrong — that is the exact signature of this bug.
2. **Start a clone.** It runs over a websocket; a wrong `VITE_WS_BACKEND_URL`
   shows as "Is the backend running?" while the backend is running.
3. **Open the bot and press the button.** If nothing opens, `setWebhook` did
   not take, or `TELEGRAM_MINI_APP_URL` is still the default.
4. **Buy a plan and watch the webhook log.** A payment that never arrives
   here is one Telegram could not deliver, not one the app rejected.

## Turning up

Railway charges for what it uses, and the backend is not small: Chromium
plus its libraries plus a virtual display is roughly a gigabyte of image,
and the crawler runs a browser per clone. Cheaper than a VPS, and easier —
but if the bill becomes the argument, a small VPS with the same Dockerfile
does the same work and costs a fixed few hundred roubles a month.

## The one command that would undo all of this

If both halves turn out to be more trouble than they are worth, serving the
built frontend from FastAPI removes the split, the cookie policy, the CORS
list, and the two places a URL can be wrong. It is a small change, and it
is deliberately not the first thing to reach for — you asked for the site
on Vercel, and that is where it should be.
