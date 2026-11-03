"""Replaceable authentication dependency, disabled unless an API token is set."""

import secrets

from fastapi import HTTPException, Request


async def require_auth(request: Request) -> None:
    """Apply optional bearer-token protection to chat and approval routes."""
    configured = request.app.state.settings.token
    if configured is None or not configured.get_secret_value():
        return
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        token, configured.get_secret_value()
    ):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})
