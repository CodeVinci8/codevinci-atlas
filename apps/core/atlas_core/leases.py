"""Один writer на worktree (Master Spec §13.4, §30.2).

Lease связан с project, worktree, run, role, expires_at, heartbeat. Гарантия
единственного writer обеспечивается на уровне БД уникальным ограничением
``UNIQUE(project_id, worktree, released_at)`` при sentinel-значении активной
аренды. Две одновременные попытки acquire → одна выигрывает, вторая получает
``WORKTREE_CONFLICT``.

После потери heartbeat новый writer запрещён до явной reconciliation
(проверка живости процесса и состояния Git), а не автоматического «угона»
аренды — это защищает от двух writer при рестарте.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .errors import AtlasError, ErrorCode, ClassifiedError, next_action_for
from .ids import new_id, utcnow_iso
from .store import Store

ACTIVE = ""  # sentinel released_at для активной аренды (NULL в SQLite не уникален)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Lease:
    id: str
    project_id: str
    worktree: str
    run_id: str | None
    role: str | None
    holder: str | None
    acquired_at: str
    expires_at: str
    heartbeat_at: str

    def is_expired(self, at: datetime | None = None) -> bool:
        return (at or _now()) >= _parse(self.expires_at)


def _conflict(msg: str) -> AtlasError:
    from .redaction import redact
    return AtlasError(ClassifiedError(
        code=ErrorCode.WORKTREE_CONFLICT, evidence=redact(msg), retryable=False,
        next_action=next_action_for(ErrorCode.WORKTREE_CONFLICT), raw_len=len(msg)))


class LeaseStore:
    def __init__(self, store: Store, *, ttl_s: float = 30.0, stale_grace_s: float = 15.0):
        self._c = store._conn
        self.ttl_s = ttl_s
        self.stale_grace_s = stale_grace_s

    def _active(self, project_id: str, worktree: str) -> Lease | None:
        row = self._c.execute(
            "SELECT * FROM leases WHERE project_id=? AND worktree=? AND released_at=?",
            (project_id, worktree, ACTIVE),
        ).fetchone()
        if not row:
            return None
        return Lease(row["id"], row["project_id"], row["worktree"], row["run_id"], row["role"],
                     row["holder"], row["acquired_at"], row["expires_at"], row["heartbeat_at"])

    def acquire(self, *, project_id: str, worktree: str, run_id: str | None = None,
                role: str | None = None, holder: str | None = None) -> Lease:
        now = _now()
        lease_id = new_id("lease")
        acquired = utcnow_iso()
        expires = (now + timedelta(seconds=self.ttl_s)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            with self._c:  # атомарная транзакция
                self._c.execute(
                    "INSERT INTO leases (id, project_id, worktree, run_id, role, holder,"
                    " acquired_at, expires_at, heartbeat_at, released_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (lease_id, project_id, worktree, run_id, role, holder, acquired, expires, acquired, ACTIVE),
                )
        except sqlite3.IntegrityError:
            existing = self._active(project_id, worktree)
            if existing is None:
                # редкая гонка: повторить один раз
                raise _conflict("lease race без активной аренды")
            if existing.is_expired(now) and self._heartbeat_stale(existing, now):
                raise _conflict(
                    f"Аренда worktree просрочена и heartbeat потерян (lease {existing.id});"
                    " требуется reconciliation, автоугон запрещён")
            raise _conflict(f"Активна другая аренда worktree (lease {existing.id}), второй writer запрещён")
        return self._active(project_id, worktree)  # type: ignore[return-value]

    def _heartbeat_stale(self, lease: Lease, now: datetime) -> bool:
        return (now - _parse(lease.heartbeat_at)).total_seconds() > (self.ttl_s + self.stale_grace_s)

    def heartbeat(self, lease_id: str) -> None:
        now = _now()
        hb = utcnow_iso()
        expires = (now + timedelta(seconds=self.ttl_s)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with self._c:
            self._c.execute(
                "UPDATE leases SET heartbeat_at=?, expires_at=? WHERE id=? AND released_at=?",
                (hb, expires, lease_id, ACTIVE),
            )

    def release(self, lease_id: str) -> None:
        with self._c:
            self._c.execute("UPDATE leases SET released_at=? WHERE id=? AND released_at=?",
                           (utcnow_iso(), lease_id, ACTIVE))

    def reconcile(self, *, project_id: str, worktree: str,
                  process_alive: Callable[[Lease], bool],
                  git_clean: Callable[[Lease], bool]) -> bool:
        """Освободить осиротевшую аренду после проверки (§13.4).

        Возвращает True, если аренда безопасно освобождена. Если процесс жив
        или worktree в конфликтном состоянии — reconciliation отклоняется.
        """

        lease = self._active(project_id, worktree)
        if lease is None:
            return True  # уже свободно
        if not lease.is_expired() or not self._heartbeat_stale(lease, _now()):
            return False  # аренда ещё живая — не трогаем
        if process_alive(lease):
            return False  # владелец жив — второй writer запрещён
        if not git_clean(lease):
            return False  # состояние Git требует ручного разбора
        with self._c:
            self._c.execute("UPDATE leases SET released_at=? WHERE id=? AND released_at=?",
                           (utcnow_iso(), lease.id, ACTIVE))
        return True

    def active_count(self, project_id: str, worktree: str) -> int:
        row = self._c.execute(
            "SELECT COUNT(*) AS n FROM leases WHERE project_id=? AND worktree=? AND released_at=?",
            (project_id, worktree, ACTIVE),
        ).fetchone()
        return int(row["n"])
