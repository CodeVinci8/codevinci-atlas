"""Честная модель ёмкости (Master Spec §11.6, §45.4).

Ключевое правило: если стабильного официального interface для остатка
лимита нет — статус ``UNKNOWN``, а не вычисленная фикция. VP-0 намеренно не
заявляет 5h/7d-остаток, потому что провайдеры не отдают его через стабильный
источник.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .ids import utcnow_iso


class CapacityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    LOW = "LOW"
    EXHAUSTED = "EXHAUSTED"
    UNKNOWN = "UNKNOWN"


class CapacitySource(str, Enum):
    OFFICIAL_STRUCTURED = "official_structured"
    WRAPPER = "wrapper"
    OBSERVED = "observed"
    MANUAL = "manual"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Capacity:
    status: CapacityStatus
    source: CapacitySource
    observed_at: str
    remaining_5h: int | None = None
    remaining_7d: int | None = None
    reset_at: str | None = None
    confidence: str = "low"

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "source": self.source.value,
            "observed_at": self.observed_at,
            "5h_remaining": self.remaining_5h,
            "7d_remaining": self.remaining_7d,
            "reset_at": self.reset_at,
            "confidence": self.confidence,
        }


def unknown_capacity() -> Capacity:
    """Единственно честный результат для VP-0: остаток не известен."""

    return Capacity(
        status=CapacityStatus.UNKNOWN,
        source=CapacitySource.UNKNOWN,
        observed_at=utcnow_iso(),
        remaining_5h=None,
        remaining_7d=None,
        reset_at=None,
        confidence="none",
    )
