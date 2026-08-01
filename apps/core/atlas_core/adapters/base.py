"""Контракт AgentAdapter (Master Spec §12).

Адаптер переводит общий контракт Atlas в конкретные команды CLI. Реальные
адаптеры строят argv для ``codex exec --json`` / ``claude -p`` и разбирают
структурированный вывод; fake-адаптеры детерминированно моделируют те же
переходы для воспроизводимой приёмки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..capacity import Capacity
from ..contracts import JobPackage, RunResult, SessionCapability


@dataclass
class AdapterResult:
    """Обёртка результата вызова адаптера."""

    result: RunResult
    handoff_state: dict = field(default_factory=dict)  # прогресс для продолжения


@runtime_checkable
class AgentAdapter(Protocol):
    provider: str

    def discover_capabilities(self) -> list[SessionCapability]:
        """Какие session-возможности поддерживает адаптер (§12.3)."""

    def auth_status(self, root_path: str) -> dict:
        """Проверяемый статус авторизации без раскрытия credentials (§11.5)."""

    def build_start_argv(self, job: JobPackage) -> list[str]:
        """Построить argv для нового структурного запуска (§12.1/§12.2)."""

    def build_resume_argv(self, session_id: str, job: JobPackage) -> list[str]:
        """Построить argv для продолжения session по ID."""

    def start(self, job: JobPackage, *, profile_alias: str, root_path: str) -> AdapterResult:
        """Выполнить новый запуск и вернуть структурный результат."""

    def resume(self, session_id: str, job: JobPackage, *, profile_alias: str, root_path: str) -> AdapterResult:
        """Продолжить session по ID."""

    def capacity(self, root_path: str) -> Capacity:
        """Вернуть ёмкость; при отсутствии стабильного источника — UNKNOWN."""
