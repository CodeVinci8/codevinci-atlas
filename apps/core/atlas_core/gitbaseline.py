"""Read-only Git baseline (Master Spec §35, VP-2).

Собирает редактированный baseline подключённого репозитория **только чтением**:
ветка, HEAD, санированные remotes, porcelain dirty-state, счётчики
tracked/untracked, вложенные инструкции с precedence, пакетные менеджеры,
baseline-команды, статус секрет-скана, метка времени и content-hash.

Гарантии (§35 acceptance):
- НИКОГДА не выполняет запись в репозиторий (нет ``reset``/``clean``/``checkout``);
- НИКОГДА не сохраняет credential-bearing URL, полный env, безграничный вывод
  или содержимое секретов;
- содержимое репозитория — данные, оно не расширяет права (§30.2).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .ids import utcnow_iso
from .redaction import contains_secret, redact

_GIT_TIMEOUT = 20
_MAX_PORCELAIN = 500          # верхняя граница строк dirty-state
_MAX_INSTRUCTION_FILES = 50
_MAX_INSTRUCTION_DEPTH = 4
_MAX_SUMMARY = 240            # символов на bounded-summary инструкции
_MAX_COMMANDS = 60
_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", ".mypy_cache",
              ".ruff_cache", ".pytest_cache", "target", ".idea", ".vscode"}

# Имена файлов инструкций по контрактам репозитория (§35). Nested-файлы более
# специфичны (выше precedence). README инструкцией не считается.
_INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md",
                      ".cursorrules", ".github/copilot-instructions.md")

# Детекторы пакетных менеджеров: (имя, файл-свидетельство).
_PKG_MANAGERS = (
    ("pnpm", "pnpm-lock.yaml"),
    ("yarn", "yarn.lock"),
    ("npm", "package-lock.json"),
    ("npm", "package.json"),
    ("uv", "uv.lock"),
    ("poetry", "poetry.lock"),
    ("pip", "requirements.txt"),
    ("pip", "pyproject.toml"),
    ("cargo", "Cargo.toml"),
    ("go", "go.mod"),
    ("bundler", "Gemfile"),
    ("gradle", "build.gradle"),
    ("maven", "pom.xml"),
)


class GitBaselineError(Exception):
    """Не удалось собрать baseline (не git-репозиторий и т.п.)."""


def _git_env() -> dict:
    """Минимальное окружение git: без интерактивных промптов кредов."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_TERMINAL_PROMPT": "0",       # никогда не спрашивать креды
        "GIT_OPTIONAL_LOCKS": "0",        # read-only: не брать index.lock
        "LC_ALL": "C",
    }


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        env=_git_env(), check=False,
    )


def is_git_repo(path: str) -> bool:
    try:
        r = _git(path, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def quick_state(repo_path: str) -> dict:
    """Лёгкая проверка живого состояния (для дрейфа/stale). Только чтение."""
    repo = os.path.realpath(repo_path)
    if not os.path.isdir(repo) or not is_git_repo(repo):
        return {"accessible": False, "head": "", "dirty": False}
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    r = _git(repo, "status", "--porcelain", "-uall")
    dirty = bool(r.stdout.splitlines()) if r.returncode == 0 else False
    return {"accessible": True, "head": head, "dirty": dirty}


def sanitize_remote_url(url: str) -> str:
    """Убрать credentials из URL remote (никогда не хранить их, §30.2)."""
    u = url.strip()
    # https://user:pass@host/... → https://host/...
    u = re.sub(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@]*@", r"\1", u)
    # На всякий случай — общий redact (токены/ключи в строке).
    u = redact(u)
    return u


def _remotes(repo: str) -> list[dict]:
    r = _git(repo, "remote", "-v")
    if r.returncode != 0:
        return []
    seen: dict[str, str] = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            name, url = parts[0], parts[1]
            seen.setdefault(name, sanitize_remote_url(url))
    return [{"name": n, "url": u} for n, u in sorted(seen.items())]


def _porcelain(repo: str) -> tuple[bool, list[dict], int, int]:
    """(dirty, bounded_entries, tracked_changes, untracked)."""
    r = _git(repo, "status", "--porcelain", "-uall")
    lines = r.stdout.splitlines() if r.returncode == 0 else []
    dirty = bool(lines)
    entries: list[dict] = []
    tracked_changes = 0
    untracked = 0
    for line in lines:
        code = line[:2]
        path = line[3:]
        if code == "??":
            untracked += 1
        else:
            tracked_changes += 1
        if len(entries) < _MAX_PORCELAIN:
            entries.append({"code": code.strip() or code, "path": redact(path)})
    return dirty, entries, tracked_changes, untracked


def _tracked_count(repo: str) -> int:
    r = _git(repo, "ls-files")
    if r.returncode != 0:
        return 0
    return sum(1 for _ in r.stdout.splitlines())


def _iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth > _MAX_INSTRUCTION_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            yield dirpath, name, depth


def discover_instructions(repo: str) -> list[dict]:
    """Найти вложенные файлы инструкций с путём, scope и precedence (§35)."""
    root = os.path.realpath(repo)
    found: list[dict] = []
    # Явные вложенные пути (.github/copilot-instructions.md) обрабатываем отдельно.
    nested_special = {p for p in _INSTRUCTION_NAMES if "/" in p}
    flat_names = {p for p in _INSTRUCTION_NAMES if "/" not in p}
    for dirpath, name, depth in _iter_files(root):
        rel = os.path.relpath(os.path.join(dirpath, name), root)
        rel_posix = rel.replace(os.sep, "/")
        if name in flat_names or rel_posix in nested_special:
            found.append(_read_instruction(root, rel_posix, depth))
            if len(found) >= _MAX_INSTRUCTION_FILES:
                break
    # precedence: глубже = специфичнее (больше). root=0.
    found.sort(key=lambda x: (x["precedence"], x["path"]))
    return found


def _read_instruction(root: str, rel_posix: str, depth: int) -> dict:
    scope = os.path.dirname(rel_posix) or "."
    full = os.path.join(root, rel_posix)
    read_ok = True
    summary = ""
    size = 0
    try:
        text = Path(full).read_text(encoding="utf-8", errors="strict")
        size = len(text.encode("utf-8"))
        one_line = " ".join(text.split())
        summary = redact(one_line)[:_MAX_SUMMARY]
    except (OSError, UnicodeDecodeError):
        read_ok = False
    return {"path": rel_posix, "scope": scope, "precedence": depth,
            "read_ok": read_ok, "bytes": size, "summary": summary}


def detect_package_managers(repo: str) -> list[dict]:
    root = os.path.realpath(repo)
    out: list[dict] = []
    seen = set()
    for name, ev in _PKG_MANAGERS:
        if (root_has := os.path.exists(os.path.join(root, ev))) and (name, ev) not in seen:
            seen.add((name, ev))
            out.append({"name": name, "evidence": ev})
        _ = root_has
    return out


def discover_commands(repo: str) -> list[dict]:
    """Явные bounded baseline-команды (только для показа, НЕ исполняются, §35).

    Извлекаются из package.json[scripts], pyproject/pytest, Makefile-целей.
    Строки не передаются в shell — это метаданные для владельца.
    """
    root = os.path.realpath(repo)
    cmds: list[dict] = []

    pkg = os.path.join(root, "package.json")
    if os.path.exists(pkg):
        try:
            data = json.loads(Path(pkg).read_text(encoding="utf-8"))
            for name, cmd in (data.get("scripts") or {}).items():
                cmds.append({"source": "package.json:scripts", "name": str(name),
                             "command": redact(str(cmd))[:200], "executed": False})
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    pyproject = os.path.join(root, "pyproject.toml")
    if os.path.exists(pyproject):
        try:
            txt = Path(pyproject).read_text(encoding="utf-8")
            if "[tool.pytest" in txt or "pytest" in txt:
                cmds.append({"source": "pyproject.toml", "name": "test",
                             "command": "pytest", "executed": False})
            if "[tool.ruff" in txt:
                cmds.append({"source": "pyproject.toml", "name": "lint",
                             "command": "ruff check .", "executed": False})
        except OSError:
            pass

    makefile = os.path.join(root, "Makefile")
    if os.path.exists(makefile):
        try:
            for line in Path(makefile).read_text(encoding="utf-8").splitlines():
                m = re.match(r"^([a-zA-Z0-9][a-zA-Z0-9_.\-]*):(?!=)", line)
                if m:
                    cmds.append({"source": "Makefile", "name": m.group(1),
                                 "command": f"make {m.group(1)}", "executed": False})
        except OSError:
            pass

    return cmds[:_MAX_COMMANDS]


def _bounded_secret_scan(repo: str, instructions: list[dict]) -> dict:
    """Ограниченный секрет-скан ключевых файлов (редактированный статус, §30)."""
    root = os.path.realpath(repo)
    targets = [os.path.join(root, i["path"]) for i in instructions]
    for ev in ("package.json", "pyproject.toml", "requirements.txt", ".env.example"):
        p = os.path.join(root, ev)
        if os.path.exists(p):
            targets.append(p)
    scanned = 0
    clean = True
    for t in targets[:100]:
        try:
            text = Path(t).read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        if contains_secret(text):
            clean = False
    return {"scanned_files": scanned, "clean": clean}


def collect_baseline(repo_path: str) -> dict:
    """Собрать редактированный read-only baseline. Не пишет в репозиторий."""
    repo = os.path.realpath(repo_path)
    if not is_git_repo(repo):
        raise GitBaselineError(f"не git-репозиторий: {redact(repo_path)}")

    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    remotes = _remotes(repo)
    dirty, porcelain, tracked_changes, untracked = _porcelain(repo)
    tracked_total = _tracked_count(repo)
    instructions = discover_instructions(repo)
    package_managers = detect_package_managers(repo)
    commands = discover_commands(repo)
    secret_scan = _bounded_secret_scan(repo, instructions)

    baseline = {
        "canonical_path": repo,
        "branch": branch,
        "head": head,
        "remotes": remotes,
        "dirty": dirty,
        "porcelain": porcelain,
        "porcelain_truncated": len(porcelain) >= _MAX_PORCELAIN,
        "tracked_total": tracked_total,
        "tracked_changes": tracked_changes,
        "untracked": untracked,
        "instructions": instructions,
        "package_managers": package_managers,
        "baseline_commands": commands,
        "secret_scan": secret_scan,
        "observed_at": utcnow_iso(),
    }
    baseline["content_hash"] = _hash_baseline(baseline)
    return baseline


def _hash_baseline(baseline: dict) -> str:
    """Стабильный SHA-256 по существенным полям (без времени)."""
    material = {k: baseline[k] for k in (
        "canonical_path", "branch", "head", "remotes", "dirty",
        "tracked_total", "tracked_changes", "untracked",
        "package_managers", "baseline_commands")}
    material["instructions"] = [{"path": i["path"], "precedence": i["precedence"]}
                                for i in baseline["instructions"]]
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
