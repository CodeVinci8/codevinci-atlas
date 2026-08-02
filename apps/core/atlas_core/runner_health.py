"""Проверка здоровья Runner из Core по аутентифицированному UDS (Master Spec §13, §31).

Синхронная (для FastAPI threadpool): подключается к сокету Runner с
request-token и запрашивает health. Недоступность сокета/отказ токена →
честный ``OFFLINE`` (не выдуманный online).
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def _read_token(token_file: str) -> str | None:
    p = Path(token_file)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def runner_health(socket_path: str, token_file: str, *, timeout: float = 2.0) -> dict:
    """Вернуть {status, ...}. status ∈ READY | OFFLINE | UNAUTHORIZED | DEGRADED."""

    if not os.path.exists(socket_path):
        return {"status": "OFFLINE", "reason": "socket отсутствует"}
    token = _read_token(token_file)
    if not token:
        return {"status": "OFFLINE", "reason": "token-файл отсутствует"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(socket_path)
            s.sendall((json.dumps({"type": "health", "token": token}) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        if not buf:
            return {"status": "OFFLINE", "reason": "нет ответа"}
        resp = json.loads(buf.decode("utf-8").splitlines()[0])
        if resp.get("type") == "error":
            return {"status": "UNAUTHORIZED", "reason": resp.get("evidence", "отказ")}
        health = resp.get("health", {})
        health.setdefault("status", "READY")
        health["recovered_on_start"] = resp.get("recovered", [])
        return health
    except (ConnectionRefusedError, FileNotFoundError):
        return {"status": "OFFLINE", "reason": "соединение отклонено"}
    except (socket.timeout, TimeoutError):
        return {"status": "DEGRADED", "reason": "timeout"}
    except Exception as exc:  # noqa: BLE001
        from .redaction import redact
        return {"status": "OFFLINE", "reason": redact(str(exc))[:80]}
