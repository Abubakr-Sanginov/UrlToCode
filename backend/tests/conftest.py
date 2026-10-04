"""Shared test setup.

The main thing here is that a developer's `.env` must not decide how the
tests behave.

`main.py` calls `load_dotenv()` before anything else, so importing the app
pulls in the real deployment values from the working copy. That is right in
production and wrong here: with a real `CORS_ALLOWED_ORIGINS` in `.env`,
`accounts._cross_site()` turns true, every session cookie becomes
`SameSite=None; Secure`, and a client speaking plain http stops returning it
— so every test that signs in and expects to still be signed in fails.
Worse, whether that happens depends on which module a test happens to import
first, which is how a suite that is green alone turns red together.

Neutralising the loader is the blunt fix and the right one: a test that wants
a deployment value sets it itself, and nothing else changes underneath it.
Tests that genuinely depend on the cross-site behaviour live in
`test_cross_site_session.py` and set the origin by hand.
"""

import os

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # type: ignore[assignment]

# In case anything read .env before this ran: a stray SESSION_SECRET would
# otherwise sign cookies with a developer's real secret rather than a test one.
for _name in (
    "CORS_ALLOWED_ORIGINS",
    "APP_URL",
    "OAUTH_REDIRECT_BASE",
    "TELEGRAM_MINI_APP_URL",
    "TELEGRAM_BOT_TOKEN",
    "ADMIN_TOKEN",
    "DATABASE_URL",
):
    os.environ.pop(_name, None)