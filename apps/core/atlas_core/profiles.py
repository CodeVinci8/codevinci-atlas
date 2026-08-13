"""Изоляция профилей и non-secret реестр (Master Spec §11).

Ключевые правила:

* Каждый профиль — alias изолированного auth/config root (§3, §11.1).
* Реестр хранит ТОЛЬКО non-secret метаданные: alias, provider, путь root,
  состояние, версию CLI. Никаких email, token, cookie, account id (§11.2).
* Core/Runner никогда не копируют credentials между roots и никогда не
  передают процессу чужой root (§11.1). Это гарантирует
  :func:`isolated_env`.
* Root профиля создаётся с правами ``0700``; при наличии пользователя
  ``atlas`` — во владении ``atlas:atlas`` (§7.3).
"""

from __future__ import annotations

import json
import os
import pwd
import stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import config
from .ids import utcnow_iso


class ProfileState(str, Enum):
    UNCONFIGURED = "UNCONFIGURED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    READY = "READY"
    LEASED = "LEASED"
    COOLDOWN = "COOLDOWN"
    ERROR = "ERROR"
    DRAINING = "DRAINING"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


# Переменная окружения, задающая изолированный root, зависит от провайдера.
ROOT_ENV_VAR = {
    "codex": "CODEX_HOME",
    "claude": "CLAUDE_CONFIG_DIR",
}


@dataclass
class Profile:
    alias: str  # публичное имя, напр. codex-plus-01
    provider: str  # codex | claude
    root_path: str  # изолированный auth/config root
    state: ProfileState = ProfileState.UNCONFIGURED
    cli_version: str | None = None
    last_result: str | None = None
    last_error: str | None = None
    runtime_user: str | None = None  # отдельная Unix-идентичность профиля (§7.2)
    executable_path: str | None = None  # per-profile исполняемый файл CLI (§11)
    disabled: bool = False  # owner-отключение (напр. истёкшая подписка): не в активном пуле
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    @property
    def env_var(self) -> str:
        return ROOT_ENV_VAR[self.provider]

    def to_registry_dict(self) -> dict:
        """Только non-secret поля идут в реестр."""

        return {
            "alias": self.alias,
            "provider": self.provider,
            "root_path": self.root_path,
            "state": self.state.value,
            "cli_version": self.cli_version,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "runtime_user": self.runtime_user,  # имя пользователя — не секрет
            "executable_path": self.executable_path,  # путь к CLI — не секрет
            "disabled": self.disabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_public_dict(self) -> dict:
        """Для UI/логов: без raw path (§11.2)."""

        d = self.to_registry_dict()
        d["root_path"] = "[REDACTED_PATH]"
        return d


class ProfileError(Exception):
    pass


def isolated_env(profile: Profile, base_env: dict | None = None) -> dict:
    """Собрать окружение процесса для профиля.

    Гарантии изоляции:
    * присутствует РОВНО одна root-переменная — принадлежащая этому профилю;
    * root-переменные чужого провайдера удалены;
    * никакие posторонние credentials не наследуются (минимальный allowlist).
    """

    env = dict(base_env if base_env is not None else {})
    # Удаляем любые root-переменные обоих провайдеров, чтобы исключить утечку.
    for var in ROOT_ENV_VAR.values():
        env.pop(var, None)
    # Ставим только root текущего профиля.
    env[profile.env_var] = profile.root_path
    return env


def assert_no_cross_owner(env: dict, profile: Profile) -> None:
    """Проверить, что в env нет чужого root (используется в тестах/guard)."""

    for provider, var in ROOT_ENV_VAR.items():
        if provider == profile.provider:
            continue
        if var in env:
            raise ProfileError(
                f"Изоляция нарушена: окружение профиля {profile.alias} содержит {var}"
            )


_PROVIDER_ABBR = {"codex": "cx", "claude": "cl"}


_EXE_NAME = {"codex": "codex", "claude": "claude"}


def detect_executable(root_path: str, provider: str) -> str | None:
    """Найти per-profile исполняемый файл CLI в root профиля.

    Приоритет: ``<root>/.local/bin/<exe>`` (owner-установка на профиль),
    затем глобальный из PATH. Возвращает путь или None.
    """

    import shutil
    name = _EXE_NAME[provider]
    local = os.path.join(root_path, ".local", "bin", name)
    if os.path.exists(local):
        return local
    found = shutil.which(name)
    return found


def runtime_user_for(alias: str, provider: str) -> str:
    """Детерминированное имя Unix-идентичности профиля, напр. ``atlas-cx01``.

    Это имя пользователя, а не секрет. Совпадает с именами, которые создаёт
    ``scripts/atlas-runtime-setup.sh``.
    """

    digits = "".join(ch for ch in alias if ch.isdigit()) or "00"
    return f"atlas-{_PROVIDER_ABBR.get(provider, provider[:2])}{digits[-2:].zfill(2)}"


def resolve_runtime_ids(runtime_user: str | None) -> tuple[int, int] | None:
    """(uid, gid) для идентичности профиля, либо None, если пользователь отсутствует."""

    if not runtime_user:
        return None
    try:
        rec = pwd.getpwnam(runtime_user)
    except KeyError:
        return None
    return rec.pw_uid, rec.pw_gid


def _chown_owner(path: Path, runtime_user: str | None) -> str:
    """Chown root в идентичность профиля (если есть), иначе в atlas, иначе текущий.

    Никогда не копирует содержимое — только выставляет владельца пустого root.
    """

    ids = resolve_runtime_ids(runtime_user)
    if ids is not None:
        os.chown(path, ids[0], ids[1])
        return f"{runtime_user}:{runtime_user}"
    try:
        atlas = pwd.getpwnam("atlas")
        os.chown(path, atlas.pw_uid, atlas.pw_gid)
        return "atlas:atlas"
    except KeyError:
        cur = pwd.getpwuid(os.getuid()).pw_name
        return f"{cur}:(runtime-identity-absent)"


def create_profile_root(alias: str, provider: str, runtime_user: str | None = None) -> Profile:
    """Создать изолированный root профиля с правами 0700 у идентичности профиля."""

    if provider not in ROOT_ENV_VAR:
        raise ProfileError(f"Неизвестный провайдер: {provider}")
    ru = runtime_user or runtime_user_for(alias, provider)
    root = config.profiles_dir(provider) / alias
    root.mkdir(parents=True, exist_ok=True)
    owner = _chown_owner(root, ru)  # владелец до chmod, чтобы 0700 относилось к нему
    os.chmod(root, 0o700)  # §7.3: profile roots 0700
    return Profile(
        alias=alias,
        provider=provider,
        root_path=str(root),
        state=ProfileState.AUTH_REQUIRED,  # root есть, credentials — нет
        runtime_user=ru,
        executable_path=detect_executable(str(root), provider),
        last_result=f"root создан 0700 ({owner})",
    )


def check_root_permissions(profile: Profile) -> dict:
    """Вернуть факты о правах root без чтения содержимого (§11.5, §30.4)."""

    p = Path(profile.root_path)
    if not p.exists():
        return {"exists": False, "mode": None, "owner": None, "world_readable": None,
                "owner_is_runtime_user": None}
    st = p.stat()
    mode = stat.S_IMODE(st.st_mode)
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    world_or_group_readable = bool(mode & (stat.S_IRWXG | stat.S_IRWXO))
    return {
        "exists": True,
        "mode": oct(mode),
        "owner": owner,
        "owner_uid": st.st_uid,
        "is_0700": mode == 0o700,
        "world_readable": world_or_group_readable,
        "owner_is_runtime_user": (profile.runtime_user == owner) if profile.runtime_user else None,
    }


class ProfileRegistry:
    """Non-secret реестр профилей на JSON (§11.2)."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else config.data_dir() / "profiles" / "registry.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"profiles": {}}
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def upsert(self, profile: Profile) -> None:
        data = self._load()
        profile.updated_at = utcnow_iso()
        data["profiles"][profile.alias] = profile.to_registry_dict()
        self._save(data)

    def get(self, alias: str) -> Profile | None:
        data = self._load()
        raw = data["profiles"].get(alias)
        if not raw:
            return None
        return Profile(
            alias=raw["alias"],
            provider=raw["provider"],
            root_path=raw["root_path"],
            state=ProfileState(raw["state"]),
            cli_version=raw.get("cli_version"),
            last_result=raw.get("last_result"),
            last_error=raw.get("last_error"),
            runtime_user=raw.get("runtime_user"),
            executable_path=raw.get("executable_path"),
            disabled=bool(raw.get("disabled", False)),
            created_at=raw.get("created_at", utcnow_iso()),
            updated_at=raw.get("updated_at", utcnow_iso()),
        )

    def list(self, provider: str | None = None) -> list[Profile]:
        data = self._load()
        out = []
        for alias in sorted(data["profiles"]):
            p = self.get(alias)
            if p and (provider is None or p.provider == provider):
                out.append(p)
        return out
