"""Raising a limit for one person, without touching anyone else's.

Everything here is behind ADMIN_TOKEN. It is not a session and not an
account flag: there is one operator, they hold one secret, and it is
compared in constant time so the screen cannot be used to guess it one
character at a time.

An administrator grants more to a person. The tiers stay as they are, and
accounts with nothing set for them keep exactly the limits they had.
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

import accounts

router = APIRouter(prefix="/api/admin", tags=["admin"])


class LimitsRequest(BaseModel):
    # Null keeps the tier's number; 0 is a real answer meaning "none left".
    dailyActions: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    maxProjects: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    note: str = Field(default="", max_length=200)


class TierRequest(BaseModel):
    tier: str = Field(min_length=1, max_length=32)


def _configured_admin_token() -> str:
    return os.environ.get("ADMIN_TOKEN", "").strip()


def _require_admin(provided: Optional[str]) -> None:
    """Refuse unless a token is both configured and matches.

    A blank ADMIN_TOKEN does not mean "no password" - it means the screen
    does not work at all, so an unconfigured deployment can never be walked
    into by sending nothing.
    """
    expected = _configured_admin_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Set ADMIN_TOKEN in the server environment to use this.",
        )
    if not provided or not hmac.compare_digest(provided.strip(), expected):
        raise HTTPException(status_code=401, detail="That admin token is not right.")


@router.get("/accounts")
async def admin_list_accounts(
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    tiers: Dict[str, Dict[str, int]] = dict(accounts.TIERS)
    return {"accounts": accounts.list_accounts(), "tiers": tiers}


@router.post("/accounts/{account_id}/limits")
async def admin_set_limits(
    account_id: int,
    body: LimitsRequest,
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    try:
        return accounts.set_limits(
            account_id,
            daily_actions=body.dailyActions,
            max_projects=body.maxProjects,
            note=body.note,
        )
    except accounts.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/accounts/{account_id}/limits")
async def admin_clear_limits(
    account_id: int,
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    try:
        return accounts.clear_limits(account_id)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/tier")
async def admin_set_tier(
    account_id: int,
    body: TierRequest,
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    try:
        accounts.set_tier(account_id, body.tier)
        return accounts.account_summary(account_id)
    except accounts.AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
