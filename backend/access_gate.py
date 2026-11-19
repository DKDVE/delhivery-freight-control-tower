"""Shared-password access gate, used when Microsoft Entra ID built-in auth is not available for the Container App.

Disabled when APP_ACCESS_CODE is unset, so local dev and tests are unaffected. One shared code, an HMAC-signed session
cookie (no server-side session store) and a per-IP rate limit on sign-in attempts. /api/health and /api/ready stay open
so container probes never need the code.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

COOKIE_NAME = "fct_session"
SESSION_TTL_S = 12 * 3600
OPEN_PATHS = {"/api/health", "/api/ready", "/signin"}
RATE_LIMIT_WINDOW_S = 60
RATE_LIMIT_MAX_ATTEMPTS = 5

SIGNIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Freight Control Tower</title>
<style>
  body {{ font-family: 'DM Sans', system-ui, sans-serif; background: #F7F8FA; color: #14202B; display: flex;
          align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
  .card {{ background: #FFFFFF; border: 1px solid #E3E6EA; border-radius: 8px; padding: 32px; width: 320px; }}
  .brand {{ display: flex; align-items: center; gap: 8px; margin-bottom: 20px; }}
  .brand .dot {{ width: 10px; height: 10px; border-radius: 50%; background: #EF4136; }}
  h1 {{ font-size: 16px; margin: 0; }}
  input {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #E3E6EA; border-radius: 6px;
           font-size: 14px; margin-bottom: 12px; }}
  button {{ width: 100%; padding: 10px; background: #EF4136; color: #fff; border: none; border-radius: 6px;
            font-size: 14px; cursor: pointer; }}
  button:hover {{ background: #C21F26; }}
  .err {{ color: #C21F26; font-size: 13px; margin-bottom: 12px; }}
  .foot {{ margin-top: 16px; font-size: 11px; color: #5B6470; text-align: center; }}
</style></head>
<body>
  <div class="card">
    <div class="brand"><span class="dot"></span><h1>Freight Control Tower</h1></div>
    {error_html}
    <form method="post" action="/signin">
      <input type="password" name="code" placeholder="Access code" autofocus required>
      <button type="submit">Sign in</button>
    </form>
    <div class="foot">Student capstone prototype &middot; not an official Delhivery system</div>
  </div>
</body></html>"""


def access_code() -> str | None:
    code = os.environ.get("APP_ACCESS_CODE")
    return code if code else None


def _secret() -> bytes:
    return (access_code() or "").encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_cookie() -> str:
    exp = str(int(time.time()) + SESSION_TTL_S)
    return f"{exp}.{_sign(exp)}"


def valid_session(cookie_value: str | None) -> bool:
    if not cookie_value or "." not in cookie_value:
        return False
    exp, _, sig = cookie_value.partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(_sign(exp), sig)


_attempts: dict[str, deque] = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    now = time.time()
    q = _attempts[ip]
    while q and now - q[0] > RATE_LIMIT_WINDOW_S:
        q.popleft()
    return len(q) >= RATE_LIMIT_MAX_ATTEMPTS


def _record_attempt(ip: str) -> None:
    _attempts[ip].append(time.time())


class AccessGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if access_code() is None:
            return await call_next(request)
        path = request.url.path
        if path in OPEN_PATHS or valid_session(request.cookies.get(COOKIE_NAME)):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return RedirectResponse("/signin")


async def signin_page(_request: Request) -> Response:
    return HTMLResponse(SIGNIN_PAGE.format(error_html=""))


async def signin_submit(request: Request) -> Response:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        return HTMLResponse(SIGNIN_PAGE.format(error_html='<div class="err">Too many attempts. Wait a minute and try again.</div>'), status_code=429)
    _record_attempt(ip)
    form = await request.form()
    code = str(form.get("code") or "")
    expected = access_code()
    if expected is None or not hmac.compare_digest(code, expected):
        return HTMLResponse(SIGNIN_PAGE.format(error_html='<div class="err">Wrong code.</div>'), status_code=401)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE_NAME, make_session_cookie(), max_age=SESSION_TTL_S, httponly=True, samesite="lax",
                     secure=request.url.scheme == "https")
    return resp
