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

Take it from the startup of a local server — start sign-in, copy the address
the browser was sent to, and read the `redirect_uri` out of it. Locally it
is:

```
http://localhost:5173/api/auth/github/callback
http://localhost:5173/api/auth/google/callback
```

It goes through the app's own address rather than the backend's, because the
session cookie is set by the callback. A cookie set by one origin during a
redirect from another is at the mercy of the browser's third-party rules,
and browsers are increasingly unwilling.

When the app is at its public address, the address becomes
`https://your-domain/api/auth/<provider>/callback`. Register that one too.

If the app is reached by an address other than the one registered — a proxy
in front, say — set `OAUTH_REDIRECT_BASE` to the registered address:

```
OAUTH_REDIRECT_BASE=https://your-domain
```

### GitHub

**Settings → Developer settings → OAuth Apps → New OAuth App**

| Field | Value |
| --- | --- |
| Application name | UrlToCode |
| Homepage URL | your app's address |
| Authorization callback URL | `<address>/api/auth/github/callback` |

GitHub also accepts `http://localhost:<port>/...` for local work, so no
tunnel is needed to try it.

### Google

**Google Cloud Console → APIs & Services → Credentials → Create credentials →
OAuth client ID**, type **Web application**.

| Field | Value |
| --- | --- |
| Name | UrlToCode |
| Authorised redirect URIs | `<address>/api/auth/google/callback` |

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

A provider account has no password, so there is nothing to type on the
password form. If someone wants to add one — to keep signing in without the
provider — that is a separate piece of work and it is not here.

## Checking it worked

```
GET /api/auth/providers
```

answers with the providers this server can actually use. If a provider you
configured is missing from that list, the keys were not picked up — usually
the server was not restarted.