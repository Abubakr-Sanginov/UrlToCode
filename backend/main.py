# Load environment variables first
from dotenv import load_dotenv

load_dotenv()


import sys

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


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ALLOWED_ORIGINS, IS_DEBUG_ENABLED
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
    print(f"Backend startup complete. Debug mode is {debug_status}.")
    yield
    # A dev server left running would hold a port and a file watcher on a
    # machine the user believes they have closed.
    dev_server.stop_all()


app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)
configure_uploaded_asset_routes(app)

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
