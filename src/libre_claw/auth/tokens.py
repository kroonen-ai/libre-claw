# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libre_claw.config import AuthConfig


class TokenError(RuntimeError):
    """Raised when a Libre Claw session token cannot be issued or verified."""


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    issuer: str
    issued_at: int
    expires_at: int
    scopes: tuple[str, ...]


class TokenManager:
    """Small HS256 JWT issuer for local dashboard/session scaffolding."""

    def __init__(self, secret: str, issuer: str, token_ttl_seconds: int) -> None:
        if not secret:
            raise TokenError("JWT secret must not be empty.")
        if token_ttl_seconds < 1:
            raise TokenError("Token TTL must be at least one second.")
        self.secret = secret.encode("utf-8")
        self.issuer = issuer
        self.token_ttl_seconds = token_ttl_seconds

    @classmethod
    def from_config(cls, config: AuthConfig) -> TokenManager:
        secret = os.getenv(config.jwt_secret_env) or _load_or_create_local_secret(config)
        return cls(secret=secret, issuer=config.oauth_issuer, token_ttl_seconds=config.token_ttl_seconds)

    def issue(self, subject: str, scopes: tuple[str, ...] = ()) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": self.issuer,
            "sub": subject,
            "iat": now,
            "exp": now + self.token_ttl_seconds,
            "scope": " ".join(scopes),
        }
        return _encode_jwt(payload, self.secret)

    def verify(self, token: str) -> TokenClaims:
        payload = _decode_jwt(token, self.secret)
        issuer = _claim_str(payload, "iss")
        if issuer != self.issuer:
            raise TokenError("Token issuer does not match Libre Claw config.")

        expires_at = _claim_int(payload, "exp")
        if expires_at < int(time.time()):
            raise TokenError("Token has expired.")

        scope = payload.get("scope", "")
        scopes = tuple(str(scope).split()) if scope else ()
        return TokenClaims(
            subject=_claim_str(payload, "sub"),
            issuer=issuer,
            issued_at=_claim_int(payload, "iat"),
            expires_at=expires_at,
            scopes=scopes,
        )


def _encode_jwt(payload: dict[str, Any], secret: bytes) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64_json(header),
            _b64_json(payload),
        ]
    )
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _b64_bytes(signature)


def _decode_jwt(token: str, secret: bytes) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("Token is not a compact JWT.")

    signing_input = ".".join(parts[:2])
    expected = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    supplied = _b64_decode(parts[2])
    if not hmac.compare_digest(expected, supplied):
        raise TokenError("Token signature is invalid.")

    try:
        header = json.loads(_b64_decode(parts[0]).decode("utf-8"))
        payload = json.loads(_b64_decode(parts[1]).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TokenError("Token payload is invalid.") from exc

    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise TokenError("Token algorithm is unsupported.")
    if not isinstance(payload, dict):
        raise TokenError("Token claims are invalid.")
    return payload


def _claim_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TokenError(f"Token claim {key} is missing.")
    return value


def _claim_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise TokenError(f"Token claim {key} is missing.")
    return value


def _b64_json(value: dict[str, Any]) -> str:
    return _b64_bytes(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _b64_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except ValueError as exc:
        raise TokenError("Token base64 segment is invalid.") from exc


def _load_or_create_local_secret(config: AuthConfig) -> str:
    secret_path = _local_secret_path(config)
    try:
        secret = secret_path.read_text(encoding="utf-8").strip()
        if secret:
            _harden_local_secret(secret_path)
            return secret
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise TokenError(f"Could not read local JWT secret at {secret_path}: {exc}") from exc

    secret = secrets.token_urlsafe(48)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secret + "\n")
    except OSError as exc:
        raise TokenError(f"Could not create local JWT secret at {secret_path}: {exc}") from exc
    _harden_local_secret(secret_path)
    return secret


def _local_secret_path(config: AuthConfig) -> Path:
    return config.fallback_keys_path.expanduser().with_name(".jwt-secret")


def _harden_local_secret(secret_path: Path) -> None:
    if os.name == "nt":
        return
    try:
        secret_path.chmod(0o600)
    except OSError as exc:
        raise TokenError(f"Could not lock down local JWT secret at {secret_path}: {exc}") from exc
