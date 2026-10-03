# The Telegram bot and Mini App

What this is for: people who would rather not make an account. They press
the bot's button, Telegram opens this app, and they are signed in. No email,
no password, nothing to remember.

There is no bot process in this repository. The bot has nothing to do except
reply to `/start` and, once someone asks it to, say "your clone is ready" —
both of which are single HTTP calls to Telegram. So the bot lives in the
backend as two routes, and there is nothing to keep running.

---

## One-time setup

### 1. Create the bot

Talk to [@BotFather](https://t.me/BotFather):

```
/newbot
```

Pick a name (`UrlToCode` is free) and a username ending in `bot`
(`UrlToCodeBot` is taken; append something). BotFather replies with a **bot
token**. Put it in `backend/.env` and nowhere else:

```
TELEGRAM_BOT_TOKEN=123456789:AAF...
```

Never put the token in a file that is committed, and never paste it into a
chat. `GET /api/telegram/status` reports whether a token is set and whether
it is the right shape, and deliberately never prints it.

### 2. Set the Mini App's address

The bot's button can only open a page on **HTTPS**. `http://localhost` will
not work, not even on your own machine.

```
TELEGRAM_MINI_APP_URL=https://your-domain.example
```

Where the domain points has to serve this app. If the address is wrong the
button opens nothing at all, with no error anyone can see.

### 3. Choose a secret for the webhook

Any long random string. Telegram sends it back on every call, and this
backend refuses anything without it:

```
TELEGRAM_WEBHOOK_SECRET=<something long and random>
```

### 4. Point Telegram at this server

From a machine that can reach the backend:

```
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-domain.example/api/telegram/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

The answer must contain `"ok": true`. To check it is still connected:

```
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

Restart the backend after editing `.env` — these are read at startup, and a
reload is not enough.

---

## How signing in works, and why it is safe

When a Mini App opens, Telegram puts a signed blob of strings into the page
as `Telegram.WebApp.initData`. That blob says who opened it. This backend
checks it before believing any of it:

* the `hash` is removed and the remaining fields are sorted and joined into
  the exact string Telegram signed;
* the key to check it with is itself a signature — HMAC of the literal
  `WebAppData` under the bot token;
* the two are compared with `compare_digest`, not `==`, so a wrong guess
  cannot be narrowed down one character at a time;
* the blob is refused after 24 hours, because Telegram never expires one. A
  blob left in a log or a screenshot would otherwise work forever.

The whole thing lives in [`telegram_auth.py`](telegram_auth.py). Its tests
are mostly forgeries: a changed user id, a changed name, a field added after
signing, and a blob signed with a different bot's token.

**A Mini App is an ordinary web page.** It could send anything. Nothing is
trusted because the page said so — only because Telegram signed it.

### Who gets which account

The account is matched on **Telegram's own user id**, never on a name or an
address typed into the app. Someone already signed in here when the Mini App
opens keeps their account rather than being given a second one with none of
their projects in it.

These accounts have no password. There is nothing to guess and nothing to
send a reset to; the only way in is Telegram.

---

## "Your clone is ready"

Once someone has pressed `/start`, their chat id is recorded, and a finished
clone sends them a message with a button back into the app.

Two things are deliberately not done here:

* **A notification is never sent to somebody who did not ask.** An account
  that has never spoken to the bot gets no messages. A wrong "your site is
  ready" addressed to a stranger is worse than silence.
* **A failed notification never fails the clone.** The person is waiting on
  a site, not on a message about a site, so `announce_ready` swallows every
  error and logs it.

Chat ids are only recorded for a link that already exists, so a `/start` from
a stranger cannot attach a stranger's conversation to somebody's account.

---

## Where things are

| | |
|---|---|
| [`telegram_auth.py`](telegram_auth.py) | checking what Telegram signed |
| [`routes/telegram.py`](routes/telegram.py) | the webhook, and sending |
| [`routes/accounts.py`](routes/accounts.py) | `POST /api/auth/telegram` |
| `frontend/src/lib/telegram.ts` | finding out we are inside Telegram |

## Checks

| | |
|---|---|
| `GET /api/telegram/status` | is a token set, is it the right shape |
| `GET /api/telegram/webhook` (wrong method) | 405 |
| `POST /api/telegram/webhook` without the secret | 403 |

---

## What is not built yet

* **Stars payments.** `STAR_PRICES` in `accounts.py` holds the figures and
  `announce_ready` is the notification shape a paid run will use, but
  nothing calls `createInvoiceLink`. The rate and the 30% commission are
  still unconfirmed — see the note at the top of that file.
* **Deep links into a particular project.** The button currently opens the
  app at the root. `/start project_<run-id>` would open that project, and
  `start_param` is already arriving in `initDataUnsafe`.
* **A reply keyboard.** Someone can only reach the app by tapping the
  button on the `/start` reply. That is fine for now and is one line of
  `reply_markup` to change.