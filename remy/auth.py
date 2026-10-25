"""The admin key for routes that place a call, spend API credits or wipe state.

Reading a case made from uploaded documents needs it too (can_read); the built-in sample cases stay open.

The browser sends it as the X-Remy-Key header (web/auth.js: opened once with ?key=..., kept in
localStorage). With REMY_ADMIN_TOKEN unset (local development), these routes only answer requests made
directly on this machine: a tunnel (cloudflared, ngrok) also connects from localhost, but it adds forwarding
headers, so anything that came through one is refused."""
import hmac

from fastapi import Depends, HTTPException, Request
from starlette.requests import HTTPConnection

from . import config

HEADER = "x-remy-key"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip", "x-forwarded-host")


def _local(request: HTTPConnection) -> bool:
    """Made on this machine, not relayed by a proxy or tunnel."""
    host = request.client.host if request.client else ""
    return host in LOOPBACK and not any(h in request.headers for h in PROXY_HEADERS)


def allowed(request: Request) -> bool:
    if config.ADMIN_TOKEN:
        return hmac.compare_digest(request.headers.get(HEADER, "").encode(), config.ADMIN_TOKEN.encode())
    return _local(request)


def ws_allowed(ws: HTTPConnection) -> bool:
    """A WebSocket can't carry the header from a browser, so the key may come as ?key= instead."""
    if config.ADMIN_TOKEN:
        key = ws.headers.get(HEADER) or ws.query_params.get("key", "")
        return hmac.compare_digest(key.encode(), config.ADMIN_TOKEN.encode())
    return _local(ws)


def require_admin(request: Request) -> None:
    if not allowed(request):
        raise HTTPException(401, "admin key required", headers={"X-Remy-Auth": "required"})


ADMIN = [Depends(require_admin)]


def can_read(conn: HTTPConnection, case: dict | None) -> bool:
    """A built-in sample case (started without patient documents) is open to anyone, so demo links work. A
    case made from uploaded documents holds a real patient's name, DOB, IDs and transcripts: key only."""
    if case and case.get("sample"):
        return True
    return ws_allowed(conn) if conn.scope["type"] == "websocket" else allowed(conn)


def require_read(request: Request, case: dict | None) -> None:
    if not can_read(request, case):
        raise HTTPException(401, "admin key required", headers={"X-Remy-Auth": "required"})


def startup_warning() -> str | None:
    if config.ADMIN_TOKEN:
        return None
    return ("WARNING: REMY_ADMIN_TOKEN is not set. Approve, reset, calls and other paid actions only work "
            "from this machine (http://localhost), not through a tunnel. Set it before sharing a public URL.")
