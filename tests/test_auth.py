# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet

from libre_claw.auth.api_keys import ApiKeyStore, EncryptedKeyFile
from libre_claw.auth.oauth import OAuthStateStore, _verify_pkce
from libre_claw.auth.tokens import TokenManager


class FakeKeyring:
    def __init__(self, fail: bool = False, fail_get: bool = False) -> None:
        self.fail = fail
        self.fail_get = fail_get
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        if self.fail or self.fail_get:
            raise RuntimeError("keyring unavailable")
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        if self.fail:
            raise RuntimeError("keyring unavailable")
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        if self.fail:
            raise RuntimeError("keyring unavailable")
        self.values.pop((service_name, username), None)


def encrypted_file(path: Path) -> EncryptedKeyFile:
    return EncryptedKeyFile(path=path, key=Fernet.generate_key())


def test_api_key_store_prefers_environment(monkeypatch, tmp_path: Path) -> None:
    fake = FakeKeyring()
    fake.set_password("libre-claw", "anthropic", "stored-key")
    store = ApiKeyStore(
        service_name="libre-claw",
        fallback_path=tmp_path / ".keys",
        keyring_backend=fake,
        encrypted_file=encrypted_file(tmp_path / ".keys"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    lookup = store.get_api_key("anthropic", "ANTHROPIC_API_KEY")

    assert lookup.value == "env-key"
    assert lookup.source == "environment"


def test_api_key_store_uses_keyring_when_available(tmp_path: Path) -> None:
    store = ApiKeyStore(
        service_name="libre-claw",
        fallback_path=tmp_path / ".keys",
        keyring_backend=FakeKeyring(),
        encrypted_file=encrypted_file(tmp_path / ".keys"),
    )

    location = store.set_api_key("openai", "stored-key")
    lookup = store.get_api_key("openai")

    assert location == "keyring"
    assert lookup.value == "stored-key"
    assert lookup.source == "keyring"


def test_api_key_store_falls_back_to_encrypted_file(tmp_path: Path) -> None:
    path = tmp_path / ".keys"
    store = ApiKeyStore(
        service_name="libre-claw",
        fallback_path=path,
        keyring_backend=FakeKeyring(fail=True),
        encrypted_file=encrypted_file(path),
    )

    location = store.set_api_key("anthropic", "fallback-key")
    lookup = store.get_api_key("anthropic")

    assert location == "encrypted_file"
    assert lookup.value == "fallback-key"
    assert lookup.source == "encrypted_file"
    assert path.read_text(encoding="utf-8") != "fallback-key"


def test_api_key_store_falls_back_when_keyring_write_cannot_be_verified(tmp_path: Path) -> None:
    path = tmp_path / ".keys"
    store = ApiKeyStore(
        service_name="libre-claw",
        fallback_path=path,
        keyring_backend=FakeKeyring(fail_get=True),
        encrypted_file=encrypted_file(path),
    )

    location = store.set_api_key("local", "fallback-key")
    lookup = store.get_api_key("local")

    assert location == "encrypted_file"
    assert lookup.value == "fallback-key"
    assert lookup.source == "encrypted_file"


def test_token_manager_issues_and_verifies_jwt() -> None:
    manager = TokenManager(secret="test-secret", issuer="libre-claw", token_ttl_seconds=60)

    token = manager.issue(subject="local-user", scopes=("dashboard",))
    claims = manager.verify(token)

    assert claims.subject == "local-user"
    assert claims.issuer == "libre-claw"
    assert claims.scopes == ("dashboard",)


def test_oauth_state_store_consumes_codes_once() -> None:
    store = OAuthStateStore()
    code = store.create(
        client_id="libre-claw-local",
        redirect_uri="http://127.0.0.1:8765/callback",
        code_challenge="challenge",
        code_challenge_method="plain",
    )

    assert store.consume(code) is not None
    assert store.consume(code) is None


def test_pkce_s256_verification() -> None:
    verifier = "libre-claw-test-verifier"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    assert _verify_pkce(verifier, challenge, "S256") is True
    assert _verify_pkce("wrong", challenge, "S256") is False
