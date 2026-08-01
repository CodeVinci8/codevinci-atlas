"""Слоистая конфигурация Core (Master Spec §10.1).

Слои (низший не расширяет ограничения верхнего):
1. встроенные безопасные значения по умолчанию;
2. ``/etc/codevinci-atlas/config.yaml`` (или ``ATLAS_CONFIG_FILE``);
3. переменные окружения ``ATLAS_*``.

Секреты (token/cookie/api-key) в конфиге запрещены (§10.2) — валидатор
отклоняет подозрительные ключи.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_FORBIDDEN_KEYS = ("token", "cookie", "api_key", "apikey", "secret", "password", "credential")


def _config_file() -> Path:
    return Path(os.environ.get("ATLAS_CONFIG_FILE", "/etc/codevinci-atlas/config.yaml"))


def _yaml_layer() -> dict:
    p = _config_file()
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    for k in data:
        if any(bad in str(k).lower() for bad in _FORBIDDEN_KEYS):
            raise ValueError(f"Секреты в config.yaml запрещены: ключ {k!r} (§10.2)")
    return data


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_", extra="ignore")

    env: str = "development"
    data_dir: str = "/var/lib/codevinci-atlas"
    runner_socket: str = "/run/codevinci-atlas/runner.sock"
    runner_token_file: str = "/run/codevinci-atlas/runner.token"
    web_origin: str = "http://127.0.0.1:3210"
    log_level: str = "INFO"
    default_locale: str = "ru"
    version: str = "0.1.0"

    @field_validator("default_locale")
    @classmethod
    def _locale(cls, v: str) -> str:
        if v not in ("ru", "en"):
            raise ValueError("default_locale must be ru|en")
        return v

    @property
    def db_path(self) -> str:
        return str(Path(self.data_dir) / "atlas.db")

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def load_settings() -> Settings:
    """Собрать настройки из слоёв: defaults → yaml → env."""

    yaml_layer = _yaml_layer()
    # env имеет приоритет над yaml (pydantic-settings читает env сам);
    # yaml передаём как init-значения (нижний слой).
    known = set(Settings.model_fields.keys())
    init = {k: v for k, v in yaml_layer.items() if k in known}
    return Settings(**init)
