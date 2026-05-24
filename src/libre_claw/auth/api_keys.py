# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from cryptography.fernet import Fernet, InvalidToken

from libre_claw.config import AuthConfig

try:
    import keyring
except ImportError:  # pragma: no cover - dependency is installed in supported builds.
    keyring = None  # type: ignore[assignment]


ApiKeySource = Literal["environment", "keyring", "encrypted_file", "missing"]
StorageLocation = Literal["keyring", "encrypted_file"]


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class KeyStorageError(RuntimeError):
    """Raised when API key storage cannot be read or written."""


@dataclass(frozen=True)
class ApiKeyLookup:
    value: str | None
    source: ApiKeySource

    @property
    def found(self) -> bool:
        return self.value is not None


class ApiKeyStore:
    """API key storage with environment, keyring, and encrypted-file fallback."""

    def __init__(
        self,
        service_name: str,
        fallback_path: Path,
        keyring_backend: KeyringBackend | None = None,
        encrypted_file: EncryptedKeyFile | None = None,
    ) -> None:
        self.service_name = service_name
        self.fallback_path = fallback_path
        self._keyring_backend = keyring_backend if keyring_backend is not None else keyring
        self._encrypted_file = encrypted_file or EncryptedKeyFile(fallback_path)

    @classmethod
    def from_config(cls, config: AuthConfig) -> ApiKeyStore:
        return cls(service_name=config.keyring_service, fallback_path=config.fallback_keys_path)

    def get_api_key(self, provider_name: str, env_var: str | None = None) -> ApiKeyLookup:
        if env_var:
            value = os.getenv(env_var)
            if value:
                return ApiKeyLookup(value=value, source="environment")

        account = _account_name(provider_name)
        keyring_value = self._get_keyring_password(account)
        if keyring_value:
            return ApiKeyLookup(value=keyring_value, source="keyring")

        fallback_value = self._encrypted_file.get(account)
        if fallback_value:
            return ApiKeyLookup(value=fallback_value, source="encrypted_file")

        return ApiKeyLookup(value=None, source="missing")

    def set_api_key(self, provider_name: str, api_key: str) -> StorageLocation:
        account = _account_name(provider_name)
        cleaned = api_key.strip()
        if not cleaned:
            raise KeyStorageError("API key must not be empty.")

        if self._set_keyring_password(account, cleaned):
            return "keyring"

        self._encrypted_file.set(account, cleaned)
        return "encrypted_file"

    def delete_api_key(self, provider_name: str) -> bool:
        account = _account_name(provider_name)
        removed = self._delete_keyring_password(account)
        return self._encrypted_file.delete(account) or removed

    def key_status(self, providers: list[tuple[str, str | None]]) -> dict[str, ApiKeySource]:
        return {
            provider_name: self.get_api_key(provider_name, env_var).source
            for provider_name, env_var in providers
        }

    def _get_keyring_password(self, account: str) -> str | None:
        if self._keyring_backend is None:
            return None
        try:
            return self._keyring_backend.get_password(self.service_name, account)
        except Exception:
            return None

    def _set_keyring_password(self, account: str, api_key: str) -> bool:
        if self._keyring_backend is None:
            return False
        try:
            self._keyring_backend.set_password(self.service_name, account, api_key)
            return self._keyring_backend.get_password(self.service_name, account) == api_key
        except Exception:
            return False

    def _delete_keyring_password(self, account: str) -> bool:
        if self._keyring_backend is None:
            return False
        try:
            self._keyring_backend.delete_password(self.service_name, account)
            return True
        except Exception:
            return False


@dataclass(frozen=True)
class EncryptedKeyFile:
    path: Path
    key: bytes | None = None

    def get(self, account: str) -> str | None:
        return self._read_entries().get(account)

    def set(self, account: str, api_key: str) -> None:
        entries = self._read_entries()
        entries[account] = api_key
        self._write_entries(entries)

    def delete(self, account: str) -> bool:
        entries = self._read_entries()
        if account not in entries:
            return False
        del entries[account]
        self._write_entries(entries)
        return True

    def _read_entries(self) -> dict[str, str]:
        if not self.path.exists():
            return {}

        try:
            decrypted = self._fernet().decrypt(self.path.read_bytes())
            raw = json.loads(decrypted.decode("utf-8"))
        except (OSError, InvalidToken, json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = f"Could not read encrypted key file {self.path}: {exc}"
            raise KeyStorageError(msg) from exc

        if not isinstance(raw, dict):
            raise KeyStorageError(f"Encrypted key file {self.path} has invalid content.")
        return {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}

    def _write_entries(self, entries: dict[str, str]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            encrypted = self._fernet().encrypt(json.dumps(entries, sort_keys=True).encode("utf-8"))
            self.path.write_bytes(encrypted)
            self.path.chmod(0o600)
        except OSError as exc:
            msg = f"Could not write encrypted key file {self.path}: {exc}"
            raise KeyStorageError(msg) from exc

    def _fernet(self) -> Fernet:
        return Fernet(self.key or _derive_machine_key())


def _account_name(provider_name: str) -> str:
    return provider_name.strip().lower()


def _derive_machine_key() -> bytes:
    material = "|".join(
        [
            "libre-claw",
            getpass.getuser(),
            platform.node(),
            str(Path.home()),
        ]
    ).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return base64.urlsafe_b64encode(digest)
