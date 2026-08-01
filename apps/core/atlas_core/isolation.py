"""Доказательство межпрофильной изоляции через реальные идентичности (§30.4).

Проверяет НАСТОЯЩУЮ границу исполнения Atlas: процесс, запущенный под
идентичностью профиля A (``atlas-cx01`` и т.п.), не может прочитать
credentials профиля B, а сервисный пользователь ``atlas`` не может прочитать
ни один профиль. Это сильнее, чем ``nobody`` против root-owned 0700.

Безопасность: используется отдельный probe-файл ``.atlas_isolation_probe`` (а
не реальный ``auth.json``); содержимое credentials никогда не читается и не
печатается. При успешном кросс-чтении фиксируется факт «leaked», но НИКОГДА
не выводится содержимое.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .profiles import Profile

PROBE_NAME = ".atlas_isolation_probe"
PROBE_MARKER = "ATLAS_ISOLATION_SURROGATE"  # суррогат, не настоящий секрет


def available() -> bool:
    """Доказательство доступно только под root и при наличии runuser."""

    return os.geteuid() == 0 and shutil.which("runuser") is not None


def _run_as(user: str, argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["runuser", "-u", user, "--", *argv],
                          capture_output=True, text=True)


def write_probe(profile: Profile) -> str | None:
    """Создать probe-файл ВНУТРИ root профиля от имени его идентичности."""

    if not profile.runtime_user:
        return None
    probe = os.path.join(profile.root_path, PROBE_NAME)
    r = _run_as(profile.runtime_user, ["sh", "-c", f"umask 077; printf '%s' '{PROBE_MARKER}' > '{probe}'"])
    if r.returncode != 0:
        return None
    return probe


def _cross_read(as_user: str, target: str) -> dict:
    """Попытка чтения target от имени as_user. Содержимое не раскрывается."""

    r = _run_as(as_user, ["cat", target])
    denied = r.returncode != 0 and "permission denied" in (r.stderr or "").lower()
    leaked = PROBE_MARKER in (r.stdout or "")
    return {
        "as_user": as_user,
        "returncode": r.returncode,
        "denied": bool(denied),
        "leaked": bool(leaked),  # True == провал изоляции
        "stderr_tail": (r.stderr or "").strip()[-60:],
    }


def prove_isolation(profiles: list[Profile], *, service_user: str = "atlas") -> dict:
    """Полная матрица кросс-чтений между идентичностями профилей.

    Возвращает {matrix, ok}. ``ok`` истинно, если все кросс-чтения DENIED и
    ничего не «leaked», а каждый профиль читает СВОЙ probe.
    """

    if not available():
        return {"available": False, "reason": "нужен root + runuser", "ok": None, "matrix": []}

    probes: dict[str, str] = {}
    for p in profiles:
        probe = write_probe(p)
        if probe:
            probes[p.alias] = probe

    matrix = []
    ok = True
    try:
        for a in profiles:
            for b in profiles:
                if a.alias == b.alias or b.alias not in probes:
                    continue
                res = _cross_read(a.runtime_user, probes[b.alias])
                res.update({"reader_profile": a.alias, "target_profile": b.alias, "kind": "cross_profile"})
                if not res["denied"] or res["leaked"]:
                    ok = False
                matrix.append(res)
            # свой probe должен читаться
            if a.alias in probes:
                own = _cross_read(a.runtime_user, probes[a.alias])
                own.update({"reader_profile": a.alias, "target_profile": a.alias, "kind": "own_read"})
                own["own_read_ok"] = own["leaked"]  # для своего чтения leaked==смогли прочитать (ожидаемо)
                if not own["leaked"]:
                    ok = False
                matrix.append(own)
        # сервисный пользователь не читает ни один профиль
        for b in profiles:
            if b.alias not in probes:
                continue
            res = _cross_read(service_user, probes[b.alias])
            res.update({"reader_profile": service_user, "target_profile": b.alias, "kind": "service_user"})
            if not res["denied"] or res["leaked"]:
                ok = False
            matrix.append(res)
    finally:
        for alias, probe in probes.items():
            try:
                os.remove(probe)
            except OSError:
                pass
    return {"available": True, "ok": ok, "service_user": service_user, "matrix": matrix}
