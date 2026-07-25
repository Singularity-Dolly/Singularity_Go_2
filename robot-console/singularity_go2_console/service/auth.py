"""Bearer-token auth for the private robot-service API."""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def expected_token() -> str:
    return (os.environ.get("ROBOT_AUTH_TOKEN") or os.environ.get("GATEWAY_AUTH_TOKEN") or "").strip()


async def require_bearer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> str:
    token = expected_token()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "ROBOT_AUTH_TOKEN is not set"},
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Bearer token required"},
        )
    provided = credentials.credentials or ""
    if not hmac.compare_digest(provided, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid bearer token"},
        )
    return provided


AuthDep = Annotated[str, Depends(require_bearer)]
