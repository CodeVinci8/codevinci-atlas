#!/usr/bin/env python3
"""Локальный сервер для реальной Chrome-верификации VP-7 (не трогает живой стек).

Поднимает НОВЫЙ Core (реальная миграция 0007) в изолированном ATLAS_DATA_DIR,
сидит осмысленные VP-7 фикстуры (проект, 4 профиля + auth-health READY/AUTH_EXPIRED/
STALE, гранты ACTIVE/EXPIRED/REVOKED, чекпоинты для timeline+compare, GitHub-доставка
с merge gate PERMIT и DENY) и монтирует собранный Web (`apps/web/dist`).

Опция ``ATLAS_VP7_EMERGENCY=1`` — засидить активный Emergency Stop (для скриншота
emergency-stopped состояния).

Запуск:
  ATLAS_DATA_DIR=<tmp> PYTHONPATH=apps/core:apps/runner \
    .venv/bin/python scripts/vp7_chrome_server.py <port>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps/core"))
sys.path.insert(0, str(_ROOT / "apps/runner"))


def _now():
    return datetime.now(timezone.utc)


def seed(data_dir: str) -> None:
    os.environ["ATLAS_CONFIG_FILE"] = "/nonexistent.yaml"
    os.environ["ATLAS_DATA_DIR"] = data_dir
    reg = Path(data_dir) / "profiles" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    # Текущая правда: два Codex + ОДИН активный Claude (claude-pro-01).
    # claude-pro-02 — истёкшая вторая подписка: disabled (не в активном пуле/UI/ёмкости),
    # исторические ссылки сохраняются. Новый второй Claude привяжут в VP-8.
    profs = {}
    for a, p, u, dis in (("codex-plus-01", "codex", "atlas-cx01", False),
                         ("codex-plus-02", "codex", "atlas-cx02", False),
                         ("claude-pro-01", "claude", "atlas-cl01", False),
                         ("claude-pro-02", "claude", "atlas-cl02", True)):
        profs[a] = {"alias": a, "provider": p, "root_path": f"/x/{a}",
                    "state": "DISABLED" if dis else "AUTH_REQUIRED", "runtime_user": u,
                    "disabled": dis}
    reg.write_text(json.dumps({"profiles": profs}, ensure_ascii=False), encoding="utf-8")

    # call-8 F: guard — фикстура НИКОГДА не мигрирует живую БД (только изолированный
    # ATLAS_DATA_DIR). Иначе — отказ до alembic.
    from atlas_core.migration_guard import assert_isolated
    assert_isolated(purpose="vp7-chrome-fixture")

    venv = _ROOT / ".venv" / "bin"
    subprocess.run([str(venv / "alembic"), "upgrade", "head"], cwd=str(_ROOT), check=True,
                   env={**os.environ, "PATH": f"{venv}:{os.environ.get('PATH', '')}",
                        "PYTHONPATH": f"{_ROOT}/apps/core:{_ROOT}/apps/runner"})

    from atlas_core.db import init_engine, session_scope
    from atlas_core.orm import Project
    from atlas_core.settings import load_settings
    s = load_settings()
    init_engine(s.db_url, s.db_path)
    with session_scope() as db:
        if db.get(Project, "proj_web") is None:
            db.add(Project(id="proj_web", name="CodeVinci Atlas (self)", source_kind="local_git",
                           source_location="/opt/CodeVinciAtlas", status="connected",
                           created_at=_now(), updated_at=_now()))
            db.commit()

    # профили → durable + auth-health (READY) через фейковый пробер
    from atlas_core.agent_registry import ProfileService
    from atlas_core.auth_health import run_auth_health
    from atlas_core.profile_reconcile import reconcile_profiles
    from atlas_core.profiles import ProfileRegistry
    reconcile_profiles(ProfileRegistry(str(reg)), actor="chrome-fixture")

    def prober(prof):
        # claude-pro-02 disabled (истёкшая подписка) — остаётся DISABLED (не синкается).
        m = {"codex-plus-01": {"authenticated": True, "state": "READY"},
             "codex-plus-02": {"authenticated": True, "state": "READY"},
             "claude-pro-01": {"authenticated": True, "state": "READY"},
             "claude-pro-02": {"authenticated": True, "state": "READY"}}
        return {"cli_version": "fixture 1.0", "auth": m[prof.alias]}
    run_auth_health(registry=ProfileRegistry(str(reg)), prober=prober, actor="chrome-fixture")

    # --- VP-7 capacity observations ------------------------------------------
    # Codex — числовые окна (68% / 96% LOW + STALE-fallback). Claude — ОДИН активный
    # профиль claude-pro-01 с ЧИСЛОВЫМИ окнами (source=claude-statusline: демонстрация
    # официального numeric-UI). claude-pro-02 disabled → БЕЗ ёмкости (не в активном UI).
    from atlas_core.capacity import persist_capacity
    from atlas_core.claude_pool import select_builder
    ids = {p["alias"]: p["id"] for p in ProfileService().list_profiles()}
    reset7d = (_now() + timedelta(days=4)).isoformat()
    reset5h = (_now() + timedelta(hours=2)).isoformat()

    def _num_win(wid, used, reset, mins):
        return {"id": wid, "label": {"5h": "Сессия (5 ч)", "7d": "Неделя (7 дн)"}[wid],
                "used_pct": used, "remaining_pct": round(100 - used, 1), "reset_at": reset,
                "window_mins": mins,
                "status": ("rejected" if used >= 100 else "warning" if used >= 80 else "allowed")}

    persist_capacity(ids["codex-plus-01"], {"provider": "codex", "plan": "plus", "auth_ok": True,
        "source": "codex-app-server", "error_code": "", "windows": [_num_win("7d", 68.0, reset7d, 10080)]})
    # codex-plus-02: сначала валидное 96% (LOW), затем неудачный refresh → STALE-fallback.
    persist_capacity(ids["codex-plus-02"], {"provider": "codex", "plan": "plus", "auth_ok": True,
        "source": "codex-app-server", "error_code": "", "windows": [_num_win("7d", 96.0, reset7d, 10080)]})
    persist_capacity(ids["codex-plus-02"], {"provider": "codex", "plan": "plus", "auth_ok": True,
        "source": "codex-probe", "error_code": "CODEX_APPSERVER_IO_FAILED", "windows": []})
    # claude-pro-01: официальные числовые окна из status-line (used_percentage).
    persist_capacity(ids["claude-pro-01"], {"provider": "claude", "plan": "pro", "auth_ok": True,
        "source": "claude-statusline", "error_code": "",
        "windows": [_num_win("5h", 23.5, reset5h, 300), _num_win("7d", 41.2, reset7d, 10080)]})
    # claude-pro-02 disabled → ёмкость НЕ наблюдается (исключён из активного пула).

    # Активный Claude Builder = единственный активный профиль claude-pro-01.
    from atlas_core.ids import new_id
    from atlas_core.orm import RunLease
    with session_scope() as db:
        db.add(RunLease(id=new_id("rl"), run_id="run_ok", role="builder",
                        profile_id=ids["claude-pro-01"], worktree="wt-vp7", holder="core",
                        acquired_at=_now().isoformat()))
        db.commit()
    select_builder(ProfileService().list_profiles(), actor="chrome-fixture")

    # --- VP-7 гранты: ACTIVE / EXPIRED / REVOKED ---------------------------
    from atlas_core import autonomy
    g_active = autonomy.create_grant(
        project_id="proj_web", mode="STANDARD",
        capabilities=["repo_read", "commit", "push_feature", "create_pr", "merge_after_pass"],
        environment="synthetic", allowed_repos=["CodeVinci8/codevinci-atlas"],
        allowed_bases=["main"], budget={"max_invocations": 6, "used_invocations": 2},
        reason="Закрытие VP-7 в STANDARD: bounded merge после PASS.", ttl_seconds=3600)
    autonomy.create_grant(project_id="proj_web", mode="AUTONOMOUS", capabilities=["repo_read"],
                          reason="Истёкший grant для витрины состояния.", ttl_seconds=-60)
    g_rev = autonomy.create_grant(project_id="proj_web", mode="TRUSTED",
                                  capabilities=["repo_read", "commands"],
                                  reason="Отозванный grant для витрины состояния.", ttl_seconds=3600)
    autonomy.revoke_grant(g_rev["id"], by="owner", reason="Больше не нужен")

    # --- checkpoints для timeline + compare -------------------------------
    from atlas_core.timemachine import CheckpointInputs, create_checkpoint
    base = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    create_checkpoint(CheckpointInputs(
        project_id="proj_web", vp_key="VP-7", run_id="run_ok", db_revision="0007",
        branch="atlas/vp-7-autonomy-github-time-machine", base_sha=base,
        head_sha="f6c3d0e0a9da4b063c5d3dcc63064e94c0ccc9e9", worktree_status="clean",
        patch_hash="sha256:patch1", artifact_hashes=[{"path": "calc.py", "sha": "sha256:aa"}],
        profile_alias="claude-pro-01", model="claude", effort="medium",
        session_ids=["sess-b1"], grant_hash=g_active["content_hash"],
        test_refs=[{"name": "unit", "hash": "sha256:t1"}], evidence_refs=["ev:accept-33"],
        cause="post-review-pass"))
    create_checkpoint(CheckpointInputs(
        project_id="proj_web", vp_key="VP-7", run_id="run_ok", db_revision="0007",
        branch="atlas/vp-7-autonomy-github-time-machine", base_sha=base,
        head_sha="0011223344556677889900aabbccddeeff001122", worktree_status="clean",
        patch_hash="sha256:patch2", artifact_hashes=[{"path": "calc.py", "sha": "sha256:bb"}],
        profile_alias="codex-plus-01", model="codex", effort="high",
        session_ids=["sess-c1"], grant_hash="sha256:othergrant",
        test_refs=[{"name": "unit", "hash": "sha256:t2"}], evidence_refs=["ev:replay"],
        cause="fork"))

    # --- GitHub-доставка: PERMIT + DENY ------------------------------------
    from atlas_core.ids import new_id
    from atlas_core.orm import GithubDelivery
    with session_scope() as db:
        db.add(GithubDelivery(
            id=new_id("ghd"), project_id="proj_web", repo="CodeVinci8/codevinci-atlas",
            base="main", branch="atlas/vp-7-autonomy-github-time-machine",
            head_sha="f6c3d0e0a9da4b063c5d3dcc63064e94c0ccc9e9", pr_number=13,
            pr_url="https://github.com/CodeVinci8/codevinci-atlas/pull/13", pr_state="OPEN",
            checks_state="GREEN", checks_head_sha="f6c3d0e0a9da4b063c5d3dcc63064e94c0ccc9e9",
            mergeable=True, merge_state="CLEAN", gate_decision="PERMIT", gate_reason="MERGE_PERMITTED",
            grant_id=g_active["id"], idempotency_key="CodeVinci8/codevinci-atlas|main|"
            "atlas/vp-7-autonomy-github-time-machine|f6c3d0e0a9da",
            created_at=_now(), updated_at=_now()))
        db.add(GithubDelivery(
            id=new_id("ghd"), project_id="proj_web", repo="CodeVinci8/codevinci-atlas",
            base="main", branch="atlas/vp-7-stale", head_sha="deadbeefdeadbeefdeadbeef",
            pr_number=12, pr_url="https://github.com/CodeVinci8/codevinci-atlas/pull/12",
            pr_state="OPEN", checks_state="PENDING", checks_head_sha="oldsha0000",
            mergeable=False, merge_state="BLOCKED", gate_decision="DENY",
            gate_reason="STALE_OR_FAILING_CI", grant_id=g_active["id"],
            idempotency_key="CodeVinci8/codevinci-atlas|main|atlas/vp-7-stale|deadbeefdead",
            created_at=_now() - timedelta(minutes=5), updated_at=_now()))
        db.commit()

    if os.environ.get("ATLAS_VP7_EMERGENCY") == "1":
        from atlas_core import emergency
        emergency.engage(reason="Витрина emergency-stopped состояния", actor="owner")


def build_app():
    from atlas_core.app import create_app
    from atlas_core.settings import load_settings
    from starlette.staticfiles import StaticFiles
    app = create_app(load_settings())
    dist = _ROOT / "apps/web/dist"
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
    return app


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8098
    data_dir = os.environ.get("ATLAS_DATA_DIR") or tempfile.mkdtemp(prefix="atlas-vp7-chrome-")
    if not (Path(data_dir) / "atlas.db").exists():
        seed(data_dir)
    else:
        os.environ.setdefault("ATLAS_CONFIG_FILE", "/nonexistent.yaml")
        os.environ["ATLAS_DATA_DIR"] = data_dir
    import uvicorn
    app = build_app()
    print(f"[vp7-chrome-server] data_dir={data_dir} port={port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
