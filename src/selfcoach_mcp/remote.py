"""Remote mode: the MCP server over HTTPS with OAuth, for claude.ai, the Claude
mobile apps and any client that connects to a URL.

Single-user by design: whoever knows the passphrase owns this server's Garmin
account. OAuth clients register themselves (dynamic client registration, as
claude.ai does), but only redirect URIs on allowed hosts are accepted and every
authorization needs the passphrase. Tokens are stored hashed in the database so
connectors survive restarts.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import time
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import db
from .config import OAUTH_REDIRECT_HOSTS, PASSPHRASE, PUBLIC_URL

SCOPE = "selfcoach"
ACCESS_TTL = 24 * 3600
REFRESH_TTL = 180 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 600
MAX_FAILURES = 10
FAILURE_WINDOW = 15 * 60


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _host_allowed(uri: str) -> bool:
    u = urlparse(uri)
    host = (u.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return u.scheme in ("http", "https")
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in OAUTH_REDIRECT_HOSTS)


class Provider:
    """OAuthAuthorizationServerProvider for a single athlete."""

    def __init__(self) -> None:
        self.pending: dict[str, tuple[float, OAuthClientInformationFull, AuthorizationParams]] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.failures: list[float] = []

    # --- clients -----------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        rows = db.query(select(db.oauth_clients.c.info).where(db.oauth_clients.c.client_id == client_id))
        return OAuthClientInformationFull.model_validate(rows[0]["info"]) if rows else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]
        bad = [u for u in uris if not _host_allowed(u)]
        if not uris or bad:
            raise RegistrationError("invalid_redirect_uri",
                                    f"Redirect URI not allowed: {', '.join(bad) or 'none given'}")
        db.upsert(db.oauth_clients, [{"client_id": client_info.client_id,
                                      "info": client_info.model_dump(mode="json"),
                                      "created_at": db.now_utc()}])

    # --- authorization -------------------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not _host_allowed(str(params.redirect_uri)):
            raise AuthorizeError("invalid_request", "Redirect URI not allowed")
        self._expire()
        req = secrets.token_urlsafe(24)
        self.pending[req] = (time.time() + PENDING_TTL, client, params)
        return f"{PUBLIC_URL}/login?req={req}"

    def _expire(self) -> None:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
        self.codes = {k: v for k, v in self.codes.items() if v.expires_at > now}
        self.failures = [t for t in self.failures if now - t < FAILURE_WINDOW]

    def complete(self, req: str, passphrase: str) -> str | None:
        """Check the passphrase; on success return the client's redirect URL with a code."""
        self._expire()
        entry = self.pending.get(req)
        if entry is None:
            raise AuthorizeError("invalid_request", "This login link has expired. Start again from the app.")
        if len(self.failures) >= MAX_FAILURES:
            raise AuthorizeError("access_denied", "Too many wrong attempts. Wait 15 minutes.")
        if not PASSPHRASE or not hmac.compare_digest(passphrase.encode(), PASSPHRASE.encode()):
            self.failures.append(time.time())
            return None
        del self.pending[req]
        _, client, params = entry
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [SCOPE], expires_at=time.time() + CODE_TTL,
            client_id=client.client_id, code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri, redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource, subject="athlete")
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        self._expire()
        code = self.codes.get(authorization_code)
        return code if code and code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        if self.codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "Authorization code already used or expired")
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    # --- tokens ------------------------------------------------------------------------
    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        db.upsert(db.oauth_tokens, [
            {"token_hash": _hash(access), "kind": "access", "client_id": client_id, "scopes": " ".join(scopes),
             "resource": resource, "expires_at": now + ACCESS_TTL, "pair": _hash(refresh)},
            {"token_hash": _hash(refresh), "kind": "refresh", "client_id": client_id, "scopes": " ".join(scopes),
             "resource": resource, "expires_at": now + REFRESH_TTL, "pair": _hash(access)},
        ])
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, scope=" ".join(scopes),
                          refresh_token=refresh)

    def _load(self, token: str, kind: str) -> dict | None:
        t = db.oauth_tokens
        rows = db.query(select(t).where(t.c.token_hash == _hash(token), t.c.kind == kind))
        if not rows or rows[0]["expires_at"] < time.time():
            return None
        return rows[0]

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        row = self._load(refresh_token, "refresh")
        if not row or row["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=row["client_id"], scopes=row["scopes"].split(),
                            expires_at=row["expires_at"], resource=row["resource"], subject="athlete")

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        await self.revoke_token(refresh_token)  # rotate
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str):
        row = self._load(token, "access")
        if not row:
            return None
        return AccessToken(token=token, client_id=row["client_id"], scopes=row["scopes"].split(),
                           expires_at=row["expires_at"], resource=row["resource"], subject="athlete")

    async def revoke_token(self, token) -> None:
        t = db.oauth_tokens
        h = _hash(token.token)
        rows = db.query(select(t.c.pair).where(t.c.token_hash == h))
        hashes = [h] + [r["pair"] for r in rows if r["pair"]]
        db.execute(t.delete().where(t.c.token_hash.in_(hashes)))


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>selfcoach · sign in</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;background:#0f1115;color:#e8e8ea;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}}form{{background:#181b22;padding:28px;
border-radius:14px;width:min(340px,88vw)}}h1{{font-size:20px;margin:0 0 6px}}p{{color:#9a9ca5;font-size:14px;
margin:0 0 18px}}input{{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;border:1px solid #2c303a;
background:#0f1115;color:#e8e8ea;font-size:16px}}button{{margin-top:14px;width:100%;padding:12px;border:0;
border-radius:8px;background:#3d7bfd;color:#fff;font-size:16px}}.err{{color:#ff6b6b}}</style></head><body>
<form method="post" action="/login"><h1>selfcoach</h1><p>{message}</p>
<input type="hidden" name="req" value="{req}"><input type="password" name="passphrase"
placeholder="Passphrase" autofocus autocomplete="current-password">
<button type="submit">Connect</button></form></body></html>"""


def install(mcp, provider: Provider) -> None:
    """Add the login page and a health check to the HTTP app."""

    @mcp.custom_route("/login", methods=["GET", "POST"], include_in_schema=False)
    async def login(request: Request) -> Response:
        if request.method == "GET":
            req = request.query_params.get("req", "")
            return HTMLResponse(LOGIN_PAGE.format(
                req=html.escape(req), message="Enter your passphrase to connect Claude to your Garmin data."))
        form = await request.form()
        req = str(form.get("req", ""))
        try:
            target = provider.complete(req, str(form.get("passphrase", "")))
        except AuthorizeError as e:
            return HTMLResponse(LOGIN_PAGE.format(req=html.escape(req),
                                                  message=f'<span class="err">{html.escape(e.error_description or "")}</span>'),
                                status_code=400)
        if target is None:
            return HTMLResponse(LOGIN_PAGE.format(req=html.escape(req),
                                                  message='<span class="err">Wrong passphrase.</span>'),
                                status_code=401)
        return RedirectResponse(target, status_code=302)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})


def auth_settings() -> AuthSettings:
    return AuthSettings(
        issuer_url=PUBLIC_URL,
        resource_server_url=f"{PUBLIC_URL}/mcp",
        validate_token_resource=False,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[SCOPE],
    )
