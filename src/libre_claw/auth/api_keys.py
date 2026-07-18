# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import platform
import socket
import subprocess
import tempfile
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
        self._use_native_macos_keychain = keyring_backend is None
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
            self._mirror_encrypted_fallback(account, keyring_value)
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

        stored_in_keyring = self._set_keyring_password(account, cleaned)
        # Keep a local encrypted mirror even when Keychain works. GUI, launchd,
        # screen, and sandboxed shells can disagree about Keychain visibility; a
        # verified fallback prevents credentials from disappearing across restarts.
        self._encrypted_file.set(account, cleaned)
        return "keyring" if stored_in_keyring else "encrypted_file"

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
        if self._use_native_macos_keychain:
            native_value = _get_macos_keychain_password(self.service_name, account)
            if native_value:
                return native_value
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

    def _mirror_encrypted_fallback(self, account: str, api_key: str) -> None:
        try:
            if self._encrypted_file.get(account) != api_key:
                self._encrypted_file.set(account, api_key)
        except KeyStorageError:
            # A usable keyring credential must remain usable even if a stale
            # fallback file cannot be repaired in the current launch context.
            return


@dataclass(frozen=True)
class EncryptedKeyFile:
    path: Path
    key: bytes | None = None
    master_key_path: Path | None = None

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
            payload = self.path.read_bytes()
            decrypted, used_legacy_key = self._decrypt(payload)
            raw = json.loads(decrypted.decode("utf-8"))
        except InvalidToken as exc:
            msg = (
                f"Could not decrypt encrypted key file {self.path}. The file was created "
                "with a different machine identity and no compatible legacy identity was found."
            )
            raise KeyStorageError(msg) from exc
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = f"Could not read encrypted key file {self.path}: {exc or type(exc).__name__}"
            raise KeyStorageError(msg) from exc

        if not isinstance(raw, dict):
            raise KeyStorageError(f"Encrypted key file {self.path} has invalid content.")
        entries = {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}
        if used_legacy_key:
            self._preserve_legacy_payload(payload)
            self._write_entries(entries)
        return entries

    def _write_entries(self, entries: dict[str, str]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            encrypted = Fernet(self._encryption_key()).encrypt(
                json.dumps(entries, sort_keys=True).encode("utf-8")
            )
            _atomic_private_write(self.path, encrypted)
        except (OSError, ValueError) as exc:
            msg = f"Could not write encrypted key file {self.path}: {exc}"
            raise KeyStorageError(msg) from exc

    def _decrypt(self, payload: bytes) -> tuple[bytes, bool]:
        if self.key is not None:
            return Fernet(self.key).decrypt(payload), False

        stable_key = self._read_master_key()
        if stable_key is not None:
            try:
                return Fernet(stable_key).decrypt(payload), False
            except InvalidToken:
                # A prior process may have created the master key immediately
                # before migrating the legacy payload. Continue the migration.
                pass

        for legacy_key in _legacy_machine_keys():
            if legacy_key == stable_key:
                continue
            try:
                return Fernet(legacy_key).decrypt(payload), True
            except InvalidToken:
                continue
        raise InvalidToken

    def _encryption_key(self) -> bytes:
        if self.key is not None:
            return self.key
        existing = self._read_master_key()
        if existing is not None:
            return existing

        key_path = self._resolved_master_key_path()
        generated = Fernet.generate_key()
        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            existing = self._read_master_key()
            if existing is None:  # pragma: no cover - defensive race guard.
                raise KeyStorageError(f"Local key file {key_path} disappeared during creation.")
            return existing
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(generated)
                handle.flush()
                os.fsync(handle.fileno())
            key_path.chmod(0o600)
        except Exception:
            key_path.unlink(missing_ok=True)
            raise
        return generated

    def _read_master_key(self) -> bytes | None:
        key_path = self._resolved_master_key_path()
        if not key_path.exists():
            return None
        try:
            key = key_path.read_bytes().strip()
            Fernet(key)
        except (OSError, ValueError) as exc:
            msg = f"Could not read local key file {key_path}: {exc or type(exc).__name__}"
            raise KeyStorageError(msg) from exc
        return key

    def _resolved_master_key_path(self) -> Path:
        return self.master_key_path or self.path.with_name(f"{self.path.name}.key")

    def _preserve_legacy_payload(self, payload: bytes) -> None:
        backup_path = self.path.with_name(f"{self.path.name}.legacy")
        try:
            descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            backup_path.chmod(0o600)
        except OSError as exc:
            backup_path.unlink(missing_ok=True)
            raise KeyStorageError(f"Could not preserve legacy key file at {backup_path}: {exc}") from exc


def _account_name(provider_name: str) -> str:
    return provider_name.strip().lower()


def _get_macos_keychain_password(service_name: str, account: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                service_name,
                "-a",
                account,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.rstrip("\r\n")
    return value or None


def _derive_machine_key() -> bytes:
    return _legacy_machine_key(getpass.getuser(), platform.node(), Path.home())


def _legacy_machine_keys() -> tuple[bytes, ...]:
    """Return keys used by pre-migration releases without exposing identities."""

    users = _unique_strings(
        getpass.getuser(),
        os.getenv("USER", ""),
        os.getenv("LOGNAME", ""),
    )
    homes = _unique_strings(str(Path.home()), os.getenv("HOME", ""))
    nodes = _legacy_node_names()
    keys: list[bytes] = []
    for user in users:
        for node in nodes:
            for home in homes:
                candidate = _legacy_machine_key(user, node, Path(home))
                if candidate not in keys:
                    keys.append(candidate)
    return tuple(keys)


def _legacy_machine_key(user: str, node: str, home: Path) -> bytes:
    # Compatibility only: old releases bound the fallback file to mutable host
    # labels. Successful reads are immediately re-encrypted with a random key.
    material = "|".join(["libre-claw", user, node, str(home)]).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return base64.urlsafe_b64encode(digest)


def _legacy_node_names() -> tuple[str, ...]:
    names = list(
        _unique_strings(
            platform.node(),
            socket.gethostname(),
            os.getenv("HOSTNAME", ""),
        )
    )
    if platform.system() == "Darwin":
        for key in ("HostName", "LocalHostName", "ComputerName"):
            try:
                result = subprocess.run(
                    ["scutil", "--get", key],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0:
                names.append(result.stdout.strip())

    expanded: list[str] = []
    for name in names:
        if not name:
            continue
        expanded.append(name)
        without_local = name.removesuffix(".local")
        expanded.extend((without_local, f"{without_local}.local"))
        hyphenated = without_local.replace(" ", "-")
        expanded.extend((hyphenated, f"{hyphenated}.local"))
    return _unique_strings(*expanded)


def _unique_strings(*values: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _atomic_private_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise
