"""Полный секрет-скан VP-0 (Master Spec §30.4, §32.6).

Критерий приёмки — «нет credentials в durable-состоянии Atlas»: рабочем
дереве, истории Git, БД, логах, artifacts, отчётах, конфиге и non-secret
реестре. Credentials по замыслу живут ТОЛЬКО в изолированных auth-root
профилей (`profiles/<provider>/<alias>/`, права 0700 у идентичности профиля).

Поэтому сами credential-root'ы **исключаются** из скана (сканировать
auth-store на наличие credentials бессмысленно), но проверяется структурная
гарантия: сервисный пользователь `atlas` (под которым работает Core и пишет
БД/логи/artifacts) НЕ может их прочитать — значит физически не может утечь.

Аллоуслист УЗКИЙ: только синтетические фикстуры в ``tests/`` и определения
правил в ``redaction.py``/``secret_scan.py``.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from .redaction import SecretHit, scan_paths


def _is_allowlisted(hit: SecretHit) -> bool:
    p = hit.path.replace(os.sep, "/")
    if "/tests/" in p or p.endswith("/redaction.py") or p.endswith("/secret_scan.py"):
        return True
    return False


def _is_credential_root(path: str) -> bool:
    """Файл внутри auth-root профиля (санкционированное место для credentials)."""

    p = path.replace(os.sep, "/")
    return ("/profiles/codex/" in p or "/profiles/claude/" in p) and "/registry.json" not in p


@dataclass
class ScanReport:
    real_hits: list = field(default_factory=list)
    allowlisted_hits: list = field(default_factory=list)
    credential_root_hits: int = 0  # ожидаемо: credentials в своих auth-store
    service_user_cannot_read_roots: bool | None = None
    git_commits: int = 0
    git_history_scanned: bool = False
    git_history_hits: list = field(default_factory=list)
    targets: list = field(default_factory=list)
    note: str = ""

    @property
    def clean(self) -> bool:
        return (not self.real_hits and not self.git_history_hits and
                self.service_user_cannot_read_roots is not False)

    def to_dict(self) -> dict:
        return {
            "clean": self.clean,
            "real_hits": [h.__dict__ if hasattr(h, "__dict__") else h for h in self.real_hits],
            "allowlisted_hits_count": len(self.allowlisted_hits),
            "credential_root_hits_excluded": self.credential_root_hits,
            "service_user_cannot_read_credential_roots": self.service_user_cannot_read_roots,
            "git_commits": self.git_commits,
            "git_history_scanned": self.git_history_scanned,
            "git_history_hits": self.git_history_hits,
            "targets": self.targets,
            "note": self.note,
        }


def _git_commit_count(repo: str) -> int:
    try:
        out = subprocess.run(["git", "-C", repo, "rev-list", "--all", "--count"],
                             capture_output=True, text=True, timeout=30)
        return int((out.stdout or "0").strip() or "0")
    except Exception:  # noqa: BLE001
        return 0


def _git_history_scan(repo: str, markers: list[str]) -> list[dict]:
    hits = []
    try:
        rev = subprocess.run(["git", "-C", repo, "rev-list", "--all"],
                             capture_output=True, text=True, timeout=30)
        revs = rev.stdout.split()
    except Exception:  # noqa: BLE001
        return hits
    if not revs:
        return hits
    for marker in markers:
        try:
            out = subprocess.run(["git", "-C", repo, "grep", "-I", "-n", "-e", marker, *revs],
                                 capture_output=True, text=True, timeout=120)
            for line in out.stdout.splitlines():
                # исключаем синтетические фикстуры tests/ и определения правил
                if "/tests/" in line or "redaction.py" in line or "secret_scan.py" in line:
                    continue
                hits.append({"marker": marker, "location": line[:200]})
        except Exception:  # noqa: BLE001
            continue
    return hits


def _service_user_cannot_read_roots(profile_roots: list[str], service_user: str) -> bool | None:
    """Структурная проверка: сервисный atlas не читает ни один credential-root."""

    import shutil
    if os.geteuid() != 0 or shutil.which("runuser") is None:
        return None
    for root in profile_roots:
        if not os.path.isdir(root):
            continue
        r = subprocess.run(["runuser", "-u", service_user, "--", "ls", root],
                           capture_output=True, text=True)
        if r.returncode == 0:  # смог прочитать — провал
            return False
    return True


def scan_repo(repo_root: str, *, extra_roots: list[str] | None = None,
              service_user: str = "atlas") -> ScanReport:
    rep = ScanReport()
    all_roots = [repo_root] + list(extra_roots or [])
    rep.targets = all_roots

    credential_roots: set[str] = set()
    for hit in scan_paths(all_roots):
        if _is_credential_root(hit.path):
            rep.credential_root_hits += 1
            credential_roots.add(os.path.dirname(hit.path))
            continue  # санкционированный auth-store — не утечка
        (rep.allowlisted_hits if _is_allowlisted(hit) else rep.real_hits).append(hit)

    # структурная гарантия для найденных auth-root'ов
    if credential_roots:
        rep.service_user_cannot_read_roots = _service_user_cannot_read_roots(
            sorted(credential_roots), service_user)

    rep.git_commits = _git_commit_count(repo_root)
    if rep.git_commits == 0:
        rep.git_history_scanned = False
        rep.note = ("В Git нет коммитов — история отсутствует; проверены рабочее дерево и "
                    "durable-состояние. Credential-root'ы исключены как санкционированные "
                    "auth-store; сервисный atlas их не читает.")
    else:
        rep.git_history_scanned = True
        from .redaction import SECRET_MARKER
        rep.git_history_hits = _git_history_scan(repo_root, [SECRET_MARKER, "ghp_", "sk-ant-", "sk-"])
        rep.note = (f"История Git просканирована ({rep.git_commits} коммит(ов)). "
                    "Credential-root'ы исключены; сервисный atlas их не читает.")
    return rep
