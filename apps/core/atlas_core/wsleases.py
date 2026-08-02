"""Один writer на worktree для VP-2 (Master Spec §13.4).

Аренда живёт в Alembic-таблице ``worktree_leases`` того же ``atlas.db``. Для
атомарного acquire используется прямое ``sqlite3``-соединение (WAL +
busy_timeout) и UNIQUE(worktree, released_at) при sentinel ``released_at=''``.
Две одновременные попытки acquire → одна выигрывает, вторая получает
``WORKTREE_CONFLICT``. После потери heartbeat новый writer запрещён до явной
:meth:`reconcile` (проверка живости процесса-писателя и чистоты Git) —
автоугон запрещён (та же семантика, что и VP-0 ``leases.py``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .errors import AtlasError, ClassifiedError, ErrorCode, next_action_for
from .ids import new_id, utcnow_iso
from .redaction import redact

ACTIVE = ""  # sentinel released_at активной аренды


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_at(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class WorktreeLease:
    id: str
    project_id: str
    worktree: str
    role: str
    holder: str
    acquired_at: str
    expires_at: str
    heartbeat_at: str

    def is_expired(self, at: datetime | None = None) -> bool:
        return (at or _now()) >= _parse(self.expires_at)


def _conflict(msg: str) -> AtlasError:
    return AtlasError(ClassifiedError(
        code=ErrorCode.WORKTREE_CONFLICT, evidence=redact(msg), retryable=False,
        next_action=next_action_for(ErrorCode.WORKTREE_CONFLICT), raw_len=len(msg)))


class WorktreeLeaseService:
    def __init__(self, db_path: str, *, ttl_s: float = 30.0, stale_grace_s: float = 15.0):
        self._c = sqlite3.connect(db_path, timeout=5.0)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA busy_timeout=5000;")
        self.ttl_s = ttl_s
        self.stale_grace_s = stale_grace_s

    def close(self) -> None:
        self._c.close()

    def _active(self, worktree: str) -> WorktreeLease | None:
        row = self._c.execute(
            "SELECT * FROM worktree_leases WHERE worktree=? AND released_at=?",
            (worktree, ACTIVE),
        ).fetchone()
        if not row:
            return None
        return WorktreeLease(row["id"], row["project_id"], row["worktree"], row["role"],
                             row["holder"], row["acquired_at"], row["expires_at"], row["heartbeat_at"])

    def _heartbeat_stale(self, lease: WorktreeLease, now: datetime) -> bool:
        return (now - _parse(lease.heartbeat_at)).total_seconds() > (self.ttl_s + self.stale_grace_s)

    def acquire(self, *, project_id: str, worktree: str, role: str = "builder",
                holder: str = "") -> WorktreeLease:
        now = _now()
        lease_id = new_id("wlease")
        acquired = utcnow_iso()
        expires = _iso_at(now + timedelta(seconds=self.ttl_s))
        try:
            with self._c:
                self._c.execute(
                    "INSERT INTO worktree_leases (id, project_id, worktree, role, holder,"
                    " acquired_at, expires_at, heartbeat_at, released_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (lease_id, project_id, worktree, role, holder, acquired, expires, acquired, ACTIVE),
                )
        except sqlite3.IntegrityError:
            existing = self._active(worktree)
            if existing is None:
                raise _conflict("гонка аренды без активной записи")
            if existing.is_expired(now) and self._heartbeat_stale(existing, now):
                raise _conflict(
                    f"Аренда worktree просрочена и heartbeat потерян (lease {existing.id});"
                    " требуется reconciliation, автоугон запрещён")
            raise _conflict(
                f"Активна другая аренда worktree (lease {existing.id}), второй writer запрещён")
        return self._active(worktree)  # type: ignore[return-value]

    def heartbeat(self, lease_id: str) -> None:
        now = _now()
        with self._c:
            self._c.execute(
                "UPDATE worktree_leases SET heartbeat_at=?, expires_at=? WHERE id=? AND released_at=?",
                (utcnow_iso(), _iso_at(now + timedelta(seconds=self.ttl_s)), lease_id, ACTIVE),
            )

    def release(self, lease_id: str) -> None:
        with self._c:
            self._c.execute("UPDATE worktree_leases SET released_at=? WHERE id=? AND released_at=?",
                            (utcnow_iso(), lease_id, ACTIVE))

    def reconcile(self, *, worktree: str,
                  process_alive: Callable[[WorktreeLease], bool],
                  git_clean: Callable[[WorktreeLease], bool]) -> bool:
        """Освободить осиротевшую аренду ТОЛЬКО после проверок (§13.4)."""
        lease = self._active(worktree)
        if lease is None:
            return True
        if not lease.is_expired() or not self._heartbeat_stale(lease, _now()):
            return False  # аренда ещё живая
        if process_alive(lease):
            return False  # владелец жив — второй writer запрещён
        if not git_clean(lease):
            return False  # Git требует ручного разбора
        with self._c:
            self._c.execute("UPDATE worktree_leases SET released_at=? WHERE id=? AND released_at=?",
                            (utcnow_iso(), lease.id, ACTIVE))
        return True

    def active_count(self, worktree: str) -> int:
        row = self._c.execute(
            "SELECT COUNT(*) AS n FROM worktree_leases WHERE worktree=? AND released_at=?",
            (worktree, ACTIVE),
        ).fetchone()
        return int(row["n"])

    def active_lease(self, worktree: str) -> WorktreeLease | None:
        return self._active(worktree)
