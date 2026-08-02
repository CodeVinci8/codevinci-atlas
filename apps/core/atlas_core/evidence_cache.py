"""Evidence Cache VP-6 (Master Spec §18.7).

Ключ = ``SHA + command/version + relevant input hashes + environment + scope``.
Reuse ТОЛЬКО при точном совпадении всех компонентов, с видимой причиной. Любое
изменение SHA/версии команды/релевантного input/окружения/scope инвалидирует
запись (даёт другой ключ → miss). Cached PASS с протухшего head не используется
никогда: :meth:`invalidate_stale` помечает записи с чужим head как ``stale`` и
:meth:`try_reuse` их отвергает.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update

from .db import session_scope
from .ids import new_id
from .orm import EvidenceCacheEntry
from .productmap import canonical_json


@dataclass(frozen=True)
class CacheComponents:
    sha: str
    command: str
    command_version: str
    input_hash: str
    environment: str
    scope: str

    def key(self) -> str:
        raw = canonical_json({
            "sha": self.sha, "command": self.command,
            "command_version": self.command_version, "input_hash": self.input_hash,
            "environment": self.environment, "scope": self.scope,
        })
        return "ck_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceCache:
    """Durable Evidence Cache поверх таблицы ``evidence_cache``."""

    def store(self, comp: CacheComponents, *, passed: bool, result: dict,
              reason: str = "") -> str:
        key = comp.key()
        with session_scope() as s:
            row = s.execute(select(EvidenceCacheEntry).where(
                EvidenceCacheEntry.cache_key == key)).scalars().first()
            if row is None:
                cid = new_id("evc")
                s.add(EvidenceCacheEntry(
                    id=cid, cache_key=key, sha=comp.sha, command=comp.command,
                    command_version=comp.command_version, input_hash=comp.input_hash,
                    environment=comp.environment, scope=comp.scope,
                    result_json=canonical_json(result), passed=passed,
                    reason=reason, stale=False, created_at=_now()))
                s.commit()
                return cid
            # Тот же ключ → тот же результат детерминированно; обновляем результат.
            row.result_json = canonical_json(result)
            row.passed = passed
            row.reason = reason
            row.stale = False
            s.commit()
            return row.id

    def try_reuse(self, comp: CacheComponents) -> dict | None:
        """Вернуть запись при ТОЧНОМ совпадении ключа и non-stale, иначе None.

        Инкрементирует ``reuse_count`` и фиксирует видимую причину переиспользования.
        """

        key = comp.key()
        with session_scope() as s:
            row = s.execute(select(EvidenceCacheEntry).where(
                EvidenceCacheEntry.cache_key == key)).scalars().first()
            if row is None or row.stale:
                return None
            row.reuse_count = row.reuse_count + 1
            row.last_reused_at = _now()
            s.commit()
            d = row.to_dict()
            d["cache_reason"] = (
                f"точное совпадение ключа (sha+command+version+input+env+scope); "
                f"reuse #{row.reuse_count}")
            return d

    def invalidate_stale(self, current_head_sha: str) -> int:
        """Пометить stale все записи с ``sha != current_head_sha``.

        Гарантия §18.7: cached PASS с протухшего head больше не переиспользуется.
        Возвращает число помеченных записей.
        """

        with session_scope() as s:
            res = s.execute(
                update(EvidenceCacheEntry)
                .where(EvidenceCacheEntry.sha != current_head_sha,
                       EvidenceCacheEntry.stale == False)  # noqa: E712
                .values(stale=True))
            s.commit()
            return int(res.rowcount or 0)

    def get(self, comp: CacheComponents) -> dict | None:
        with session_scope() as s:
            row = s.execute(select(EvidenceCacheEntry).where(
                EvidenceCacheEntry.cache_key == comp.key())).scalars().first()
            return row.to_dict() if row else None
