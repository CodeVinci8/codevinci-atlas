"""Протокол Core↔Runner: framing и request-token (Master Spec §13, §30.2).

Сообщения — newline-delimited JSON. Первое поле каждого запроса — token
(nonce), выданный вне канала и хранящийся в файле, доступном только
пользователю ``atlas`` (0600). Токен сверяется до любого исполнения.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

# Единственные разрешённые ключи «дополнительного» окружения: root профиля.
ALLOWED_ENV_EXTRA_KEYS = {"CODEX_HOME", "CLAUDE_CONFIG_DIR"}


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def write_token_file(path: str | os.PathLike, token: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(token)
    os.chmod(p, 0o600)  # только владелец (§7.3 credentials не шире 0600)


def read_token_file(path: str | os.PathLike) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: bytes) -> dict:
    return json.loads(line.decode("utf-8"))
