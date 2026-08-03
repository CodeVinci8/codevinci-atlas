"""Autonomy и Capability Grants (Master Spec §19).

Четыре режима автономии и durable grants с **раздельными** capabilities (никогда
не «full access» boolean). Оценка **fail-closed**: возвращает стабильный
reason-код и точное next action. Активный grant разрешает действие **только**
внутри своего точного scope (repo/base/environment/workspace/capability/budget).

Жёсткие правила VP-7 (§40 Out): direct main, force push, branch/repo delete,
production deploy, DNS/Nginx/TLS, paid calls, cookie import **недоступны** через
автономию — требуют явного owner-действия вне grant. destructive rollback
доступен только по **отдельному** grant, который его явно перечисляет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import select

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import Grant
from .productmap import content_hash
from .redaction import redact

# --- Режимы автономии (ровно четыре, §19) ----------------------------------
MODES = ("GUIDED", "STANDARD", "AUTONOMOUS", "TRUSTED")


class Capability(str, Enum):
    """Раздельные capabilities (§19). Никогда не сворачиваются в один флаг."""

    REPO_READ = "repo_read"
    REPO_WRITE = "repo_write"
    COMMANDS = "commands"
    DEPS_INSTALL = "deps_install"
    COMMIT = "commit"
    PUSH_FEATURE = "push_feature"
    CREATE_PR = "create_pr"
    MERGE_AFTER_PASS = "merge_after_pass"
    DIRECT_MAIN = "direct_main"
    FORCE_PUSH = "force_push"
    BRANCH_DELETE = "branch_delete"
    REPO_DELETE = "repo_delete"
    PRODUCTION_DEPLOY = "production_deploy"
    DNS_NGINX_TLS = "dns_nginx_tls"
    PAID_CALLS = "paid_calls"
    COOKIE_IMPORT = "cookie_import"
    DESTRUCTIVE_ROLLBACK = "destructive_rollback"


ALL_CAPABILITIES: tuple[str, ...] = tuple(c.value for c in Capability)

# Недоступны через автономию в VP-7 (§40 Out) — даже если grant их перечисляет.
# destructive_rollback НЕ здесь: он доступен по отдельному явному grant.
VP7_HARD_DENIED: frozenset[str] = frozenset({
    Capability.DIRECT_MAIN.value, Capability.FORCE_PUSH.value,
    Capability.BRANCH_DELETE.value, Capability.REPO_DELETE.value,
    Capability.PRODUCTION_DEPLOY.value, Capability.DNS_NGINX_TLS.value,
    Capability.PAID_CALLS.value, Capability.COOKIE_IMPORT.value,
})

# Действия, потребляющие invocation-бюджет (write/merge). read/verify не тратят.
BUDGETED_CAPABILITIES: frozenset[str] = frozenset({
    Capability.COMMIT.value, Capability.PUSH_FEATURE.value,
    Capability.CREATE_PR.value, Capability.MERGE_AFTER_PASS.value,
    Capability.COMMANDS.value, Capability.DEPS_INSTALL.value,
})

# Capabilities, затрагивающие конкретный удалённый repo: grant ОБЯЗАН иметь явный
# scope (allowed_repos/allowed_bases непусты) — иначе fail-closed deny (VP-7 D-fix).
# «Пустой allowlist» НЕ означает «любой repo». commit — worktree-локальный, не сюда.
SCOPE_REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    Capability.REPO_WRITE.value, Capability.PUSH_FEATURE.value,
    Capability.CREATE_PR.value, Capability.MERGE_AFTER_PASS.value,
})

# --- Стабильные reason-коды (fail-closed) ----------------------------------
R_PERMITTED = "PERMITTED"
R_NO_GRANT = "NO_GRANT"
R_EXPIRED = "GRANT_EXPIRED"
R_REVOKED = "GRANT_REVOKED"
R_PROJECT_MISMATCH = "PROJECT_MISMATCH"
R_PROJECT_UNBOUND = "PROJECT_UNBOUND"
R_REPO = "REPO_NOT_ALLOWED"
R_BASE = "BASE_NOT_ALLOWED"
R_ENV = "ENVIRONMENT_MISMATCH"
R_WORKSPACE = "WORKSPACE_NOT_ALLOWED"
R_CAP_MISSING = "CAPABILITY_MISSING"
R_CAP_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
R_BUDGET = "BUDGET_EXHAUSTED"
R_CONFLICT = "VERSION_CONFLICT"
R_INACTIVE = "GRANT_INACTIVE"

_NEXT_ACTION: dict[str, str] = {
    R_PERMITTED: "Действие разрешено внутри точного scope grant.",
    R_NO_GRANT: "Создайте grant с нужным режимом и capability для этого проекта.",
    R_EXPIRED: "Grant истёк — выпустите новый grant с актуальным сроком.",
    R_REVOKED: "Grant отозван — выпустите новый grant, если действие ещё нужно.",
    R_PROJECT_MISMATCH: "Grant привязан к другому проекту — используйте grant этого проекта.",
    R_PROJECT_UNBOUND: "Grant без project_id недопустим для операции проекта — выпустите привязанный grant.",
    R_REPO: "Добавьте репозиторий в allowlist grant или выберите разрешённый.",
    R_BASE: "Добавьте базовую ветку в allowlist grant или выберите разрешённую.",
    R_ENV: "Согласуйте environment действия с environment grant.",
    R_WORKSPACE: "Добавьте workspace в allowlist grant или выберите разрешённый.",
    R_CAP_MISSING: "Добавьте требуемую capability в grant (раздельно, не «full access»).",
    R_CAP_UNAVAILABLE: "Недоступно через автономию (VP-7 §40 Out): требуется явное owner-действие.",
    R_BUDGET: "Бюджет вызовов исчерпан — увеличьте бюджет отдельным решением владельца.",
    R_CONFLICT: "Grant изменён параллельно — перечитайте актуальную версию и повторите.",
    R_INACTIVE: "Grant не активен — проверьте состояние или выпустите новый.",
}


def next_action(code: str) -> str:
    return _NEXT_ACTION.get(code, "Проверьте grant и повторите.")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Decision:
    permitted: bool
    reason_code: str
    next_action: str
    grant_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {"permitted": self.permitted, "reason_code": self.reason_code,
                "next_action": self.next_action, "grant_id": self.grant_id,
                "detail": self.detail}


def default_branch_rules() -> dict:
    """Ветвевые правила по умолчанию (§20.3): feature branch обязателен, direct
    main/force/delete off, squash default."""
    return {
        "require_feature_branch": True,
        "feature_prefix": "atlas/",
        "forbid_direct_main": True,
        "forbid_force_push": True,
        "forbid_branch_delete": True,
        "squash_default": True,
    }


def _immutable_payload(*, owner_ref, project_id, environment, mode, allowed_repos,
                       allowed_bases, workspace_allowlist, capabilities, branch_rules,
                       command_restrictions, budget, reason, starts_at, expires_at) -> dict:
    """Canonical immutable-авторизация, покрываемая content_hash (для snapshot)."""
    return {
        "owner_ref": owner_ref, "project_id": project_id, "environment": environment,
        "mode": mode, "allowed_repos": sorted(allowed_repos),
        "allowed_bases": sorted(allowed_bases),
        "workspace_allowlist": sorted(workspace_allowlist),
        "capabilities": sorted(capabilities), "branch_rules": branch_rules,
        "command_restrictions": command_restrictions,
        "budget": {"max_invocations": budget.get("max_invocations"),
                   "max_cost": budget.get("max_cost")},
        "reason": reason, "starts_at": starts_at,
        "expires_at": expires_at,
    }


def create_grant(*, project_id: str, mode: str, capabilities: list[str],
                 owner_ref: str = "owner", environment: str = "",
                 allowed_repos: list[str] | None = None,
                 allowed_bases: list[str] | None = None,
                 workspace_allowlist: list[str] | None = None,
                 branch_rules: dict | None = None,
                 command_restrictions: dict | None = None,
                 budget: dict | None = None, reason: str = "",
                 ttl_seconds: int | None = 3600, expires_at: datetime | None = None,
                 actor: str = "owner", correlation_id: str = "") -> dict:
    """Создать durable grant (§19). Валидирует режим и capabilities."""
    if mode not in MODES:
        raise ValueError(f"неизвестный режим: {mode}")
    caps = sorted(set(capabilities))
    unknown = [c for c in caps if c not in ALL_CAPABILITIES]
    if unknown:
        raise ValueError(f"неизвестные capabilities: {unknown}")
    allowed_repos = sorted(set(allowed_repos or []))
    allowed_bases = sorted(set(allowed_bases or []))
    workspace_allowlist = sorted(set(workspace_allowlist or []))
    branch_rules = branch_rules or default_branch_rules()
    command_restrictions = command_restrictions or {}
    budget = budget or {}
    now = _utcnow()
    if expires_at is None and ttl_seconds is not None:
        expires_at = now + timedelta(seconds=ttl_seconds)
    payload = _immutable_payload(
        owner_ref=owner_ref, project_id=project_id, environment=environment, mode=mode,
        allowed_repos=allowed_repos, allowed_bases=allowed_bases,
        workspace_allowlist=workspace_allowlist, capabilities=caps,
        branch_rules=branch_rules, command_restrictions=command_restrictions,
        budget=budget, reason=reason, starts_at=now.isoformat(),
        expires_at=expires_at.isoformat() if expires_at else None)
    ch = content_hash(payload)
    import json as _json
    gid = new_id("grant")
    aid = audit.record("grant.created",
                       f"grant={gid} mode={mode} caps={len(caps)} project={project_id}",
                       actor=actor, correlation_id=correlation_id)
    with session_scope() as s:
        row = Grant(
            id=gid, owner_ref=owner_ref, project_id=project_id, environment=environment,
            mode=mode, allowed_repos_json=_json.dumps(allowed_repos, ensure_ascii=False),
            allowed_bases_json=_json.dumps(allowed_bases, ensure_ascii=False),
            workspace_allowlist_json=_json.dumps(workspace_allowlist, ensure_ascii=False),
            capabilities_json=_json.dumps(caps, ensure_ascii=False),
            branch_rules_json=_json.dumps(branch_rules, ensure_ascii=False, sort_keys=True),
            command_restrictions_json=_json.dumps(command_restrictions, ensure_ascii=False,
                                                  sort_keys=True),
            budget_json=_json.dumps(budget, ensure_ascii=False, sort_keys=True),
            reason=redact(reason)[:800], starts_at=now, expires_at=expires_at,
            state="ACTIVE", actor=actor, correlation_id=correlation_id,
            audit_refs_json=_json.dumps([aid]), content_hash=ch, version=1,
            created_at=now, updated_at=now)
        s.add(row)
        s.commit()
        return row.to_dict()


def get_grant(grant_id: str) -> dict | None:
    with session_scope() as s:
        row = s.get(Grant, grant_id)
        return row.to_dict() if row else None


def list_grants(*, project_id: str | None = None, state: str | None = None,
                limit: int = 100) -> list[dict]:
    with session_scope() as s:
        stmt = select(Grant).order_by(Grant.created_at.desc(), Grant.id.desc())
        if project_id:
            stmt = stmt.where(Grant.project_id == project_id)
        if state:
            stmt = stmt.where(Grant.state == state)
        rows = s.execute(stmt.limit(max(1, min(limit, 500)))).scalars().all()
    # Ленивая пометка истёкших при чтении (не мутирует immutable-авторизацию).
    out = []
    for r in rows:
        d = r.to_dict()
        if d["state"] == "ACTIVE" and _is_expired(d):
            d["state"] = "EXPIRED"
        out.append(d)
    return out


def _is_expired(d: dict, now: datetime | None = None) -> bool:
    exp = d.get("expires_at")
    if not exp:
        return False
    now = now or _utcnow()
    try:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
    except ValueError:
        return False
    return _aware(exp_dt) < now


def revoke_grant(grant_id: str, *, by: str = "owner", reason: str = "",
                 expected_version: int | None = None,
                 correlation_id: str = "") -> dict:
    """Отозвать grant (optimistic version). Возвращает обновлённый grant."""
    now = _utcnow()
    with session_scope() as s:
        row = s.get(Grant, grant_id)
        if row is None:
            raise KeyError("grant не найден")
        if expected_version is not None and row.version != expected_version:
            raise ConflictError("VERSION_CONFLICT")
        row.state = "REVOKED"
        row.revoked_at = now
        row.revoked_by = by
        row.revoke_reason = redact(reason)[:400]
        row.version = row.version + 1
        row.updated_at = now
        s.commit()
        out = row.to_dict()
    audit.record("grant.revoked", f"grant={grant_id} by={by}", actor=by,
                 correlation_id=correlation_id)
    return out


class ConflictError(Exception):
    pass


def grant_snapshot(grant_id: str) -> dict:
    """Безопасный snapshot grant для ReviewPackage/checkpoint (id + hash + scope,
    без мутабельного used-budget/version-шума)."""
    d = get_grant(grant_id)
    if not d:
        return {}
    return {
        "grant_id": d["id"], "mode": d["mode"], "content_hash": d["content_hash"],
        "capabilities": d["capabilities"], "allowed_repos": d["allowed_repos"],
        "allowed_bases": d["allowed_bases"], "environment": d["environment"],
        "expires_at": d["expires_at"], "state": d["state"],
    }


def evaluate(capability: str, *, grant_id: str | None = None, project_id: str | None = None,
             repo: str | None = None, base: str | None = None, environment: str | None = None,
             workspace: str | None = None, expected_version: int | None = None,
             now: datetime | None = None) -> Decision:
    """Fail-closed оценка (§19). Возвращает :class:`Decision` со стабильным
    reason-кодом и точным next action.

    Порядок: hard-denied capability → grant существует/активен/не истёк →
    optimistic version → **project binding** → repo/base/env/workspace scope →
    capability в grant → budget. Любой сбой → deny (никогда не «permit по
    умолчанию»). Если передан ``project_id``, grant обязан быть привязан к нему
    (совпадение repo/base/env не компенсирует несовпадение проекта)."""
    now = now or _utcnow()

    # 1. Жёстко недоступные в VP-7 capabilities — независимо от grant (§40 Out).
    if capability in VP7_HARD_DENIED:
        return Decision(False, R_CAP_UNAVAILABLE, next_action(R_CAP_UNAVAILABLE),
                        detail=f"{capability} недоступна через автономию в VP-7")

    # 2. Найти grant.
    with session_scope() as s:
        if grant_id:
            row = s.get(Grant, grant_id)
            g = row.to_dict() if row else None
        else:
            stmt = select(Grant).where(Grant.state == "ACTIVE")
            if project_id:
                stmt = stmt.where(Grant.project_id == project_id)
            stmt = stmt.order_by(Grant.created_at.desc())
            g = None
            for cand in s.execute(stmt).scalars().all():
                d = cand.to_dict()
                if capability in d["capabilities"] and not _is_expired(d, now):
                    g = d
                    break
            # если по capability не нашли — берём самый свежий активный для reason.
            if g is None:
                first = s.execute(stmt).scalars().first()
                g = first.to_dict() if first else None
    if g is None:
        return Decision(False, R_NO_GRANT, next_action(R_NO_GRANT))

    gid = g["id"]
    # 3. Состояние/срок.
    if g["state"] == "REVOKED" or g.get("revoked_at"):
        return Decision(False, R_REVOKED, next_action(R_REVOKED), grant_id=gid)
    if g["state"] not in ("ACTIVE",):
        if g["state"] == "EXPIRED":
            return Decision(False, R_EXPIRED, next_action(R_EXPIRED), grant_id=gid)
        return Decision(False, R_INACTIVE, next_action(R_INACTIVE), grant_id=gid)
    if _is_expired(g, now):
        _mark_expired(gid)
        return Decision(False, R_EXPIRED, next_action(R_EXPIRED), grant_id=gid)

    # 4. Optimistic version.
    if expected_version is not None and g["version"] != expected_version:
        return Decision(False, R_CONFLICT, next_action(R_CONFLICT), grant_id=gid,
                        detail=f"ожидалась версия {expected_version}, актуальная {g['version']}")

    # 4b. Project binding (fail-closed): если у операции есть авторитетный project_id,
    #     grant ОБЯЗАН быть привязан именно к нему. Совпадение repo/base/env НЕ
    #     компенсирует несовпадение проекта. Проверяется и в auto-select, и при
    #     явном grant_id (иначе явный grant чужого проекта прошёл бы).
    if project_id is not None:
        if not g["project_id"]:
            return Decision(False, R_PROJECT_UNBOUND, next_action(R_PROJECT_UNBOUND),
                            grant_id=gid, detail="grant без project_id для операции проекта")
        if g["project_id"] != project_id:
            return Decision(False, R_PROJECT_MISMATCH, next_action(R_PROJECT_MISMATCH),
                            grant_id=gid,
                            detail=f"grant проекта {g['project_id']} != {project_id}")

    # 5a. Scope-required capabilities: grant ОБЯЗАН иметь явный repo/base scope.
    #     Пустой allowlist → fail-closed deny (не «любой repo»).
    if capability in SCOPE_REQUIRED_CAPABILITIES:
        if not g["allowed_repos"]:
            return Decision(False, R_REPO, next_action(R_REPO), grant_id=gid,
                            detail="grant без allowed_repos не разрешает repo-действие (fail-closed)")
        if not g["allowed_bases"]:
            return Decision(False, R_BASE, next_action(R_BASE), grant_id=gid,
                            detail="grant без allowed_bases не разрешает repo-действие (fail-closed)")

    # 5b. Scope: repo / base / environment / workspace. Fail-closed — если каллер
    #     указал значение, оно ДОЛЖНО присутствовать в allowlist (пустой список =
    #     нет разрешения для этого значения, а не «любой»).
    if repo is not None and repo not in g["allowed_repos"]:
        return Decision(False, R_REPO, next_action(R_REPO), grant_id=gid,
                        detail=f"repo {repo} вне allowlist")
    if base is not None and base not in g["allowed_bases"]:
        return Decision(False, R_BASE, next_action(R_BASE), grant_id=gid,
                        detail=f"base {base} вне allowlist")
    if environment is not None and environment != g["environment"]:
        return Decision(False, R_ENV, next_action(R_ENV), grant_id=gid,
                        detail=f"environment {environment} != {g['environment'] or '(пусто)'}")
    if workspace is not None and workspace not in g["workspace_allowlist"]:
        return Decision(False, R_WORKSPACE, next_action(R_WORKSPACE), grant_id=gid,
                        detail=f"workspace {workspace} вне allowlist")

    # 6. Capability присутствует в grant?
    if capability not in g["capabilities"]:
        return Decision(False, R_CAP_MISSING, next_action(R_CAP_MISSING), grant_id=gid,
                        detail=f"{capability} не входит в grant")

    # 7. Budget.
    budget = g.get("budget") or {}
    max_inv = budget.get("max_invocations")
    used_inv = budget.get("used_invocations", 0)
    if capability in BUDGETED_CAPABILITIES and max_inv is not None and used_inv >= max_inv:
        return Decision(False, R_BUDGET, next_action(R_BUDGET), grant_id=gid,
                        detail=f"использовано {used_inv}/{max_inv}")

    return Decision(True, R_PERMITTED, next_action(R_PERMITTED), grant_id=gid)


def _mark_expired(grant_id: str) -> None:
    with session_scope() as s:
        row = s.get(Grant, grant_id)
        if row is not None and row.state == "ACTIVE":
            row.state = "EXPIRED"
            row.updated_at = _utcnow()
            s.commit()


def consume_budget(grant_id: str, *, n: int = 1, expected_version: int | None = None,
                   correlation_id: str = "") -> dict:
    """Инкремент used_invocations (optimistic version). Fail-closed: если бюджет
    исчерпан, поднимает :class:`BudgetError`."""
    import json as _json
    with session_scope() as s:
        row = s.get(Grant, grant_id)
        if row is None:
            raise KeyError("grant не найден")
        if expected_version is not None and row.version != expected_version:
            raise ConflictError("VERSION_CONFLICT")
        budget = _json.loads(row.budget_json or "{}")
        max_inv = budget.get("max_invocations")
        used = budget.get("used_invocations", 0) + n
        if max_inv is not None and used > max_inv:
            raise BudgetError(f"бюджет исчерпан: {used}/{max_inv}")
        budget["used_invocations"] = used
        row.budget_json = _json.dumps(budget, ensure_ascii=False, sort_keys=True)
        row.version = row.version + 1
        row.updated_at = _utcnow()
        s.commit()
        out = row.to_dict()
    audit.record("grant.budget.consumed", f"grant={grant_id} n={n}",
                 correlation_id=correlation_id)
    return out


class BudgetError(Exception):
    pass
