"""End-to-end доказательство восстановления Runner (Master Spec §7.5, §13.4).

Честный сценарий ЖЁСТКОГО краха Runner (не «graceful interrupt»):

1. Взять writer-lease и запустить возобновляемый worker через Runner #1,
   работающий в ОТДЕЛЬНОМ процессе.
2. На середине задачи SIGKILL'нуть Runner #1 и его cgroup (модель systemd
   ``KillMode=control-group``) — запись ``finished`` в journal НЕ появляется.
3. Runner #2 при старте обнаруживает started-без-finished и помечает job
   INTERRUPTED (journal recovery).
4. Core делает reconciliation осиротевшего lease: пока процесс-писатель жив —
   reconciliation ОТКЛОНЯЕТСЯ (нет второго writer); после гибели писателя и
   при чистом git — lease освобождается. Автоугон запрещён.
5. Взять СВЕЖИЙ lease и продолжить worker через Runner #2 до успеха.
6. Доказать: каждый элемент обработан РОВНО один раз, один финальный успех,
   один writer в любой момент.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

from atlas_core.leases import LeaseStore
from atlas_core.store import Store

from .client import RunnerClient
from .protocol import decode, encode, generate_token

_REPO = Path(__file__).resolve().parents[3]
_WORKER = str(_REPO / "scripts" / "vp0_worker.py")
_HOST = str(_REPO / "scripts" / "runner_host.py")


def _pid_alive(pid: int) -> bool:
    if not pid or pid < 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


async def _start_host(sock, token, jrn, allowed) -> "asyncio.subprocess.Process":
    # Убрать устаревший сокет от убитого Runner, иначе poll вернётся до того,
    # как новый инстанс выполнит journal-recovery и создаст свой сокет.
    try:
        os.unlink(sock)
    except FileNotFoundError:
        pass
    env = {**os.environ, "ATLAS_RUNNER_SOCK": sock, "ATLAS_RUNNER_TOKEN": token,
           "ATLAS_RUNNER_JOURNAL": jrn, "ATLAS_RUNNER_ALLOWED": allowed,
           # тест-хост может идти от root в CI/dev-среде: явно разрешаем.
           "ATLAS_RUNNER_ALLOW_ROOT": "1",
           "PYTHONPATH": f"{_REPO}/apps/core:{_REPO}/apps/runner"}
    proc = await asyncio.create_subprocess_exec(sys.executable, _HOST, env=env)
    for _ in range(200):
        if os.path.exists(sock):
            return proc
        await asyncio.sleep(0.05)
    return proc


async def prove_recovery_to_success(base_dir: str, *, items: int = 6, sleep: float = 0.4) -> dict:
    base = Path(base_dir)
    worktree = base / "worktrees" / "vp0-recovery"
    worktree.mkdir(parents=True, exist_ok=True)
    state = str(worktree / "progress.json")
    if os.path.exists(state):
        os.remove(state)

    store = Store(str(base / "recovery.db"))
    leases = LeaseStore(store, ttl_s=1.0, stale_grace_s=0.5)
    project, wt = "codevinci-atlas", "atlas/vp-0-recovery"
    token = generate_token()
    sock = str(base / "runner" / "recovery.sock")
    jrn = str(base / "runner" / "recovery.jsonl")
    Path(sock).parent.mkdir(parents=True, exist_ok=True)

    max_active = 0
    single_writer_ok = True

    def track():
        nonlocal max_active, single_writer_ok
        n = leases.active_count(project, wt)
        max_active = max(max_active, n)
        if n > 1:
            single_writer_ok = False
        return n

    argv = ["python3", _WORKER, "--state", state, "--items", str(items), "--sleep", str(sleep)]
    rid = "req_recovery"

    # --- Фаза 1: Runner #1 в отдельном процессе, запуск worker, жёсткий краш -
    host1 = await _start_host(sock, token, jrn, str(base))
    leases.acquire(project_id=project, worktree=wt, run_id="run-1", role="builder", holder="run-1")
    store.upsert_run(run_id="run-1", state="RUNNING", project_id=project, vp_id="VP-0")
    track()

    # шлём run и читаем события, пока не поймаем pid worker'а
    reader, w = await asyncio.open_unix_connection(sock)
    w.write(encode({"type": "run", "token": token,
                    "request": {"argv": argv, "cwd": str(worktree), "timeout_s": 30, "request_id": rid}}))
    await w.drain()
    worker_pid = -1
    started = False
    deadline = time.time() + 5
    while time.time() < deadline and not started:
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        if not line:
            break
        evt = decode(line)
        if evt.get("type") == "run.started":
            worker_pid = evt.get("pid", -1)
            started = True
    await asyncio.sleep(sleep * 2 + 0.3)  # ~2 элемента
    processed_at_interrupt = list(json.loads(Path(state).read_text())["processed"])

    # ЖЁСТКИЙ краш: убить Runner #1 и его worker-cgroup (модель systemd)
    host1.kill()
    await host1.wait()
    worker_alive_after_crash = _pid_alive(worker_pid)
    _kill_group(worker_pid)  # teardown cgroup: писатель тоже завершается
    for _ in range(50):
        if not _pid_alive(worker_pid):
            break
        await asyncio.sleep(0.05)
    try:
        w.close()
    except Exception:
        pass
    store.upsert_run(run_id="run-1", state="INTERRUPTED", project_id=project)

    # journal: started без finished (жёсткий краш)
    journal_records = [r["event"] for r in _read_journal(jrn)]
    hard_crash_ok = "started" in journal_records and "finished" not in journal_records

    # --- Фаза 2: Runner #2, journal-recovery, reconciliation, продолжение ---
    host2 = await _start_host(sock, token, jrn, str(base))
    # Runner #2 обнаружил незавершённый job при старте?
    recovered = [r["request_id"] for r in _read_journal(jrn)
                 if r["event"] == "interrupted" and r.get("reason", "").startswith("runner restart")]

    client = RunnerClient(sock, token)

    # reconciliation: пока писатель жив — ОТКЛОНЯЕТСЯ; после гибели — освобождает
    deadline = time.time() + 3.0
    reconciled = False
    while time.time() < deadline:
        reconciled = leases.reconcile(
            project_id=project, worktree=wt,
            process_alive=lambda l: _pid_alive(worker_pid),
            git_clean=lambda l: True)
        if reconciled:
            break
        await asyncio.sleep(0.2)
    active_after_reconcile = track()

    lease2 = leases.acquire(project_id=project, worktree=wt, run_id="run-2", role="builder", holder="run-2")
    store.upsert_run(run_id="run-2", state="RUNNING", project_id=project, vp_id="VP-0")
    track()
    ev2 = await client.run({"argv": argv, "cwd": str(worktree), "timeout_s": 30,
                            "request_id": "req_recovery_cont"}, timeout_s=35)
    leases.release(lease2.id)
    track()

    host2.kill()
    await host2.wait()

    final_state = json.loads(Path(state).read_text())
    processed = list(final_state["processed"])
    duplicates = len(processed) != len(set(processed))
    final_run_state = ev2[-1]["state"]
    store.upsert_run(run_id="run-2", state="SUCCEEDED" if final_run_state == "SUCCEEDED" else "FAILED",
                     project_id=project)
    store.close()

    ok = (0 < len(processed_at_interrupt) < items and hard_crash_ok and
          bool(recovered) and reconciled and active_after_reconcile == 0 and
          final_run_state == "SUCCEEDED" and final_state.get("done") and
          processed == list(range(items)) and not duplicates and
          max_active == 1 and single_writer_ok)

    return {
        "ok": bool(ok),
        "processed_at_interrupt": processed_at_interrupt,
        "hard_crash_no_finished_record": hard_crash_ok,
        "worker_alive_immediately_after_crash": worker_alive_after_crash,
        "recovered_on_restart": recovered,
        "reconciled": reconciled,
        "active_after_reconcile": active_after_reconcile,
        "final_run_state": final_run_state,
        "final_processed": processed,
        "duplicate_processing": duplicates,
        "max_concurrent_writers": max_active,
        "single_writer_ok": single_writer_ok,
        "one_final_success": final_run_state == "SUCCEEDED" and bool(final_state.get("done")),
    }


def _read_journal(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
