"""VP-5 profile lease — не более одной активной аренды на профиль
(Master Spec §13.4, §17.3).

Аренда живёт в Alembic-таблице ``run_leases`` того же ``atlas.db``. Атомарность
обеспечивает ``UNIQUE(profile_id, released_at)`` при sentinel ``released_at=''``:
две одновременные попытки acquire → одна выигрывает, вторая получает
``PROFILE_LEASED``. Это гарантирует безопасное release-before-acquire при смене
профиля (rate limit) — второго writer на профиль не возникает. После потери
heartbeat новый writer запрещён до явной :meth:`reconcile` (проверка живости
процесса) — автоугон запрещён (та же семантика, что worktree-аренда VP-2).
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
class ProfileLease:
    id: str
    run_id: str
    role: str
    profile_id: str
    worktree: str
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


class RunLeaseService:
    def __init__(self, db_path: str, *, ttl_s: float = 30.0, stale_grace_s: float = 15.0):
        self._c = sqlite3.connect(db_path, timeout=5.0)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA busy_timeout=5000;")
        self.ttl_s = ttl_s
        self.stale_grace_s = stale_grace_s

    def close(self) -> None:
        self._c.close()

    def _active(self, profile_id: str) -> ProfileLease | None:
        row = self._c.execute(
            "SELECT * FROM run_leases WHERE profile_id=? AND released_at=?",
            (profile_id, ACTIVE)).fetchone()
        if not row:
            return None
        return ProfileLease(row["id"], row["run_id"], row["role"], row["profile_id"],
                            row["worktree"], row["holder"], row["acquired_at"],
                            row["expires_at"], row["heartbeat_at"])

    def _heartbeat_stale(self, lease: ProfileLease, now: datetime) -> bool:
        return (now - _parse(lease.heartbeat_at)).total_seconds() > (self.ttl_s + self.stale_grace_s)

    def acquire(self, *, profile_id: str, run_id: str, role: str = "builder",
                worktree: str = "", holder: str = "") -> ProfileLease:
        now = _now()
        lease_id = new_id("please")
        acquired = utcnow_iso()
        expires = _iso_at(now + timedelta(seconds=self.ttl_s))
        try:
            with self._c:
                self._c.execute(
                    "INSERT INTO run_leases (id, run_id, role, profile_id, worktree, holder,"
                    " acquired_at, expires_at, heartbeat_at, released_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (lease_id, run_id, role, profile_id, worktree, holder,
                     acquired, expires, acquired, ACTIVE))
        except sqlite3.IntegrityError:
            existing = self._active(profile_id)
            if existing is None:
                raise _conflict("гонка аренды профиля без активной записи")
            if existing.is_expired(now) and self._heartbeat_stale(existing, now):
                raise _conflict(
                    f"Аренда профиля просрочена и heartbeat потерян (lease {existing.id});"
                    " требуется reconciliation, автоугон запрещён")
            raise _conflict(
                f"Профиль уже арендован (lease {existing.id}), второй writer запрещён")
        return self._active(profile_id)  # type: ignore[return-value]

    def heartbeat(self, lease_id: str) -> None:
        now = _now()
        with self._c:
            self._c.execute(
                "UPDATE run_leases SET heartbeat_at=?, expires_at=? WHERE id=? AND released_at=?",
                (utcnow_iso(), _iso_at(now + timedelta(seconds=self.ttl_s)), lease_id, ACTIVE))

    def release(self, lease_id: str) -> None:
        with self._c:
            self._c.execute("UPDATE run_leases SET released_at=? WHERE id=? AND released_at=?",
                            (utcnow_iso(), lease_id, ACTIVE))

    def reconcile(self, *, profile_id: str, process_alive: Callable[[ProfileLease], bool]) -> bool:
        """Освободить осиротевшую аренду профиля ТОЛЬКО после проверки живости (§13.4)."""
        lease = self._active(profile_id)
        if lease is None:
            return True
        if not lease.is_expired() or not self._heartbeat_stale(lease, _now()):
            return False
        if process_alive(lease):
            return False
        with self._c:
            self._c.execute("UPDATE run_leases SET released_at=? WHERE id=? AND released_at=?",
                            (utcnow_iso(), lease.id, ACTIVE))
        return True

    def active_count(self, profile_id: str) -> int:
        row = self._c.execute(
            "SELECT COUNT(*) AS n FROM run_leases WHERE profile_id=? AND released_at=?",
            (profile_id, ACTIVE)).fetchone()
        return int(row["n"])

    def active_lease(self, profile_id: str) -> ProfileLease | None:
        return self._active(profile_id)
