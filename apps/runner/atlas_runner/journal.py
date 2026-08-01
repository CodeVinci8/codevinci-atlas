"""Минимальный recovery journal Runner (Master Spec §13.1, §7.5).

Журнал — это append-only JSONL: старт, завершение и обрыв каждого job. При
рестарте Runner все job без записи о завершении помечаются ``INTERRUPTED``,
чтобы Core восстановил их из checkpoint, а не создал второго writer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from atlas_core.ids import utcnow_iso
from atlas_core.redaction import redact


class RecoveryJournal:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, record: dict) -> None:
        record["at"] = utcnow_iso()
        line = json.dumps(record, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def started(self, request_id: str, argv0: str, pid: int) -> None:
        self._append({"event": "started", "request_id": request_id, "argv0": redact(argv0), "pid": pid})

    def finished(self, request_id: str, exit_code: int, state: str) -> None:
        self._append({"event": "finished", "request_id": request_id, "exit_code": exit_code, "state": state})

    def interrupted(self, request_id: str, reason: str) -> None:
        self._append({"event": "interrupted", "request_id": request_id, "reason": redact(reason)})

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def unfinished_jobs(self) -> list[str]:
        """request_id, для которых есть started без finished (обрыв)."""

        started: dict[str, dict] = {}
        finished: set[str] = set()
        for rec in self.read_all():
            rid = rec.get("request_id")
            if rec["event"] == "started":
                started[rid] = rec
            elif rec["event"] in ("finished", "interrupted"):
                # обе записи терминальные: job больше не «висит» (recovery идемпотентен)
                finished.add(rid)
        return [rid for rid in started if rid not in finished]
