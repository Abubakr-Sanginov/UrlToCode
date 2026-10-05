# Load environment variables first
from dotenv import load_dotenv

load_dotenv()


import sys
from typing import Any, Optional

from contextlib import asynccontextmanager

# A Windows console defaults to cp1251, which cannot represent the box
# drawing a prompt preview draws - or the Chinese in a crawled page title.
# Printing any of it raises UnicodeEncodeError, and because a print sits
# in the middle of a generation, that error used to abort the run before
# the model was ever called. Errors are replaced rather than raised so a
# log line that cannot be encoded is skipped instead of stopping work.
for _stream in (sys.stdout, sys.stderr):
    try:
        # reconfigure() is on TextIOWrapper and TextIOWrapper subclasses;
        # a bare TextIO does not have it, and a test that captured stdout
        # in binary does not support it.
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        # A stream that cannot be reconfigured keeps whatever encoding it
        # was given. Errors are replaced where it can, so a log line that
        # will not encode is skipped instead of stopping work.
        pass


from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ALLOWED_ORIGINS, IS_DEBUG_ENABLED
from routes.accounts import SESSION_COOKIE
from routes import (
    admin,
    capabilities,
    oauth,
    screenshot,
    shares,
    telegram,
    generate_code,
    home,
    evals,
    export,
    design_systems,
    prompt_reports,
    agent_runs,
    eval_sets,
    url_to_code,
    local_project,
    accounts as accounts_route,
)
from uploaded_assets import configure_uploaded_asset_routes
import dev_server
import accounts as accounts_store
import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    debug_status = "ENABLED" if IS_DEBUG_ENABLED else "DISABLED"
    # Which database, said at startup. Projects and quotas live here, and a
    # server quietly running on the local file while the deployment believes
    # it is on Postgres loses everything written by the first of them.
    print(f"Storage: {db.describe()}")
    # Made here rather than on the first request: a wrong connection string
    # should stop the server saying so, not surface later as a failed signup.
    accounts_store._ready()
    # Loud, because the failure this guards against is invisible: a server
    # charging one Star for Studio credits the tier perfectly and hands out a
    # $45 plan for nothing. It has to be noticed in the log, on the day it is
    # switched on, rather than inferred from revenue later.
    if accounts_store.test_stars() is not None:
        print(
            "!! TEST PRICING IS ON: every plan costs "
            f"{accounts_store.test_stars()} Stars. Anyone can buy a paid "
            "tier for almost nothing. Unset TELEGRAM_TEST_STARS to stop."
        )
    print(f"Backend startup complete. Debug mode is {debug_status}.")
    yield
    # A dev server left running would hold a port and a file watcher on a
    # machine the user believes they have closed.
    dev_server.stop_all()


app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)
configure_uploaded_asset_routes(app)


class SessionHeader:
    """Let a session arrive in a header as well as a cookie.

    A cookie set while the browser is on the backend's address has to be sent
    again by a page on the site's address, which makes it a third-party
    cookie - and browsers increasingly refuse to send those no matter what
    SameSite says. Nothing else about the flow is broken: the account is
    created, the cookie is set correctly, and the person lands back not
    signed in.

    The token is folded into the request's cookies before routing, so every
    route keeps reading exactly one cookie and none of them change. A real
    cookie still wins: a browser that does send one has nothing to gain from
    this.

    Plain ASGI on purpose. Behind @app.middleware the downstream call is made
    with the scope the middleware was handed, so a rebuilt request is quietly
    ignored - which is a bug that shows up as a passing test and a broken
    sign-in.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        token: Optional[bytes] = None
        has_cookie = False
        has_cookie_header = False
        name = f"{SESSION_COOKIE}=".encode("latin-1")
        for key, value in scope.get("headers", []):
            lowered = key.lower()
            if lowered == b"x-session-token" and token is None:
                token = value
            elif lowered == b"cookie":
                has_cookie_header = True
                if name in value:
                    has_cookie = True

        if token is None or has_cookie:
            return await self.app(scope, receive, send)

        pair = name + token
        rebuilt = []
        for key, value in scope.get("headers", []):
            if key.lower() == b"cookie":
                value = value + b"; " + pair
            rebuilt.append((key, value))
        if not has_cookie_header:
            rebuilt.append((b"cookie", pair))

        return await self.app({**scope, "headers": rebuilt}, receive, send)

# Configure CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add routes
app.include_router(generate_code.router)
app.include_router(screenshot.router)
app.include_router(home.router)
app.include_router(capabilities.router)
app.include_router(evals.router)
app.include_router(export.router)
app.include_router(design_systems.router)
app.include_router(prompt_reports.router)
app.include_router(agent_runs.router)
app.include_router(eval_sets.router)
app.include_router(url_to_code.router)
app.include_router(local_project.router)
app.include_router(accounts_route.router)
app.include_router(oauth.router)
app.include_router(shares.router)
app.include_router(telegram.router)
app.include_router(admin.router)

# Outermost of the three, so it sees the headers as they arrived. CORS runs
# inside it, which is why the preflight for X-Session-Token has to be allowed
# explicitly: the header list below is what the browser is told it may send.
app.add_middleware(SessionHeader)
