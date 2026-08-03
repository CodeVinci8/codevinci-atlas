"""GitHub adapter (Master Spec §20, §45.8).

1.0 использует **`gh` от runtime-пользователя**; Core **не копирует и не хранит**
GitHub token (остаётся в auth store `gh`). Git-контракт §20.3 обязателен:
feature branch required, RU commit/PR, проверка автора CodeVinci, direct
main/force/delete off, squash default, before/after Audit.

Два бэкенда «forge»:

* :class:`LocalForge` — изолированный локальный **bare-remote** (+ sidecar-JSON
  для PR/checks/mergeability) для детерминированных branch/commit/push/replay/
  merge/deny тестов (создание отдельного GitHub-репо не авторизовано, §20.4);
* :class:`GhForge` — реальный `gh` для фактической VP-7-ветки и PR Atlas
  (auth/metadata/PR create-read/current-head checks/mergeability/squash merge).

Идемпотентность: повтор ``open_pr`` для того же (base, head-branch) возвращает
тот же открытый PR, а не создаёт дубль. Стабильные reason-коды для отказов.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import audit
from .redaction import redact

_GIT_TIMEOUT = 60
_GH_TIMEOUT = 90

# Ветки, в которые прямой push/commit запрещён (§20.3).
PROTECTED_BRANCHES = frozenset({"main", "master"})


class GitContractError(Exception):
    """Нарушение git-контракта. ``code`` — стабильный reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _git_env() -> dict:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }


def git(cwd: str, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                       timeout=_GIT_TIMEOUT, env=_git_env(), check=False)
    if check and r.returncode != 0:
        raise GitContractError("GIT_ERROR", redact((r.stderr or r.stdout).strip())[:200])
    return r


def is_feature_branch(branch: str, *, prefix: str = "atlas/") -> bool:
    b = (branch or "").strip()
    if not b or b in PROTECTED_BRANCHES:
        return False
    return b.startswith(prefix) if prefix else True


def configured_identity(cwd: str) -> tuple[str, str]:
    name = git(cwd, "config", "user.name").stdout.strip()
    email = git(cwd, "config", "user.email").stdout.strip()
    return name, email


# --- Git-контракт (энфорсится независимо от forge) --------------------------
@dataclass
class GitContract:
    expected_author_name: str = "CodeVinci"
    feature_prefix: str = "atlas/"

    def assert_feature_branch(self, branch: str) -> None:
        if not is_feature_branch(branch, prefix=self.feature_prefix):
            raise GitContractError(
                "FEATURE_BRANCH_REQUIRED",
                f"нужна feature-ветка ({self.feature_prefix}…), а не '{branch}'")

    def assert_author(self, cwd: str) -> tuple[str, str]:
        name, email = configured_identity(cwd)
        if name != self.expected_author_name:
            raise GitContractError(
                "AUTHOR_MISMATCH",
                f"автор '{name}' != ожидаемый '{self.expected_author_name}'")
        if not email:
            raise GitContractError("AUTHOR_MISMATCH", "email автора не настроен")
        return name, email

    def assert_russian(self, text: str) -> None:
        """Коммиты/PR — на русском (§9, §20.3). Проверяем наличие кириллицы."""
        if not text or not any("Ѐ" <= ch <= "ӿ" for ch in text):
            raise GitContractError("NON_RUSSIAN_TEXT",
                                   "текст коммита/PR должен быть на русском")

    def commit(self, cwd: str, message: str, *, allow_empty: bool = False) -> str:
        """Коммит в текущей feature-ветке под настроенной идентичностью
        (без ``--author``). Отказ на protected-ветке и на пустом/не-RU сообщении."""
        branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assert_feature_branch(branch)
        self.assert_author(cwd)
        self.assert_russian(message)
        git(cwd, "add", "-A", check=True)
        args = ["commit", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        r = git(cwd, *args)
        if r.returncode != 0:
            raise GitContractError("COMMIT_FAILED", redact((r.stderr or r.stdout))[:200])
        return git(cwd, "rev-parse", "HEAD").stdout.strip()

    def reject_force(self) -> GitContractError:
        return GitContractError("FORCE_PUSH_FORBIDDEN", "force push запрещён (§20.3)")

    def reject_delete(self) -> GitContractError:
        return GitContractError("DELETE_FORBIDDEN", "удаление веток/репозитория запрещено (§20.3)")

    def reject_direct_main(self) -> GitContractError:
        return GitContractError("DIRECT_MAIN_FORBIDDEN", "прямой push в main запрещён (§20.3)")


# --- Forge-абстракция -------------------------------------------------------
@dataclass
class PullRequest:
    number: int
    base: str
    head_branch: str
    head_sha: str
    title: str
    body: str
    state: str = "OPEN"  # OPEN|MERGED|CLOSED
    url: str = ""
    merged_sha: str = ""

    def to_dict(self) -> dict:
        return {"number": self.number, "base": self.base, "head_branch": self.head_branch,
                "head_sha": self.head_sha, "title": self.title, "state": self.state,
                "url": self.url, "merged_sha": self.merged_sha}

    def to_record(self) -> dict:
        """Полная запись для sidecar (включая body)."""
        return {**self.to_dict(), "body": self.body}


def _pr_from_record(p: dict) -> PullRequest:
    return PullRequest(**{k: p.get(k, "") if k != "number" else p["number"]
                          for k in PullRequest.__dataclass_fields__})


class LocalForge:
    """Детерминированный локальный forge поверх bare-remote (§32.3). PR/checks/
    mergeability — в sidecar-JSON рядом с bare-репо. Полностью offline."""

    def __init__(self, bare_path: str, repo_name: str = "synthetic/local"):
        self.bare = str(bare_path)
        self.repo_name = repo_name
        self.sidecar = Path(self.bare) / "atlas-forge.json"

    # sidecar-состояние
    def _load(self) -> dict:
        if not self.sidecar.exists():
            return {"pulls": [], "checks": {}, "next_number": 1}
        return json.loads(self.sidecar.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        tmp = self.sidecar.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.sidecar)

    def auth_status(self) -> dict:
        return {"authenticated": True, "backend": "local", "login": "local-forge"}

    def repo_metadata(self) -> dict:
        default = git(self.bare, "symbolic-ref", "--short", "HEAD").stdout.strip() or "main"
        return {"repo": self.repo_name, "default_branch": default, "backend": "local"}

    def branch_head(self, branch: str) -> str:
        return git(self.bare, "rev-parse", f"refs/heads/{branch}").stdout.strip()

    def branch_exists(self, branch: str) -> bool:
        return git(self.bare, "rev-parse", "--verify", f"refs/heads/{branch}").returncode == 0

    def open_pr(self, *, base: str, head_branch: str, head_sha: str, title: str,
                body: str) -> PullRequest:
        data = self._load()
        # Идемпотентность: существующий OPEN PR для (base, head_branch).
        for p in data["pulls"]:
            if p["base"] == base and p["head_branch"] == head_branch and p["state"] == "OPEN":
                p["head_sha"] = head_sha  # обновить head, но не создавать дубль
                self._save(data)
                return _pr_from_record(p)
        num = data["next_number"]
        data["next_number"] = num + 1
        pr = PullRequest(number=num, base=base, head_branch=head_branch, head_sha=head_sha,
                         title=title, body=body, state="OPEN",
                         url=f"local://{self.repo_name}/pull/{num}")
        data["pulls"].append(pr.to_record())
        self._save(data)
        return pr

    def get_pr(self, number: int) -> PullRequest | None:
        data = self._load()
        for p in data["pulls"]:
            if p["number"] == number:
                return _pr_from_record(p)
        return None

    def set_checks(self, head_sha: str, state: str) -> None:
        data = self._load()
        data["checks"][head_sha] = state
        self._save(data)

    def checks(self, head_sha: str) -> dict:
        data = self._load()
        state = data["checks"].get(head_sha, "UNKNOWN")
        return {"head_sha": head_sha, "state": state}

    def mergeability(self, number: int) -> dict:
        pr = self.get_pr(number)
        if pr is None:
            return {"mergeable": False, "state": "UNKNOWN"}
        return {"mergeable": pr.state == "OPEN", "state": "CLEAN" if pr.state == "OPEN" else pr.state}

    def squash_merge(self, number: int, *, expected_head: str, message: str,
                     author_name: str, author_email: str) -> dict:
        """Реальный squash-merge feature-ветки в base внутри bare-remote через
        временный рабочий клон. Проверяет, что head PR совпадает с ожидаемым
        (защита от протухшего SHA)."""
        pr = self.get_pr(number)
        if pr is None:
            raise GitContractError("PR_NOT_FOUND", f"PR #{number} не найден")
        if pr.state != "OPEN":
            raise GitContractError("PR_NOT_OPEN", f"PR #{number} в состоянии {pr.state}")
        actual_head = self.branch_head(pr.head_branch)
        if actual_head != expected_head or pr.head_sha != expected_head:
            raise GitContractError("STALE_HEAD",
                                   f"head изменился: ожидался {expected_head[:12]}")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="atlas-merge-") as tmp:
            wc = str(Path(tmp) / "wc")
            git(str(Path(self.bare).parent), "clone", "--quiet", self.bare, wc, check=True)
            git(wc, "config", "user.name", author_name, check=True)
            git(wc, "config", "user.email", author_email, check=True)
            git(wc, "checkout", pr.base, check=True)
            git(wc, "merge", "--squash", f"origin/{pr.head_branch}", check=True)
            r = git(wc, "commit", "-m", message)
            if r.returncode != 0:
                raise GitContractError("MERGE_FAILED", redact((r.stderr or r.stdout))[:200])
            merged_sha = git(wc, "rev-parse", "HEAD").stdout.strip()
            git(wc, "push", "origin", pr.base, check=True)
        data = self._load()
        for p in data["pulls"]:
            if p["number"] == number:
                p["state"] = "MERGED"
                p["merged_sha"] = merged_sha
        self._save(data)
        return {"merged": True, "merged_sha": merged_sha, "number": number}


class GhForge:
    """Реальный forge через `gh` runtime-пользователя. Token не читается/не
    хранится Core. Все команды — read-only статус/JSON, кроме явных create/merge."""

    def __init__(self, repo: str):
        self.repo = repo  # owner/name

    def _gh(self, *args: str, timeout: int = _GH_TIMEOUT) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *args], capture_output=True, text=True,
                              timeout=timeout, env={**os.environ, "GH_PROMPT_DISABLED": "1"},
                              check=False)

    def auth_status(self) -> dict:
        r = self._gh("auth", "status")
        # Вывод не логируем целиком (redaction на границе). Только факт входа.
        authed = "Logged in" in (r.stdout + r.stderr) and r.returncode == 0
        # login можно взять из `gh api user` без токена в выводе
        login = ""
        ru = self._gh("api", "user", "--jq", ".login")
        if ru.returncode == 0:
            login = ru.stdout.strip()
        return {"authenticated": authed, "backend": "gh", "login": login,
                "detail": "gh: авторизован" if authed else "gh: не авторизован"}

    def repo_metadata(self) -> dict:
        r = self._gh("repo", "view", self.repo, "--json",
                     "defaultBranchRef,nameWithOwner,visibility")
        if r.returncode != 0:
            return {"repo": self.repo, "error": redact(r.stderr)[:120], "backend": "gh"}
        d = json.loads(r.stdout)
        return {"repo": d.get("nameWithOwner", self.repo),
                "default_branch": (d.get("defaultBranchRef") or {}).get("name", "main"),
                "visibility": d.get("visibility"), "backend": "gh"}

    def open_pr(self, *, base: str, head_branch: str, head_sha: str, title: str,
                body: str) -> PullRequest:
        # Идемпотентность: существующий открытый PR для head-ветки.
        existing = self._gh("pr", "list", "--repo", self.repo, "--head", head_branch,
                            "--base", base, "--state", "open", "--json",
                            "number,url,headRefOid,title,state")
        if existing.returncode == 0 and existing.stdout.strip():
            arr = json.loads(existing.stdout)
            if arr:
                p = arr[0]
                return PullRequest(number=p["number"], base=base, head_branch=head_branch,
                                   head_sha=p.get("headRefOid", head_sha), title=p.get("title", title),
                                   body=body, state=p.get("state", "OPEN").upper(), url=p["url"])
        r = self._gh("pr", "create", "--repo", self.repo, "--base", base, "--head",
                     head_branch, "--title", title, "--body", body)
        if r.returncode != 0:
            raise GitContractError("PR_CREATE_FAILED", redact(r.stderr)[:200])
        url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        num = int(url.rstrip("/").split("/")[-1]) if url and url.rstrip("/").split("/")[-1].isdigit() else 0
        return PullRequest(number=num, base=base, head_branch=head_branch, head_sha=head_sha,
                           title=title, body=body, state="OPEN", url=url)

    def get_pr(self, number: int) -> PullRequest | None:
        r = self._gh("pr", "view", str(number), "--repo", self.repo, "--json",
                     "number,baseRefName,headRefName,headRefOid,title,state,url,mergeable")
        if r.returncode != 0:
            return None
        d = json.loads(r.stdout)
        return PullRequest(number=d["number"], base=d["baseRefName"], head_branch=d["headRefName"],
                           head_sha=d["headRefOid"], title=d["title"], state=d["state"].upper(),
                           url=d["url"])

    def checks(self, head_sha: str) -> dict:
        # checks для конкретного SHA через commit status/checks API.
        r = self._gh("api", f"repos/{self.repo}/commits/{head_sha}/check-runs",
                     "--jq", "[.check_runs[] | {name, status, conclusion}]")
        if r.returncode != 0:
            return {"head_sha": head_sha, "state": "UNKNOWN", "detail": redact(r.stderr)[:120]}
        runs = json.loads(r.stdout or "[]")
        if not runs:
            return {"head_sha": head_sha, "state": "PENDING", "runs": 0}
        if any(x.get("status") != "completed" for x in runs):
            return {"head_sha": head_sha, "state": "PENDING", "runs": len(runs)}
        ok = all(x.get("conclusion") in ("success", "neutral", "skipped") for x in runs)
        return {"head_sha": head_sha, "state": "GREEN" if ok else "FAILING", "runs": len(runs)}

    def mergeability(self, number: int) -> dict:
        r = self._gh("pr", "view", str(number), "--repo", self.repo, "--json",
                     "mergeable,mergeStateStatus,state")
        if r.returncode != 0:
            return {"mergeable": False, "state": "UNKNOWN"}
        d = json.loads(r.stdout)
        return {"mergeable": d.get("mergeable") == "MERGEABLE" and d.get("state") == "OPEN",
                "state": d.get("mergeStateStatus", "")}

    def squash_merge(self, number: int, *, expected_head: str, **_kw) -> dict:
        pr = self.get_pr(number)
        if pr is None:
            raise GitContractError("PR_NOT_FOUND", f"PR #{number} не найден")
        if pr.head_sha != expected_head:
            raise GitContractError("STALE_HEAD", f"head изменился: ожидался {expected_head[:12]}")
        r = self._gh("pr", "merge", str(number), "--repo", self.repo, "--squash")
        if r.returncode != 0:
            raise GitContractError("MERGE_FAILED", redact(r.stderr)[:200])
        return {"merged": True, "number": number}


# --- Высокоуровневый адаптер ------------------------------------------------
@dataclass
class GitHubAdapter:
    """Единый адаптер: git-плоскость + forge (local|gh) + git-контракт §20.3.

    ``enforce_grant`` (по умолчанию **True**): каждая production write/merge-
    операция ОБЯЗАНА иметь валидный grant (capability+scope+budget). Отсутствие
    grant → fail-closed ``GRANT_REQUIRED`` (VP-7 D-fix, bypass A). Явный
    ``enforce_grant=False`` — **только** для детерминированных LocalForge-тестов,
    которые не проверяют grant-энфорсмент (видимая test-only граница, не
    production-default обход)."""

    forge: LocalForge | GhForge
    contract: GitContract = field(default_factory=GitContract)
    enforce_grant: bool = True
    project_id: str = ""   # доверенный persisted project, к которому привязан адаптер

    def __post_init__(self) -> None:
        # Bypass A (fail-closed на этапе конструирования): реальный GhForge
        # (production write-путь) НИКОГДА не работает с отключённым grant-
        # энфорсментом. ``enforce_grant=False`` допустим ТОЛЬКО для
        # детерминированных LocalForge-тестов — с GhForge это запрещено, чтобы
        # test-only границу нельзя было переключить в production.
        if not self.enforce_grant and isinstance(self.forge, GhForge):
            raise GitContractError(
                "GRANT_ENFORCEMENT_REQUIRED",
                "enforce_grant=False недопустим с реальным GhForge (production write-путь)")

    def auth_status(self) -> dict:
        return self.forge.auth_status()

    def repo_metadata(self) -> dict:
        return self.forge.repo_metadata()

    def _forge_repo(self) -> str | None:
        """Имя удалённого repo (owner/name) активного forge — для проверки
        точного repo-scope grant на write/merge-операциях."""
        f = self.forge
        if isinstance(f, GhForge):
            return f.repo
        if isinstance(f, LocalForge):
            return f.repo_name
        return None

    def _consume(self, grant_id: str, capability: str, correlation_id: str = "",
                 *, repo: str | None = None, base: str | None = None) -> None:
        """Проверить grant (**авторитетный project** + capability + **точный scope**
        repo/base) и списать invocation-бюджет за реальную write/merge-операцию
        (VP-7 D-fix + Fix2). Fail-closed: отсутствующий/исчерпанный/несоответствующий
        grant → GitContractError. project_id адаптера передаётся в
        :func:`autonomy.evaluate`, поэтому grant чужого/непривязанного проекта
        отвергается (PROJECT_MISMATCH/PROJECT_UNBOUND), а не только по repo/капабилити."""
        if not grant_id:
            if self.enforce_grant:
                raise GitContractError(
                    "GRANT_REQUIRED",
                    f"операция {capability} требует явный grant (capability+scope+budget)")
            return  # только явная test-only граница (enforce_grant=False)
        from . import autonomy
        dec = autonomy.evaluate(capability, grant_id=grant_id,
                                project_id=(self.project_id or None), repo=repo, base=base)
        if not dec.permitted:
            raise GitContractError(dec.reason_code, dec.next_action)
        try:
            autonomy.consume_budget(grant_id, n=1, correlation_id=correlation_id)
        except autonomy.BudgetError as exc:
            raise GitContractError("BUDGET_EXHAUSTED", str(exc)) from exc

    def commit(self, worktree: str, message: str, *, allow_empty: bool = False,
               grant_id: str = "", correlation_id: str = "") -> str:
        audit.record("github.commit.before", f"wt={Path(worktree).name}",
                     correlation_id=correlation_id)
        self._consume(grant_id, "commit", correlation_id)
        sha = self.contract.commit(worktree, message, allow_empty=allow_empty)
        audit.record("github.commit.after", f"sha={sha[:12]}", correlation_id=correlation_id)
        return sha

    def push_feature(self, worktree: str, branch: str, *, remote: str = "origin",
                     force: bool = False, grant_id: str = "", correlation_id: str = "") -> dict:
        """Push feature-ветки. Отказ на protected-ветке/force. Идемпотентно:
        повторный push того же SHA — success no-op. С grant_id — списывает бюджет."""
        self.contract.assert_feature_branch(branch)
        if force:
            raise self.contract.reject_force()
        self._consume(grant_id, "push_feature", correlation_id, repo=self._forge_repo())
        local_sha = git(worktree, "rev-parse", "HEAD").stdout.strip()
        audit.record("github.push.before", f"branch={branch} sha={local_sha[:12]}",
                     correlation_id=correlation_id)
        r = git(worktree, "push", remote, f"HEAD:refs/heads/{branch}")
        if r.returncode != 0:
            raise GitContractError("PUSH_FAILED", redact((r.stderr or r.stdout))[:200])
        idempotent = "Everything up-to-date" in (r.stderr + r.stdout)
        audit.record("github.push.after", f"branch={branch} idempotent={idempotent}",
                     correlation_id=correlation_id)
        return {"pushed_sha": local_sha, "branch": branch, "idempotent": idempotent}

    def delete_branch(self, *_a, **_k):
        raise self.contract.reject_delete()

    def create_pr(self, *, base: str, head_branch: str, head_sha: str, title: str,
                  body: str, grant_id: str = "", correlation_id: str = "") -> dict:
        self.contract.assert_feature_branch(head_branch)
        self.contract.assert_russian(title)
        audit.record("github.pr.before", f"head={head_branch} base={base}",
                     correlation_id=correlation_id)
        self._consume(grant_id, "create_pr", correlation_id, repo=self._forge_repo(), base=base)
        pr = self.forge.open_pr(base=base, head_branch=head_branch, head_sha=head_sha,
                                title=title, body=body)
        audit.record("github.pr.after", f"pr=#{pr.number} state={pr.state}",
                     correlation_id=correlation_id)
        return pr.to_dict()

    def get_pr(self, number: int) -> dict | None:
        pr = self.forge.get_pr(number)
        return pr.to_dict() if pr else None

    def checks(self, head_sha: str) -> dict:
        return self.forge.checks(head_sha)

    def mergeability(self, number: int) -> dict:
        return self.forge.mergeability(number)

    def merge_pull_request(self, *, project_id: str, review_package_id: str,
                           quality_report_id: str, pr_number: int, expected_head: str,
                           grant_id: str, base: str = "", environment: str = "",
                           message: str = "", correlation_id: str = "") -> dict:
        """**Единственный** production-путь squash-merge (Fix1, §20.2). Исполнение
        неотделимо от авторитетной проверки: RP/QR грузятся из хранилища, а CI/head/
        mergeability берутся из forge (не из caller-словаря). Сырого
        ``squash_merge(grant_id, expected_head)`` больше нет — обойти gate нельзя.

        Порядок: authorize_merge_execution (RP/QR/PASS/head/CI/mergeable) → RU-message
        + grant consume (project+repo+base scope) → **TOCTOU**: перечитать live head
        прямо перед merge → raw forge squash. Любой сбой условия → GitContractError,
        merge НЕ исполняется."""
        from .merge_gate import authorize_merge_execution
        eff_project = project_id or self.project_id
        auth = authorize_merge_execution(
            forge=self.forge, repo=self._forge_repo() or "", project_id=eff_project,
            review_package_id=review_package_id, quality_report_id=quality_report_id,
            pr_number=pr_number, expected_head=expected_head, grant_id=grant_id,
            base=base, environment=environment, correlation_id=correlation_id)
        if not auth.permitted:
            audit.record("github.merge.denied",
                         f"pr=#{pr_number} code={auth.reason_code} head={expected_head[:12]}",
                         correlation_id=correlation_id)
            raise GitContractError(auth.reason_code, auth.next_action)

        msg = message or f"VP-7: squash-merge PR #{pr_number} после авторитетного PASS"
        self.contract.assert_russian(msg)
        # Списание бюджета через привязанный проект (Fix2) + точный repo/base scope.
        pr = self.forge.get_pr(pr_number)
        self._consume(grant_id, "merge_after_pass", correlation_id,
                      repo=self._forge_repo(), base=(base or (pr.base if pr else None)))

        name, email = self.contract.expected_author_name, ""
        if isinstance(self.forge, LocalForge):
            try:  # автор squash-коммита в bare-remote — общая идентичность репо-хоста
                name, email = configured_identity(str(Path(self.forge.bare).parent))
            except Exception:  # noqa: BLE001
                pass
        # TOCTOU: перечитать live head непосредственно перед исполнением.
        fresh = self.forge.get_pr(pr_number)
        if fresh is None or fresh.state != "OPEN" or fresh.head_sha != expected_head:
            raise GitContractError("STALE_HEAD_AT_MERGE",
                                   f"head изменился перед merge (ожидался {expected_head[:12]})")
        audit.record("github.merge.before", f"pr=#{pr_number} head={expected_head[:12]}",
                     correlation_id=correlation_id)
        res = self.forge.squash_merge(pr_number, expected_head=expected_head, message=msg,
                                      author_name=name or "CodeVinci", author_email=email or "")
        audit.record("github.merge.after", f"pr=#{pr_number} merged={res.get('merged')}",
                     correlation_id=correlation_id)
        return {**res, "authorization": auth.reason_code}
