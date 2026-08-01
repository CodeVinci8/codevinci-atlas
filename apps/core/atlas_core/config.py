"""Разрешение путей рантайма (Master Spec §7.3, §10).

Единый канонический runtime-layout — ``/var/lib/codevinci-atlas``. Ранее для
удобства прогона использовался repo-local ``./var``; это расхождение убрано —
теперь путь один и тот же для profile-init, логина, диагностики, адаптеров,
Runner и реальных probe. Для изоляции тестов используется ТОЛЬКО переменная
``ATLAS_DATA_DIR`` (временный каталог на тест), а не второй «layout».
"""

from __future__ import annotations

import os
from pathlib import Path

PROD_DATA_DIR = "/var/lib/codevinci-atlas"
PROD_SOCKET_DIR = "/run/codevinci-atlas"
PROD_SOCKET = "/run/codevinci-atlas/runner.sock"

# Права базовых каталогов: владелец atlas rwx, прочие только traverse (x),
# чтобы per-profile идентичности достигали своего 0700-root, но не листали
# чужие. Листовые root профилей — 0700 у своей идентичности.
BASE_DIR_MODE = 0o751


def repo_root() -> Path:
    # apps/core/atlas_core/config.py -> корень репозитория на 3 уровня выше
    return Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    env = os.environ.get("ATLAS_DATA_DIR")
    if env:
        return Path(env)
    return Path(PROD_DATA_DIR)


def profiles_dir(provider: str) -> Path:
    return data_dir() / "profiles" / provider


def runner_dir() -> Path:
    return data_dir() / "runner"


def artifacts_dir() -> Path:
    return data_dir() / "artifacts"


def logs_dir() -> Path:
    return data_dir() / "logs"


def db_path() -> Path:
    return data_dir() / "atlas.db"


def runner_socket() -> str:
    env = os.environ.get("ATLAS_RUNNER_SOCKET")
    if env:
        return env
    # По умолчанию сокет живёт в runner-каталоге data_dir (в тестовом ATLAS_DATA_DIR
    # это временный путь); прод-путь — PROD_SOCKET под /run.
    return str(runner_dir() / "runner.sock")


def ensure_dirs() -> None:
    base = data_dir()
    for d in (base, base / "profiles", profiles_dir("codex"), profiles_dir("claude"),
              runner_dir(), artifacts_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, BASE_DIR_MODE)
        except PermissionError:
            pass  # чужой каталог (напр. смонтированный) — не наша забота
