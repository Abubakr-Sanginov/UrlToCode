# Running it 24/7

There is no bot process to run. That is the first thing to understand.

The bot has exactly two jobs: answer `/start` with a button, and say "your
clone is ready". Both are HTTP calls. So both live in the backend as routes:

| | |
|---|---|
| `POST /api/telegram/webhook` | Telegram tells us what someone typed |
| `GET /s/{token}` | the shared site |
| the Mini App | this same app, served over HTTPS |

A separate long-polling bot script would be a second thing to install, keep
alive and forget to restart. A webhook is the HTTP address of the app you are
already running.

---

## The one thing that cannot be worked around

**Telegram cannot reach `localhost`.** Neither `setWebhook` nor a Mini App
button will open an address without a public HTTPS one, with a certificate a
browser trusts.

So something on the internet has to be listening. That is the whole question,
and it comes down to where the app runs.

---

## Where to run it

### Recommended: a VPS, Caddy, one domain

Roughly 300–600 ₽/month. Paid with a Russian card, which matters given no
passport.

reg.ru, Timeweb or Selectel. Pick any with **Ubuntu 22.04 or 24.04**. Then:

```bash
# 1. A domain pointed at the server's IP. Any registrar; reg.ru is easiest.
#    A record: app.example.ru -> the server's IPv4

# 2. On the server
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git
git clone <your repo> /opt/urltocode
cd /opt/urltocode
```

**Caddy** rather than nginx, because it gets and renews the certificate on
its own. Three lines where nginx plus certbot is thirty:

```caddyfile
# /etc/caddy/Caddyfile
app.example.ru {
    handle /api/* {
        reverse_proxy 127.0.0.1:7001
    }
    handle /s/* {
        reverse_proxy 127.0.0.1:7001
    }
    handle {
        reverse_proxy 127.0.0.1:5173
    }
}
```

```bash
sudo systemctl enable --now caddy
```

Caddy asks Let's Encrypt for a certificate on first start. It works if DNS
already points at the server — check that first, because the failure is a
timeout and gives no useful reason.

**Run it under systemd** so it comes back after a reboot, which is the whole
difference between a server and a machine you remember to switch on:

```ini
# /etc/systemd/system/urltocode.service
[Unit]
Description=UrlToCode
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/urltocode
ExecStart=docker compose up -d
ExecStop=docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now urltocode
```

### Without a server: Cloudflare Tunnel

Free, no public IP, no certificate to manage — Caddy's job done by someone
else. A `cloudflared` process holds the tunnel open from **any** machine that
is on 24/7.

The catch, and it is a real one: something still has to stay awake. On a
laptop that means the tunnel dies every time the lid closes. It trades money
for uptime, and for a bot that is usually the wrong way round.

---

## Before the bot will do anything

Four things, all in `backend/.env`:

```
TELEGRAM_BOT_TOKEN=123456789:AAF...     from @BotFather
TELEGRAM_WEBHOOK_SECRET=<long random>   any string you invent
TELEGRAM_MINI_APP_URL=https://app.example.ru
APP_URL=https://app.example.ru
```

**Restart the backend after editing `.env`.** These are read once at startup;
a reload does not pick them up.

Then point Telegram at it, once:

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://app.example.ru/api/telegram/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

The answer must contain `"ok": true`. Check it is still connected:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

`pending_update_count` should settle back to zero.

### Check it without a phone

```bash
# Is a token set, is it the right shape, where would the button open
curl https://app.example.ru/api/telegram/status

# Should be 403: a webhook address is public, and this proves the secret works
curl -X POST https://app.example.ru/api/telegram/webhook -d '{}'
```

`configured: false` means the token is missing or half-copied. That endpoint
never prints the token.

---

## Two things to set before a launch

### `SESSION_SECRET`

Unset, this falls back to a hardcoded constant that is in the repository.
Anyone who has read the repository can then forge a session cookie for any
account, including a paid one.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### The database password

The Neon connection string was pasted into a chat. Rotate it in the Neon
console and put the new one in `.env`. Until then, treat that database as
public.

---

## What is stored where

| | |
|---|---|
| accounts, projects, share links | Neon, via `DATABASE_URL` |
| clone runs and project files | **disk**, `backend/clone_runs/` |

The disk part matters: it is inside the container, so
`docker compose down && docker compose up -d` with a rebuild can leave you
with account rows pointing at files that are not there. A shared link then
answers "this site is no longer stored" rather than opening.

Two ways out, cheapest first:

* **Mount a volume** at `backend/clone_runs` so the files outlive the
  container. One line in `docker-compose.yml`, and it also survives upgrades.
* **Object storage** (S3, or a Neon-adjacent bucket) and stop trusting the
  local disk at all.

Not urgent for a launch on a VPS with a persistent disk. Urgent the first
time the disk fills, because every clone fails at once and the failure will
look like an LLM problem.

---

## What the bot does not do yet

* **Payments.** `STAR_PRICES` and the tariff endpoint are in place;
  `createInvoiceLink` is not called. Nobody can pay yet.
* **Deep links.** The button opens the app at the root rather than at a
  particular project. `/start project_<run-id>` would fix that, and
  `start_param` is already arriving.