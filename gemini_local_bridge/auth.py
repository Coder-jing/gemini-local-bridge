"""Lightweight bearer-token auth for the remote (HTTP) MCP transport.

Gemini web connects to a remote MCP server by URL. To keep that endpoint from
being wide open on the public internet, every request must present the shared
token configured in ``auth_token``. Two forms are accepted:

* ``Authorization: Bearer <token>`` header
* ``?token=<token>`` query parameter (convenient for URL-based connectors)

The middleware is intentionally simple: this is a personal bridge, not a
multi-tenant SaaS. For stronger guarantees (SSO, per-user identity, logging)
put Cloudflare Access in front — see README "Remote access via Cloudflare".

Use ``create_auth_asgi_middleware`` to wrap the FastMCP Starlette app, or
``require_token`` as a pure function in tests.
"""

from __future__ import annotations

import hmac
import os
import secrets
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Paths that must stay reachable without a token so OAuth/metadata discovery
# and health probes work. The MCP protocol endpoint itself is protected.
PUBLIC_PATHS = {"/", "/healthz"}


def generate_token(nbytes: int = 32) -> str:
    """Return a URL-safe random token suitable for ``auth_token``."""
    return secrets.token_urlsafe(nbytes)


def load_token(explicit: str | None, env_var: str = "GEMINI_BRIDGE_TOKEN") -> str | None:
    """Resolve the auth token from an explicit value or the environment."""
    if explicit:
        return explicit
    return os.environ.get(env_var) or None


def require_token(provided: str | None, expected: str) -> bool:
    """Constant-time comparison of the presented token against the expected."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def extract_token(request: Request) -> str | None:
    """Pull the token from the Authorization header or ``?token=`` query."""
    auth = request.headers.get("authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return request.query_params.get("token")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests that don't present the configured bearer token."""

    def __init__(self, app, token: str, enabled: bool = True):
        super().__init__(app)
        self.token = token
        self.enabled = enabled

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self.enabled:
            return await call_next(request)

        # Always allow health/metadata paths so load balancers and connectors
        # can probe the server without a secret.
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        presented = extract_token(request)
        if not require_token(presented, self.token):
            return JSONResponse(
                {
                    "error": "unauthorized",
                    "message": (
                        "Missing or invalid token. Present it as "
                        "'Authorization: Bearer <token>' or '?token=<token>'."
                    ),
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


def create_auth_asgi_middleware(token: str, enabled: bool = True):
    """Return a Starlette middleware class bound to *token*.

    Pass the result to ``app.add_middleware(...)`` before the server starts.
    """

    def _factory(app):
        return TokenAuthMiddleware(app, token=token, enabled=enabled)

    return _factory