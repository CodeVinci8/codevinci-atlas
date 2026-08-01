"""Редактирование секретов и сканер secret-markers (Master Spec §30).

Два независимых механизма:

1. :func:`redact` — вырезает потенциальные секреты из любого текста, прежде
   чем он попадёт в лог, evidence, событие или artifact.
2. :func:`scan_for_secrets` / :func:`scan_paths` — сканер, который ищет
   маркеры секретов в БД, логах, artifacts и Git. Используется в приёмочном
   тесте «No credentials anywhere».

Специальный «канареечный» маркер ``ATLAS_SECRET_MARKER`` намеренно
используется в тестах: он моделирует секрет, который никогда не должен
покидать границу изоляции. Если сканер находит его в durable-состоянии —
это доказанная утечка.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Канареечный маркер для тестов утечки. Реальные секреты им не являются —
# это управляемый «секрет», за которым следит приёмка.
SECRET_MARKER = "ATLAS_SECRET_MARKER"

REDACTION_PLACEHOLDER = "[REDACTED]"

# Набор эвристик. Цель — не пропустить секрет, ложные срабатывания допустимы.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}")),
    ("oauth_json", re.compile(r'(?i)"?(access_token|refresh_token|id_token|client_secret|api[_-]?key)"?\s*[:=]\s*"?[A-Za-z0-9._\-]{8,}"?')),
    ("cookie_header", re.compile(r"(?i)\b(cookie|set-cookie)\b\s*[:=].*")),
    ("session_cookie", re.compile(r"(?i)\b(sessionKey|session-token|__Secure-[A-Za-z0-9_\-]+|_cfuvid)\s*=\s*[A-Za-z0-9._%\-]{8,}")),
    ("password", re.compile(r"(?i)\b(password|passwd|secret)\b\s*[:=]\s*\S+")),
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
]

# Пути к auth-root и credential-файлам скрываются как raw path (§11.2).
_PATH_RULES: list[re.Pattern[str]] = [
    re.compile(r"(?i)(/[^\s\"']*/)?(auth\.json|credentials?\.json|\.credentials\.json)"),
    re.compile(r"(?i)/[^\s\"']*/(profiles/(codex|claude))/[^\s\"']+"),
]


def redact(text: str | bytes | None) -> str:
    """Вернуть текст без потенциальных секретов."""

    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    out = text
    for _name, pattern in _RULES:
        out = pattern.sub(REDACTION_PLACEHOLDER, out)
    for pattern in _PATH_RULES:
        out = pattern.sub("[REDACTED_PATH]", out)
    return out


def contains_secret(text: str) -> bool:
    """True, если строка содержит потенциальный секрет или канареечный маркер."""

    if SECRET_MARKER in text:
        return True
    for _name, pattern in _RULES:
        if pattern.search(text):
            return True
    return False


@dataclass
class SecretHit:
    path: str
    line: int
    rule: str
    preview: str  # уже redacted, для отчёта


def scan_for_secrets(text: str, *, source: str = "<memory>") -> list[SecretHit]:
    """Просканировать текст на маркеры секретов."""

    hits: list[SecretHit] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if SECRET_MARKER in line:
            hits.append(SecretHit(source, lineno, "secret_marker", redact(line)[:120]))
        for name, pattern in _RULES:
            if pattern.search(line):
                hits.append(SecretHit(source, lineno, name, redact(line)[:120]))
    return hits


# Бинарные/служебные расширения, которые не сканируем как текст.
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".pack", ".idx", ".woff", ".woff2"}
_SKIP_DIRS = {".git/objects", "node_modules", "__pycache__", ".venv", "var/runner", "var/profiles"}


def scan_paths(roots: list[str], *, follow_symlinks: bool = False) -> list[SecretHit]:
    """Рекурсивно просканировать файлы на маркеры секретов.

    Не следует по symlink наружу (защита от symlink-escape, §30.1).
    Пропускает бинарные файлы и объектную БД Git по содержимому.
    """

    hits: list[SecretHit] = []
    for root in roots:
        if not os.path.exists(root):
            continue
        if os.path.isfile(root):
            hits.extend(_scan_file(root))
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
            if any(skip in dirpath for skip in _SKIP_DIRS):
                dirnames[:] = []
                continue
            for name in filenames:
                fp = os.path.join(dirpath, name)
                if os.path.splitext(name)[1].lower() in _SKIP_SUFFIXES:
                    continue
                if not follow_symlinks and os.path.islink(fp):
                    continue
                hits.extend(_scan_file(fp))
    return hits


def _scan_file(path: str) -> list[SecretHit]:
    try:
        with open(path, "r", encoding="utf-8", errors="strict") as fh:
            content = fh.read()
    except (UnicodeDecodeError, OSError, PermissionError):
        return []  # бинарный/недоступный файл — секрет как текст не найдём
    return scan_for_secrets(content, source=path)
