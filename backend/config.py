import os


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    `bool(os.environ.get(...))` is wrong for falsy-looking strings: both
    "false" and "0" are truthy strings. Accept the common spellings instead.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


NUM_VARIANTS = 4
NUM_VARIANTS_VIDEO = 2

# LLM-related
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", None)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", None)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", None)
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", None)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", None)
# Models used by the direct-provider paths (url-to-code), where the UI sends
# no model id. Override via env when an endpoint serves a different catalog.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or "gpt-5.4-mini"
# Read for the same reason as the three above: a gateway that serves a
# different catalog, or a different model than the built-in default.
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL") or ""
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3-flash-preview"

# Image generation (optional)
REPLICATE_API_KEY = os.environ.get("REPLICATE_API_KEY", None)

# Browsers may only call this backend from these origins. A wildcard is not
# usable here: it cannot be combined with credentials, and this API takes API
# keys from the page.
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in (
        os.environ.get("CORS_ALLOWED_ORIGINS")
        or "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174"
    ).split(",")
    if origin.strip()
]

# Where a user is told to write when something breaks. Set SUPPORT_EMAIL
# before launch; the default names nobody, which is honest about a project
# that has not chosen an address yet.
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "").strip()

# The crawler drives a visible browser by default: Cloudflare-style checks
# flag headless Chromium and serve "Just a moment..." instead of the site.
# Set CRAWLER_HEADLESS=1 where no display exists (Docker, CI, a server).
CRAWLER_HEADLESS = _env_flag("CRAWLER_HEADLESS")

# For hosts with 512 MB of RAM, where Chromium plus the API is enough to get
# the instance killed. Trades completeness for staying alive: no videos, no
# live view, a leaner browser.
CRAWLER_LOW_MEMORY = _env_flag("CRAWLER_LOW_MEMORY")

# Debugging-related
IS_DEBUG_ENABLED = _env_flag("IS_DEBUG_ENABLED")
DEBUG_DIR = os.environ.get("DEBUG_DIR", "")

# When enabled, every LLM request is written to run_logs/prompt_reports as a
# JSON report viewable at /evals/prompt-reports.
# Hard per-generation spend ceiling; a run that would continue past this is
# aborted. Applies per variant/eval run. Unpriced models are not bounded.
GENERATION_MAX_COST_USD = 3.0

PROMPT_REPORTS_ENABLED = os.environ.get(
    "PROMPT_REPORTS_ENABLED", ""
).strip().lower() in {"1", "true", "yes", "on"}
LOCAL_ASSET_DIR = os.environ.get(
    "LOCAL_ASSET_DIR", os.path.join(os.path.dirname(__file__), "local_assets")
)
# Base URL the backend serves /local-assets from. The live (websocket) path
# infers this per-request; the evals path has no request, so it uses this.
LOCAL_ASSET_BASE_URL = os.environ.get("LOCAL_ASSET_BASE_URL", "http://127.0.0.1:7001")

# Set to True when running in production (on the hosted version)
# Used as a feature flag to enable or disable certain features
IS_PROD = _env_flag("IS_PROD")
