# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from aiohttp import web

from libre_claw.auth.tokens import TokenManager
from libre_claw.config import AuthConfig

try:
    from authlib.common.security import generate_token
except ImportError:  # pragma: no cover - dependency is installed in supported builds.
    generate_token = None  # type: ignore[assignment]


SUPPORTED_CHALLENGE_METHODS: Final[set[str]] = {"plain", "S256"}


@dataclass(frozen=True)
class AuthorizationCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    created_at: float


class OAuthStateStore:
    def __init__(self) -> None:
        self._codes: dict[str, AuthorizationCode] = {}

    def create(
        self,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        code = _generate_code()
        self._codes[code] = AuthorizationCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            created_at=time.monotonic(),
        )
        return code

    def consume(self, code: str) -> AuthorizationCode | None:
        return self._codes.pop(code, None)


class OAuthServer:
    """OAuth 2.0 PKCE scaffold for the future Libre Claw web dashboard."""

    def __init__(
        self,
        config: AuthConfig,
        token_manager: TokenManager | None = None,
        state_store: OAuthStateStore | None = None,
    ) -> None:
        self.config = config
        self.token_manager = token_manager or TokenManager.from_config(config)
        self.state_store = state_store or OAuthStateStore()

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/authorize", self.authorize)
        app.router.add_post("/token", self.token)
        app.router.add_get("/callback", self.callback)
        return app

    async def authorize(self, request: web.Request) -> web.StreamResponse:
        response_type = request.query.get("response_type")
        if response_type != "code":
            return _oauth_error("unsupported_response_type", "Only authorization code flow is supported.")

        client_id = request.query.get("client_id", "")
        if client_id != self.config.oauth_client_id:
            return _oauth_error("invalid_client", "Unknown OAuth client.")

        redirect_uri = request.query.get("redirect_uri", self.config.oauth_redirect_uri)
        if redirect_uri != self.config.oauth_redirect_uri:
            return _oauth_error("invalid_request", "Redirect URI does not match Libre Claw config.")

        code_challenge = request.query.get("code_challenge", "")
        method = request.query.get("code_challenge_method", "plain")
        if not code_challenge or method not in SUPPORTED_CHALLENGE_METHODS:
            return _oauth_error("invalid_request", "A valid PKCE code challenge is required.")

        code = self.state_store.create(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=method,
        )
        state = request.query.get("state")
        redirect_params = {"code": code}
        if state:
            redirect_params["state"] = state
        raise web.HTTPFound(location=redirect_uri + "?" + urlencode(redirect_params))

    async def token(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("grant_type") != "authorization_code":
            return _oauth_error("unsupported_grant_type", "Only authorization_code is supported.")

        code = str(form.get("code", ""))
        stored = self.state_store.consume(code)
        if stored is None:
            return _oauth_error("invalid_grant", "Authorization code is invalid or already used.")

        client_id = str(form.get("client_id", ""))
        redirect_uri = str(form.get("redirect_uri", ""))
        verifier = str(form.get("code_verifier", ""))
        if client_id != stored.client_id or redirect_uri != stored.redirect_uri:
            return _oauth_error("invalid_grant", "Client or redirect URI does not match authorization code.")
        if not _verify_pkce(verifier, stored.code_challenge, stored.code_challenge_method):
            return _oauth_error("invalid_grant", "PKCE verifier is invalid.")

        token = self.token_manager.issue(subject=client_id, scopes=("dashboard",))
        return web.json_response(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": self.config.token_ttl_seconds,
                "scope": "dashboard",
            }
        )

    async def callback(self, request: web.Request) -> web.Response:
        del request
        return web.Response(text="Libre Claw OAuth callback received. You can close this tab.")


def _oauth_error(error: str, description: str, status: int = 400) -> web.Response:
    return web.json_response({"error": error, "error_description": description}, status=status)


def _verify_pkce(verifier: str, expected_challenge: str, method: str) -> bool:
    if not verifier:
        return False
    if method == "plain":
        challenge = verifier
    else:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(challenge, expected_challenge)


def _generate_code() -> str:
    if generate_token is not None:
        return str(generate_token(48))
    return secrets.token_urlsafe(36)
