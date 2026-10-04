# Signing in with GitHub and Google

Both are the standard authorization-code flow: the person is sent to the
provider, comes back with a code, and the code is exchanged for a verified
identity. What that identity is worth is decided in one function,
`accounts.account_for_provider`.

Nothing is drawn for a provider that is not configured. If you never set the
keys for Google, there is no Google button — a dead button on the sign-in
screen is worse than none.

## The keys

Put these in `backend/.env`:

```
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

A button appears for each pair that is filled in. One of the two alone does
nothing — a client id without its secret cannot exchange a code.

**Restart the backend after editing `.env`.** These are read once at import,
like `DATABASE_URL`; a reload will not pick them up.

## The callback address

Each provider matches the callback address exactly, character for character,
and refuses anything else. This is the one thing that usually goes wrong.

**It is the backend's address, not the site's.** The callback route is what
sets the session cookie, and a cookie belongs to the address that set it. If
the callback ran on the site, the cookie would be set on the site, and every
API call goes to the backend — so the browser would be asked for a cookie it
does not have, and nobody would be signed in. That is the whole reason the
cookie is `SameSite=None; Secure`: it is what lets a cookie set on the
backend travel to API calls the page makes on the site's origin.

With the site on Vercel and the backend on Render:

```
https://urltocode.onrender.com/api/auth/github/callback
https://urltocode.onrender.com/api/auth/google/callback
```

which is `OAUTH_REDIRECT_BASE` plus the path. Set it, because the address the
browser was sent to is the backend's own, and the two are the same thing
only while there is no proxy in between:

```
OAUTH_REDIRECT_BASE=https://urltocode.onrender.com
APP_URL=https://url-to-code-gjbe.vercel.app
```

`APP_URL` is where the person lands afterwards. Register the callback under
`OAUTH_REDIRECT_BASE`, never under `APP_URL`.

Locally it is the same idea on one address, through the Vite proxy:

```
http://localhost:5173/api/auth/github/callback
http://localhost:5173/api/auth/google/callback
```

If the app is reached by an address other than the one registered — a proxy
in front, say — `OAUTH_REDIRECT_BASE` overrides it.

### GitHub

**Settings → Developer settings → OAuth Apps → New OAuth App**

| Field | Value |
| --- | --- |
| Application name | UrlToCode |
| Homepage URL | `https://url-to-code-gjbe.vercel.app` |
| Authorization callback URL | `https://urltocode.onrender.com/api/auth/github/callback` |

GitHub also accepts `http://localhost:<port>/...` for local work, so no
tunnel is needed to try it.

### Google

**Google Cloud Console → APIs & Services → Credentials → Create credentials →
OAuth client ID**, type **Web application**.

| Field | Value |
| --- | --- |
| Name | UrlToCode |
| Authorised redirect URIs | `https://urltocode.onrender.com/api/auth/google/callback` |

Then enable the API: **APIs & Services → Library → People API**. The flow
reads who the person is from Google's userinfo endpoint, which the People
API backs.

Google asks which account to use every time (`prompt=select_account`). On a
shared machine, picking the account by first letter is how the wrong person
gets signed in.

## Where the person ends up

Back at the app, signed in, with the cookie set. If anything went wrong they
come back to the sign-in dialog with a plain sentence on it — the round trip
leaves the page, so the message travels on the address as `?signInError=`.

## What an account gets from a provider sign-in

| Question | Answer |
| --- | --- |
| Which identity is matched? | The provider's own id for the person, never the email |
| What is the email for? | Recognising an account that already exists — and only if the provider verified it |
| What if the provider did not verify it? | A separate, empty account. The existing one is untouched |
| Does the account get a password? | No. There is nothing to guess and nothing to reset |
| Changed their email at GitHub? | Same account. The provider's id has not changed |
| Both providers, same verified address? | One account — it is the same person |

The unverified case is the one worth being explicit about: anyone can put an
unverified address on a GitHub account, including somebody else's. If that
were enough to reach an account here, their projects would be one click away,
so it is not — an unverified address gets an empty account of its own and
leaves the real one alone.

## After signing in with a provider

A provider account has no password, and there is no password form any more:
Google and GitHub are the only way in. That is the reason the cookie is
`SameSite=None; Secure` and the callback sits on the backend — both exist so
that a session set during a redirect between two addresses is still usable.

The `/api/auth/register` and `/api/auth/login` routes remain on the server.
Nothing in the app calls them, and a provider account has no password to log
in with, so they cannot be used to reach one. They are left in place because
removing them would change the API for no gain, not because anything depends
on them.

## Checking it worked

```
GET /api/auth/providers
```

answers with the providers this server can actually use. If a provider you
configured is missing from that list, the keys were not picked up — usually
the server was not restarted.