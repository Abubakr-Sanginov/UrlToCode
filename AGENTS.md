# Project Agent Instructions

Python environment:

- Always use the backend Poetry virtualenv (`backend-py3.10`) for Python commands.
- Preferred invocation: `cd backend && poetry run <command>`.
- If you need to activate directly, use Poetry to discover it in the current environment:
  - `cd backend && poetry env activate` (then run the `source .../bin/activate` command it prints)

Testing policy:

- Always run backend tests after every code change: `cd backend && poetry run pytest`.
- Always run type checking after every code change: `cd backend && poetry run pyright`.
- Type checking policy: no new warnings in changed files (`pyright`).

## Frontend

- Frontend: `cd frontend && pnpm lint`

If changes touch both, run both sets.

## Prompt formatting

- Prefer triple-quoted strings (`"""..."""`) for multi-line prompt text.
- For interpolated multi-line prompts, prefer a single triple-quoted f-string over concatenated string fragments.

# Hosted

The hosted version is on the `hosted` branch. The `hosted` branch connects to a saas backend, which is a seperate codebase at ../screenshot-to-code-saas

## Cursor Cloud specific instructions

Dependencies are refreshed automatically on startup (`poetry install` in `backend/`, `pnpm install` in `frontend/`); no manual install is needed.
Cursor Cloud environment setup should run `bash /agent/repos/screenshot-to-code/scripts/cursor-cloud-install.sh`; the script changes to the repo root before installing so it works regardless of the startup working directory.

Services (see `README.md` for the canonical commands):
- Backend (FastAPI + WebSocket): from `backend/`, `poetry run uvicorn main:app --reload --port 7001`.
- Frontend (Vite/React): from `frontend/`, `pnpm dev` → open `http://localhost:5173`. The Vite dev server binds to `localhost` only, so use `http://localhost:5173`, not `http://127.0.0.1:5173` (the latter refuses the connection).
- Frontend talks to the backend over a WebSocket (`VITE_WS_BACKEND_URL`, default `ws://127.0.0.1:7001`); generation streams over that socket, other routes are plain HTTP.

Non-obvious caveats:
- `poetry` is installed under `~/.local/bin` and is on PATH for interactive shells (`.bashrc`) but not necessarily for non-interactive scripts; use the full path `~/.local/bin/poetry` if `poetry` is not found.
- The Poetry virtualenv resolves to Python 3.12 (named like `backend-...-py3.12`), not 3.10 — `pyproject.toml` pins `^3.10`, which 3.12 satisfies. Just use `poetry run`.
- Core feature (screenshot → code) requires at least one LLM key: set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` in `backend/.env` (restart backend after editing) or in the in-app Settings dialog. Without a key, generation fails fast with a "No OpenAI, Anthropic, or Gemini API key" message. `REPLICATE_API_KEY` (image gen/edit) only works via `backend/.env`, not the UI.
- Playwright Chromium is pre-installed for the optional "Screenshot preview" tool; Settings shows it as "Available".
- `pnpm install` prints an "Ignored build scripts (esbuild, puppeteer)" warning — this is harmless; Vite build/dev and tests work without approving builds.
- `cd frontend && pnpm lint` currently reports pre-existing errors (e.g. `@typescript-eslint/no-explicit-any` in `generateCode.ts`) because lint runs with `--max-warnings 0`; these are baseline issues, not environment problems.

# Project map (UrltoCode)

Product: paste a URL → a headless Chromium crawler walks the real site → the user picks pages → an LLM writes the clone page by page as real code (Next.js App Router or React/Vite + Tailwind). Forked from abi/screenshot-to-code; the original screenshot→code flow still exists in the code, but the product focus is URL→code (`/url-to-code` WebSocket).

## Layout

- `backend/` — FastAPI + WebSocket, Python 3.10+ (Poetry). Entry: `backend/main.py` (registers all routers, CORS, dev server).
- `frontend/` — React 18 + Vite + TypeScript, Zustand stores, react-router. Entry: `frontend/src/main.tsx`.
- `mobile/` — separate Expo/React Native app, not wired to the backend yet. Has its own `mobile/AGENTS.md` (read it before touching mobile; Expo APIs change per SDK).
- `README.md` (product + setup), `backend/DEPLOY.md`, `backend/TELEGRAM.md`, `backend/DATABASE.md`, `backend/OAUTH.md`, `Evaluation.md`, `TESTING.md`, `QA.md`, `Troubleshooting.md`.
- `.sharrowkin/` and `*.mp4` / `mobile/` in the working tree are untracked local artifacts, not project source.

## Backend

- `routes/` — one module per feature: `url_to_code.py` (main clone flow, big: WebSocket `/url-to-code`, `/url-to-code/{run_id}/follow`, REST under `/api/clone-runs/...`, `/api/clone-cost/estimate`, `/api/clone-cache`), `accounts.py` (sessions cookie, `/api/me`), `oauth.py` (GitHub/Google), `telegram.py` (bot webhook, invoices, "clone ready" notify), `shares.py`, `admin.py`, `generate_code.py` (screenshot→code), `evals*.py`, `design_systems.py`, `agent_runs.py`, `export.py`, `local_project.py`, `capabilities.py`, `home.py`, `screenshot.py`.
- `crawler/` — Playwright-based site crawler: `crawler.py` (`SiteCrawler`, `CrawlResult`, `CrawlPage`), `playwright_worker.py`, `route_discovery.py`, `route_recorder.py`, `interaction_states.py`, `responsive.py`, `embedded.py`, `media_store.py` (saved assets served under `MEDIA_ROUTE`).
- `clone_*.py` — clone pipeline pieces: `clone_runs` (run records, `CloneRun`, `new_run_id`), `clone_runner`, `clone_jobs`, `clone_pages`, `clone_routing`, `clone_backend`, `clone_visual`, `clone_cache`, `clone_mock`, `vision`, `llm_http` (`complete`, `LlmConfig`, `ProviderError`).
- `agent/` — agent loop: `runner.py`, `state.py`, `providers/` (openai, anthropic, gemini, base), `tools/` (tool definitions, asset extraction, parsing).
- `prompts/` — all LLM prompt text. `url_to_code_prompts.py` and `framework_stacks.py` for the clone flow; `create/`, `update/` for screenshot flow. Multi-line prompts use `"""..."""` (see Prompt formatting above).
- `accounts.py` (account/tier/usage logic), `db.py` (SQLite default, Postgres when `DATABASE_URL` is set; `db.q` converts `?` → `%s`), `telegram_auth.py` (verifies Telegram `initData` signatures), `costs/` (pricing, token usage), `image_generation/` (Replicate), `asset_extraction.py`, `babel_cdn.py`, `uploaded_assets/`.
- `config.py` — all env settings. Key vars: `OPENAI_API_KEY`/`OPENAI_BASE_URL`/`OPENAI_MODEL`, `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`, `GEMINI_API_KEY`/`GEMINI_MODEL`, `OPENROUTER_API_KEY`/`OPENROUTER_MODEL`, `REPLICATE_API_KEY`, `DATABASE_URL`, `SESSION_SECRET`, `CORS_ALLOWED_ORIGINS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_MINI_APP_URL`, `TELEGRAM_BOT_USERNAME`, `DEBUG_DIR`, `LOCAL_ASSET_DIR`. Default models are in `config.py`, not in the README.
- `clone_runs/` (created at runtime) holds generated projects; on Railway it must be a volume or clones are lost on deploy.
- `evals/`, `run_evals.py`, `evals_data/` — offline screenshot eval harness (see `Evaluation.md`).
- Tests: `backend/tests/test_*.py` (pytest). Type checking: pyright (`backend/pyrightconfig.json`).

## Frontend

- Routes (`frontend/src/main.tsx`): `/welcome` (public landing), `/` (the app, signed-in), `/profile` (signed-in), `/evals/*` (internal eval UIs: best-of-n, run, openai-input-compare, prompt-reports, agent-runs, sessions, compare).
- `src/components/url-to-code/` — URL-clone UI; `src/hooks/useUrlToCode.ts` — its WebSocket hook.
- `src/generateCode.ts` — screenshot→code generation client (has known lint errors, see above).
- `src/store/` — Zustand stores (`app-store.ts`, `project-store.ts`); `src/lib/` — models, stacks, prompt history, takeScreenshot, babel CDN.
- `src/config.ts` — reads `VITE_HTTP_BACKEND_URL` / `VITE_WS_BACKEND_URL` at build time (rebuild needed after change).
- Tests: Jest (`pnpm test`), plus `pnpm test:qa` for e2e QA (`src/tests/qa.test.ts`).

## Auth, payments, deployment (non-obvious)

- Session = cookie. Cross-origin deploy (Vercel site + Railway backend) needs `SameSite=None; Secure`; this is decided by whether `CORS_ALLOWED_ORIGINS` contains an `https://` origin (`routes/accounts.py: _cross_site`). If `/api/me` returns 200 with null account after deploy, CORS origins are wrong.
- Telegram: accounts match Telegram's user id; Mini App auth checks the signed `initData` (24h max age). Stars payments: invoice payload is HMAC-signed, charge id is the primary key of `payments` so webhook retries are idempotent. Notifications only go to users who already pressed `/start`.
- Plans/limits live in `accounts.py` (daily allowance per kind, `limit_overrides` table). Free: one project, one clone per day.

## Working in this repo

- Uncommitted work in progress at the time of writing: crawler headless/display handling (`crawler/crawler.py`, `crawler/playwright_worker.py`), `routes/url_to_code.py`, `UrlToCodePane.tsx`, `useUrlToCode.ts`. Recent commits are about the crawler running headless without a display (Xvfb), progress display of the crawl, and Stars billing.
- Commit style in this repo: short imperative-ish sentences, no prefixes (e.g. "Show the crawl moving instead of 0 of 30 until it stops").
- Full test/lint commands are in the README "Tests" section and in the Testing policy above.
