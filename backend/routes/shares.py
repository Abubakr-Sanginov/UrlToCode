"""Sharing a cloned site with someone.

A link that opens the site itself, not the code. The HTML served is the same
laid-out document the preview pane already shows - a Next.js or Vite project
has been rendered into one self-contained page, so there is nothing to build
here and nothing to install per visitor.

The public route serves markup this server did not write: it came from a
page someone else had cloned. So it is served inside a sandbox and from no
session of ours, which is what stops a shared site reaching back into this
account and its cookies. Forms are allowed, because a cloned contact form
that silently does nothing is the thing a friend would notice first.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import accounts
import clone_runs
from routes.accounts import current_account, require_account

router = APIRouter(tags=["shares"])

# Rendered from the site's own markup, then handed to the browser in an
# origin of its own that has nothing to do with this one: no cookies, no
# storage, no access to any other address on this domain.
#
# allow-forms is in, allow-same-origin is out. Without it a shared page
# cannot read the reader's session or call this API as them; with it a
# contact form on a cloned site still posts.
SHARED_PAGE_CSP = (
    "sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox"
)

BANNER = (
    '<div style="position:fixed;left:0;right:0;bottom:0;z-index:2147483647;'
    'font:13px/1.4 system-ui,sans-serif;background:#111827;color:#f9fafb;'
    'padding:8px 12px;text-align:center">'
    "This is a site made with UrlToCode, not a page from the site it copies. "
    "Do not enter a password here."
    "</div>"
)


class ShareRequest(BaseModel):
    runId: str
    pagePath: str = "/"


@router.get("/api/shares")
async def list_shares(utc_session: Optional[str] = Cookie(default=None)) -> Dict[str, Any]:
    account = require_account(utc_session)
    limits = accounts._limits(account)
    used = accounts.shares_used(account)
    return {
        "shares": accounts.list_shares(account),
        "usage": {
            "used": used,
            "limit": limits["shares_per_month"],
            "remaining": max(limits["shares_per_month"] - used, 0),
        },
    }


@router.post("/api/shares")
async def create_share(
    body: ShareRequest, utc_session: Optional[str] = Cookie(default=None)
) -> Dict[str, Any]:
    account = require_account(utc_session)
    try:
        return accounts.create_share(account, body.runId, body.pagePath)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/shares/{token}")
async def revoke_share(
    token: str, utc_session: Optional[str] = Cookie(default=None)
) -> Dict[str, Any]:
    account = require_account(utc_session)
    # A token that is not theirs reports as not found rather than as
    # forbidden, so this cannot be used to find out which tokens exist.
    if not accounts.revoke_share(account, token):
        raise HTTPException(status_code=404, detail="No such link.")
    return {"revoked": token}


@router.get("/s/{token}", response_class=HTMLResponse)
async def public_share(token: str) -> HTMLResponse:
    """Open a shared site. No account, no session, by design."""
    target = accounts.share_by_token(token)
    if target is None:
        raise HTTPException(status_code=404, detail="This link is not available.")

    run = clone_runs.load_run(target["runId"])
    if run is None:
        # The clone itself is gone - a restart on a local machine, a cleaned
        # folder. The link has nothing left to show.
        raise HTTPException(status_code=410, detail="This site is no longer stored.")

    page = run.page(target["pagePath"]) or run.page("/")
    if page is None or not page.code:
        raise HTTPException(status_code=404, detail="This page is not available.")

    return HTMLResponse(
        content=page.code + BANNER,
        headers={"Content-Security-Policy": SHARED_PAGE_CSP, "Cache-Control": "no-store"},
    )