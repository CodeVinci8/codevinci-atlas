"""Адаптеры агентов Atlas (Master Spec §12)."""

from .base import AdapterResult, AgentAdapter
from .fake import FakeClaudeAdapter, FakeCodexAdapter, FaultInjection
from .real_claude import RealClaudeAdapter
from .real_codex import RealCodexAdapter

__all__ = [
    "AgentAdapter",
    "AdapterResult",
    "FakeCodexAdapter",
    "FakeClaudeAdapter",
    "FaultInjection",
    "RealCodexAdapter",
    "RealClaudeAdapter",
]
