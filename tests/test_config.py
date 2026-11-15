"""Unit tests for the settings store."""

from __future__ import annotations

import json

import pytest

from server.config import ConfigStore, default_config_dir
from server.errors import ApiError


def test_default_config_dir_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("KL_CONFIG_DIR", str(tmp_path / "custom-config"))
    assert default_config_dir() == (tmp_path / "custom-config").resolve()

    monkeypatch.delenv("KL_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    if __import__("os").name == "nt":
        assert default_config_dir() == (tmp_path / "appdata" / "KnowledgeLib").resolve()


def test_defaults_and_mask(tmp_path):
    store = ConfigStore(tmp_path / "config", tmp_path)
    public = store.public()
    assert public["api"]["hasApiKey"] is False
    assert public["api"]["baseUrl"] == "https://api.deepseek.com"
    assert public["kb"]["rootExists"] is True
    assert (tmp_path / "library").is_dir()

    store.update({"api": {"apiKey": "sk-abcdefghij", "model": "my-model"}})
    public = store.public()
    assert public["api"]["hasApiKey"] is True
    assert public["api"]["apiKeyMasked"] == "sk-****ghij"
    assert "sk-abcdefghij" not in json.dumps(public)
    assert store.model() == "my-model"


def test_empty_key_keeps_existing_and_clear(tmp_path):
    store = ConfigStore(tmp_path / "config", tmp_path)
    store.update({"api": {"apiKey": "sk-first-key"}})
    store.update({"api": {"apiKey": ""}})
    assert store.api_key() == "sk-first-key"
    store.update({"api": {"clearApiKey": True}})
    assert store.api_key() == ""
    assert store.public()["api"]["hasApiKey"] is False


def test_environment_priority(tmp_path):
    store = ConfigStore(tmp_path / "config", tmp_path, {"KL_API_KEY": "env-key", "KL_MODEL": "env-model"})
    public = store.public()
    assert store.api_key() == "env-key"
    assert public["api"]["keySource"] == "env"
    assert store.model() == "env-model"
    assert public["api"]["envModel"] is True


def test_web_key_inherits_api_key(tmp_path):
    store = ConfigStore(tmp_path / "config", tmp_path)
    store.update({"api": {"apiKey": "sk-chat"}})
    assert store.web_key() == "sk-chat"
    assert store.public()["web"]["keySource"] == "inherit"
    store.update({"web": {"apiKey": "sk-web"}})
    assert store.web_key() == "sk-web"
    assert store.public()["web"]["keySource"] == "settings"


def test_corrupt_file_is_backed_up(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text("{not json", encoding="utf-8")
    store = ConfigStore(config, tmp_path)
    assert store.warnings
    assert (config / "settings.json").is_file()
    backups = list(config.glob("settings.json.corrupt-*"))
    assert backups


def test_user_config_dir_and_legacy_migration(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(
        json.dumps({"api": {"apiKey": "legacy-key", "model": "legacy-model"}}),
        encoding="utf-8",
    )
    config = tmp_path / "config"
    store = ConfigStore(config, data)
    assert (config / "settings.json").is_file()
    assert store.api_key() == "legacy-key"
    assert store.model() == "legacy-model"
    assert store.public()["configPath"] == str((config / "settings.json").resolve())
    assert store.warnings


def test_invalid_values_rejected(tmp_path):
    store = ConfigStore(tmp_path / "config", tmp_path)
    with pytest.raises(ApiError):
        store.update({"api": {"baseUrl": "file:///etc/passwd"}})
    with pytest.raises(ApiError):
        store.update({"web": {"searchCount": 0}})
    with pytest.raises(ApiError):
        store.update({"kb": {"topK": 99}})
    with pytest.raises(ApiError):
        store.update({"api": {"model": ""}})


def test_root_update_creates_directory(tmp_path):
    store = ConfigStore(tmp_path / "config", tmp_path)
    target = tmp_path / "somewhere" / "books"
    store.update({"kb": {"root": str(target)}})
    assert target.is_dir()
    assert store.public()["kb"]["root"] == str(target.resolve())
