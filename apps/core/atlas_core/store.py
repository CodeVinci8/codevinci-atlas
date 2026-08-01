"""Durable-состояние на SQLite (Master Spec §15, §23).

VP-0 использует стандартный ``sqlite3`` с WAL. Схема — минимальный срез
таблиц раздела 23.1, достаточный для доказательства: runs, agent_sessions,
profiles, leases, checkpoints, handoffs, audit_events, evidence.

Инвариант приёмки: ни одно поле не хранит credentials. Значения проходят
через redaction на входе, а приёмочный сканер проверяет отсутствие
secret-markers во всём файле БД.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from . import config
from .ids import new_id, utcnow_iso
from .redaction import contains_secret, redact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    vp_id TEXT,
    role TEXT,
    provider TEXT,
    profile_alias TEXT,
    state TEXT NOT NULL,
    session_id TEXT,
    error_code TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    provider TEXT,
    profile_alias TEXT,
    session_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    alias TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    state TEXT NOT NULL,
    cli_version TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    worktree TEXT NOT NULL,
    run_id TEXT,
    role TEXT,
    holder TEXT,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    released_at TEXT,
    UNIQUE (project_id, worktree, released_at)
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    vp_id TEXT,
    run_id TEXT,
    cause TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    vp_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT,
    correlation_id TEXT,
    message TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    kind TEXT,
    content TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL
);
"""


class SecretLeakError(Exception):
    """Попытка записать секрет в durable-состояние."""


class Store:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else config.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- защита от записи секретов -----------------------------------------
    def _guard(self, *values: object) -> None:
        for v in values:
            if isinstance(v, str) and contains_secret(v):
                raise SecretLeakError("Попытка записи секрета в БД заблокирована")

    # --- runs ---------------------------------------------------------------
    def upsert_run(self, *, run_id: str, state: str, project_id: str = "", vp_id: str = "",
                   role: str = "", provider: str = "", profile_alias: str = "",
                   session_id: str | None = None, error_code: str | None = None) -> None:
        self._guard(profile_alias, session_id or "")
        now = utcnow_iso()
        cur = self._conn.execute("SELECT version FROM runs WHERE id=?", (run_id,))
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO runs (id, project_id, vp_id, role, provider, profile_alias, state,"
                " session_id, error_code, version, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                (run_id, project_id, vp_id, role, provider, profile_alias, state, session_id,
                 error_code, now, now),
            )
        else:
            self._conn.execute(
                "UPDATE runs SET state=?, session_id=?, error_code=?, profile_alias=?,"
                " version=version+1, updated_at=? WHERE id=?",
                (state, session_id, error_code, profile_alias, now, run_id),
            )
        self._conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def runs_in_states(self, states: list[str]) -> list[dict]:
        q = ",".join("?" for _ in states)
        rows = self._conn.execute(f"SELECT * FROM runs WHERE state IN ({q})", states).fetchall()
        return [dict(r) for r in rows]

    def mark_interrupted_active(self) -> list[str]:
        """При рестарте Core: активные runs помечаются INTERRUPTED (§7.5)."""

        active = ("QUEUED", "PREPARING", "RUNNING", "COLLECTING")
        rows = self.runs_in_states(list(active))
        ids = [r["id"] for r in rows]
        for rid in ids:
            self._conn.execute("UPDATE runs SET state='INTERRUPTED', updated_at=? WHERE id=?",
                               (utcnow_iso(), rid))
        self._conn.commit()
        return ids

    # --- checkpoints / handoffs --------------------------------------------
    def save_checkpoint(self, payload: dict) -> str:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._guard(blob)
        cid = payload.get("checkpoint_id") or new_id("ckpt")
        self._conn.execute(
            "INSERT OR REPLACE INTO checkpoints (id, project_id, vp_id, run_id, cause, payload_json, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (cid, payload.get("project_id", ""), payload.get("vp_id", ""), payload.get("run_id", ""),
             payload.get("cause", ""), blob, utcnow_iso()),
        )
        self._conn.commit()
        return cid

    def latest_checkpoint(self, project_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT payload_json FROM checkpoints WHERE project_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_handoff(self, payload: dict) -> str:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._guard(blob)
        hid = payload.get("handoff_id") or new_id("hand")
        self._conn.execute(
            "INSERT OR REPLACE INTO handoffs (id, project_id, vp_id, payload_json, created_at)"
            " VALUES (?,?,?,?,?)",
            (hid, payload.get("project_id", ""), payload.get("vp_id", ""), blob, utcnow_iso()),
        )
        self._conn.commit()
        return hid

    def get_handoff(self, handoff_id: str) -> dict | None:
        row = self._conn.execute("SELECT payload_json FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    # --- audit / evidence ---------------------------------------------------
    def audit(self, event_type: str, message: str = "", *, actor: str = "core",
              correlation_id: str = "") -> str:
        msg = redact(message)
        aid = new_id("aud")
        self._conn.execute(
            "INSERT INTO audit_events (id, event_type, actor, correlation_id, message, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (aid, event_type, actor, correlation_id, msg, utcnow_iso()),
        )
        self._conn.commit()
        return aid

    def audit_events(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM audit_events ORDER BY created_at, id").fetchall()
        return [dict(r) for r in rows]

    def add_evidence(self, run_id: str, kind: str, content: str, content_hash: str = "") -> str:
        red = redact(content)
        self._guard(red)
        eid = new_id("evd")
        self._conn.execute(
            "INSERT INTO evidence (id, run_id, kind, content, content_hash, created_at) VALUES (?,?,?,?,?,?)",
            (eid, run_id, kind, red, content_hash, utcnow_iso()),
        )
        self._conn.commit()
        return eid
