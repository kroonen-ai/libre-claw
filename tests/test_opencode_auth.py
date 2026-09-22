# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet

from libre_claw.auth.api_keys import ApiKeyStore, EncryptedKeyFile, KeyStorageError
from libre_claw.auth.opencode import default_opencode_auth_path, import_opencode_key, read_opencode_api_key
from libre_claw.cli import main
from libre_claw.config import load_config, set_global_default_model
from libre_claw.opencode import canonical_opencode_provider, lookup_opencode_key
from libre_claw.providers import ProviderConfigurationError, create_provider


class NoKeychain:
    def get_password(self, *args):
        return None
    def set_password(self, *args):
        raise RuntimeError("disabled in tests")
    def delete_password(self, *args):
        return None


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for env in ("OPENCODE_API_KEY", "OPENCODE_GO_API_KEY", "XDG_DATA_HOME"):
        monkeypatch.delenv(env, raising=False)
    keyfile = EncryptedKeyFile(tmp_path / "keys", key=Fernet.generate_key())
    return ApiKeyStore("test", keyfile.path, keyring_backend=NoKeychain(), encrypted_file=keyfile)


def write_auth(tmp_path, records):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(records))
    return path


@pytest.mark.parametrize("alias,canonical", [
    ("zen", "opencode"), ("OpenCode-Zen", "opencode"), ("opencode_zen", "opencode"),
    ("go", "opencode-go"), ("opencode_go", "opencode-go"),
])
def test_aliases_share_only_their_own_credential_namespace(store, alias, canonical):
    store.set_api_key(alias, "saved-token")
    assert canonical_opencode_provider(alias) == canonical
    assert store.get_api_key(canonical).value == "saved-token"
    other = "opencode" if canonical == "opencode-go" else "opencode-go"
    assert store.get_api_key(other).value is None
    assert b"saved-token" not in store.fallback_path.read_bytes()


def test_go_specific_credentials_override_shared_environment(store, monkeypatch):
    store.set_api_key("opencode", "zen-saved")
    assert lookup_opencode_key(store, "go").value is None
    monkeypatch.setenv("OPENCODE_API_KEY", "shared-env")
    assert lookup_opencode_key(store, "go").value == "shared-env"
    store.set_api_key("go", "go-saved")
    assert lookup_opencode_key(store, "go").value == "go-saved"
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-env")
    assert lookup_opencode_key(store, "go").value == "go-env"
    assert lookup_opencode_key(store, "zen").value == "shared-env"
    assert store.key_status([("opencode-go", "OPENCODE_GO_API_KEY")]) == {"opencode-go": "environment"}


def test_custom_and_empty_env_settings_do_not_use_shared_credentials(store, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "shared-env")
    assert lookup_opencode_key(store, "go", "ANOTHER_KEY").value is None
    assert lookup_opencode_key(store, "go", "").value is None


def test_delete_removes_legacy_aliases_without_removing_other_plan(store):
    store._encrypted_file.set("zen", "legacy-token")
    store.set_api_key("go", "go-token")
    assert store.get_api_key("opencode").value == "legacy-token"
    assert store.delete_api_key("opencode") is True
    assert store.get_api_key("zen").value is None
    assert store.get_api_key("go").value == "go-token"


def test_import_is_explicit_scoped_encrypted_and_preserves_native_file(store, tmp_path):
    source = write_auth(tmp_path, {"opencode": {"type": "api", "key": "zen-token"},
        "opencode-go": {"type": "api", "key": "go-token"},
        "unrelated": {"type": "oauth", "access": "unrelated-secret"}})
    original = source.read_bytes()
    assert import_opencode_key(store, "go", path=source) == "encrypted_file"
    assert store.get_api_key("opencode-go").value == "go-token"
    assert store.get_api_key("opencode").value is None
    assert store.get_api_key("unrelated").value is None
    assert source.read_bytes() == original
    assert b"go-token" not in store.fallback_path.read_bytes()
    assert store.fallback_path.stat().st_mode & 0o777 == 0o600


def test_import_does_not_overwrite_an_existing_account_without_replace(store, tmp_path):
    store.set_api_key("zen", "original-token")
    source = write_auth(tmp_path, {"opencode": {"type": "api", "key": "replacement-token"}})
    with pytest.raises(KeyStorageError, match="--replace"):
        import_opencode_key(store, "zen", path=source)
    assert store.get_api_key("zen").value == "original-token"
    import_opencode_key(store, "zen", path=source, replace=True)
    assert store.get_api_key("zen").value == "replacement-token"


@pytest.mark.parametrize("entry", [{"type": "oauth", "access": "secret"}, {"type": "api", "key": "secret\nInjected: header"}, {"type": "api", "key": ""}])
def test_import_rejects_oauth_and_invalid_tokens_without_exposing_secrets(tmp_path, entry):
    source = write_auth(tmp_path, {"opencode": entry})
    with pytest.raises(KeyStorageError) as error:
        read_opencode_api_key("zen", source)
    assert "secret" not in str(error.value)


def test_auth_import_honors_xdg_and_does_not_search_other_accounts(store, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_opencode_auth_path() == tmp_path / "xdg" / "opencode" / "auth.json"
    source = write_auth(tmp_path, {"opencode": {"type": "api", "key": "zen-only"}})
    with pytest.raises(KeyStorageError, match="No saved opencode-go"):
        import_opencode_key(store, "go", path=source)


def test_cli_connect_import_status_and_delete_never_print_keys(store, tmp_path, monkeypatch):
    monkeypatch.setattr("libre_claw.cli.ApiKeyStore.from_config", lambda config: store)
    runner = CliRunner()
    result = runner.invoke(main, ["auth", "connect-opencode", "go"], input="go-secret\ngo-secret\n")
    assert result.exit_code == 0, result.output
    assert "go-secret" not in result.output
    assert "https://opencode.ai/auth" in result.output
    assert store.get_api_key("opencode-go").value == "go-secret"
    source = write_auth(tmp_path, {"opencode": {"type": "api", "key": "zen-secret"}})
    result = runner.invoke(main, ["auth", "import-opencode", "zen", "--path", str(source)])
    assert result.exit_code == 0, result.output
    assert "zen-secret" not in result.output
    monkeypatch.setattr("libre_claw.cli.codex_status", lambda: pytest.fail("A scoped OpenCode status must not access Codex"))
    result = runner.invoke(main, ["auth", "status", "opencode-go"])
    assert result.exit_code == 0 and "encrypted_file" in result.output
    assert "go-secret" not in result.output
    result = runner.invoke(main, ["auth", "delete-key", "go"])
    assert result.exit_code == 0
    assert store.get_api_key("opencode-go").value is None
    assert store.get_api_key("opencode").value == "zen-secret"


@pytest.mark.parametrize("alias,provider", [("zen", "opencode"), ("go", "opencode-go"), ("opencode_zen", "opencode"), ("opencode_go", "opencode-go")])
def test_provider_factory_and_saved_configuration_use_canonical_plan(store, tmp_path, alias, provider):
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[general]\ndefault_provider="{alias}"\ndefault_model="future-model"\n'
                           f'[providers.{alias}]\napi_format="responses"\ndefault_model="older-model"\n')
    config = load_config(config_path)
    store.set_api_key(alias, "test-token")
    selected = create_provider(config, api_key_store=store)
    assert selected.provider == provider
    assert selected.model == "future-model" and selected.api_format == "responses"
    assert selected.api_key == "test-token"
    assert ("/zen/go/" in selected.base_url) == (provider == "opencode-go")
    set_global_default_model(alias, "next-model", config_path=config_path)
    reloaded = load_config(config_path)
    assert reloaded.general.default_provider == provider
    assert reloaded.providers[provider]["default_model"] == "next-model"


def test_factory_reports_invalid_key_as_configuration_error(store, monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "secret\nheader")
    with pytest.raises(ProviderConfigurationError) as error:
        create_provider(load_config(), api_key_store=store, provider_name="go", model="future-model")
    assert "single-line" in str(error.value) and "secret" not in str(error.value)


def test_mac_legacy_credentials_migrate_without_missing_subprocess_name(store, tmp_path, monkeypatch):
    from libre_claw.auth import api_keys
    monkeypatch.setattr(api_keys.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(api_keys.platform, "node", lambda: "new-host")
    monkeypatch.setattr(api_keys.socket, "gethostname", lambda: "new-host")
    monkeypatch.setattr(api_keys.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="legacy-host\n"))
    legacy = api_keys._legacy_machine_key(api_keys.getpass.getuser(), "legacy-host", Path.home())
    path = tmp_path / "legacy-keys"
    path.write_bytes(Fernet(legacy).encrypt(json.dumps({"opencode": "legacy-secret"}).encode()))
    migrated = EncryptedKeyFile(path)
    assert migrated.get("opencode") == "legacy-secret"
    assert path.with_name("legacy-keys.key").exists()
    assert b"legacy-secret" not in path.read_bytes()
