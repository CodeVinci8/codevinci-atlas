"""Структурная классификация ошибок (Master Spec §12.4).

Каждая ошибка несёт: код таксономии, redacted-evidence (без секретов),
признак retryable и рекомендованное next_action. Классификатор переводит
сырые сигналы (текст CLI, exit code, исключения) в стабильные коды, чтобы
Core мог принимать решения (сменить профиль, ждать, остановиться) без
разбора нестабильных сообщений провайдера.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .redaction import redact


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    CAPACITY_UNKNOWN = "CAPACITY_UNKNOWN"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TOOL_FAILED = "TOOL_FAILED"
    PROCESS_CRASHED = "PROCESS_CRASHED"
    TIMEOUT = "TIMEOUT"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    WORKTREE_CONFLICT = "WORKTREE_CONFLICT"
    POLICY_DENIED = "POLICY_DENIED"
    USER_INTERRUPTED = "USER_INTERRUPTED"
    UNKNOWN = "UNKNOWN"


# next_action по-русски: что Core должен сделать дальше.
_NEXT_ACTION = {
    ErrorCode.AUTH_REQUIRED: "Запросить официальный логин владельца в изолированный root профиля.",
    ErrorCode.AUTH_EXPIRED: "Пометить профиль AUTH_REQUIRED и запросить повторный логин.",
    ErrorCode.RATE_LIMITED: "Checkpoint, release lease, переключиться на совместимый профиль без второго writer.",
    ErrorCode.CAPACITY_UNKNOWN: "Показать capacity UNKNOWN честно, не вычислять фиктивный остаток.",
    ErrorCode.NETWORK_ERROR: "Ограниченный retry с backoff; не маскировать под rate limit.",
    ErrorCode.PROVIDER_UNAVAILABLE: "Ждать и повторить позже; не переключать профиль как при лимите.",
    ErrorCode.MODEL_UNAVAILABLE: "Показать effective selection и причину; выбрать доступную модель.",
    ErrorCode.PERMISSION_DENIED: "Проверить grant/capability; без расширения прав не продолжать.",
    ErrorCode.TOOL_FAILED: "Собрать redacted-evidence и передать в review как finding.",
    ErrorCode.PROCESS_CRASHED: "Пометить run INTERRUPTED, восстановить из checkpoint.",
    ErrorCode.TIMEOUT: "Прервать process group, checkpoint, при необходимости fresh session.",
    ErrorCode.OUTPUT_INVALID: "Один повтор; второй невалидный вывод — остановка (Master Spec §17.5).",
    ErrorCode.WORKTREE_CONFLICT: "Запретить второго writer; сверить Git/process перед новым lease.",
    ErrorCode.POLICY_DENIED: "Действие вне envelope; требуется owner grant.",
    ErrorCode.USER_INTERRUPTED: "Checkpoint и остановка по запросу оператора; ждать resume.",
    ErrorCode.UNKNOWN: "Собрать redacted-evidence, не гадать; эскалировать владельцу.",
}

# Коды, для которых повтор тем же профилем осмыслен.
_RETRYABLE = {
    ErrorCode.NETWORK_ERROR,
    ErrorCode.PROVIDER_UNAVAILABLE,
    ErrorCode.TIMEOUT,
    ErrorCode.OUTPUT_INVALID,
}


@dataclass(frozen=True)
class ClassifiedError:
    code: ErrorCode
    evidence: str  # уже redacted
    retryable: bool
    next_action: str
    raw_len: int = 0

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "evidence": self.evidence,
            "retryable": self.retryable,
            "next_action": self.next_action,
            "raw_len": self.raw_len,
        }


class AtlasError(Exception):
    """Исключение с уже классифицированной ошибкой."""

    def __init__(self, classified: ClassifiedError):
        self.classified = classified
        super().__init__(f"{classified.code.value}: {classified.evidence}")


# Порядок важен: более специфичные сигналы проверяются раньше общих.
_PATTERNS: list[tuple[ErrorCode, re.Pattern[str]]] = [
    (ErrorCode.RATE_LIMITED, re.compile(r"rate.?limit|429|too many requests|usage limit|quota exceeded|reset[_ ]?at", re.I)),
    (ErrorCode.AUTH_EXPIRED, re.compile(r"token expired|session expired|refresh failed|401.*expired|reauth", re.I)),
    (ErrorCode.AUTH_REQUIRED, re.compile(r"not (logged in|authenticated)|login required|no credentials|unauthorized|401|please (log ?in|run .*login)", re.I)),
    (ErrorCode.PERMISSION_DENIED, re.compile(r"permission denied|forbidden|403|not allowed", re.I)),
    (ErrorCode.POLICY_DENIED, re.compile(r"policy denied|blocked by policy|outside grant|capability .*denied|refused by policy|declined by policy", re.I)),
    (ErrorCode.MODEL_UNAVAILABLE, re.compile(r"model .*(not found|unavailable|unknown)|no such model", re.I)),
    (ErrorCode.PROVIDER_UNAVAILABLE, re.compile(r"service unavailable|503|overloaded|provider .*unavailable|502|500", re.I)),
    (ErrorCode.NETWORK_ERROR, re.compile(r"network|connection (refused|reset|timed? out)|dns|econnrefused|getaddrinfo|tls|ssl", re.I)),
    (ErrorCode.TIMEOUT, re.compile(r"timed? ?out|deadline exceeded|timeout", re.I)),
    (ErrorCode.OUTPUT_INVALID, re.compile(r"invalid (json|output|schema)|json.?decode|schema validation|malformed", re.I)),
    (ErrorCode.WORKTREE_CONFLICT, re.compile(r"worktree|another writer|lease held|index.lock|already checked out", re.I)),
    (ErrorCode.USER_INTERRUPTED, re.compile(r"interrupt|sigint|sigterm|cancell?ed by user|aborted", re.I)),
    (ErrorCode.PROCESS_CRASHED, re.compile(r"segfault|core dumped|killed|crashed|signal \d+", re.I)),
    (ErrorCode.TOOL_FAILED, re.compile(r"tool .*failed|command failed|non-?zero exit", re.I)),
]


def classify(
    raw: str = "",
    *,
    exit_code: int | None = None,
    exception: BaseException | None = None,
) -> ClassifiedError:
    """Классифицировать сырой сигнал в стабильную ошибку.

    Никогда не сохраняет сырой текст без редактирования — evidence проходит
    через :func:`redact`.
    """

    text = raw or ""
    if exception is not None:
        text = f"{text}\n{type(exception).__name__}: {exception}".strip()

    code = ErrorCode.UNKNOWN

    # Явные системные сигналы приоритетнее текстового разбора.
    if isinstance(exception, TimeoutError):
        code = ErrorCode.TIMEOUT
    elif isinstance(exception, PermissionError):
        code = ErrorCode.PERMISSION_DENIED
    elif isinstance(exception, (ConnectionError, OSError)) and exception is not None and "sock" in text.lower():
        code = ErrorCode.NETWORK_ERROR
    else:
        for candidate, pattern in _PATTERNS:
            if pattern.search(text):
                code = candidate
                break
        else:
            if exit_code is not None and exit_code != 0:
                code = ErrorCode.TOOL_FAILED

    redacted = redact(text).strip() or "(нет диагностических данных)"
    return ClassifiedError(
        code=code,
        evidence=redacted,
        retryable=code in _RETRYABLE,
        next_action=_NEXT_ACTION[code],
        raw_len=len(text),
    )


def next_action_for(code: ErrorCode) -> str:
    return _NEXT_ACTION[code]
