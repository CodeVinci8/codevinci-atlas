"""Сервис Product Map (Master Spec §36, VP-3).

Оркестрирует bounded intake, versioned Brief/Map, поштучные решения, scope
envelope, parking lot, approval, один активный VP, Portfolio-проекцию и
детерминированный экспорт. Весь owner-текст — данные (§30.2): он редактируется,
ограничивается по длине, не исполняется и не расширяет права. Секреты в БД не
попадают. Каждая мутация пишет append-only Audit.

Истина о состоянии: только валидное evidence переводит факт в ``VERIFIED``;
approval не превращает гипотезу в VERIFIED автоматически (§44, §15.4).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from . import audit
from .db import session_scope
from .ids import new_id
from .orm import (
    Approval,
    Brief,
    Decision,
    DecisionEvent,
    GitBaseline,
    IdempotencyKey,
    MapEdge,
    MapNode,
    MapVersion,
    ParkingItem,
    ProductIntake,
    Project,
    VpActivation,
)
from .redaction import contains_secret, redact

# --- домен -----------------------------------------------------------------
TRUTH_STATUSES = ("VERIFIED", "OWNER_PROVIDED", "INFERRED", "HYPOTHESIS", "STALE", "UNKNOWN")
NODE_TYPES = ("goal", "user_problem", "brief_decision", "vp", "blocker",
              "evidence_ref", "next_action", "parking_item")
EDGE_TYPES = ("dependency", "blocks", "proves", "includes", "next")
EXPORT_SCHEMA_VERSION = 1

# Границы (bounded content, §36).
_MAX_TEXT = 4000
_MAX_ITEM = 500
_MAX_LIST = 50
_MAX_NODES = 200
_MAX_EDGES = 400


class ProductMapError(Exception):
    """Ошибка Product Map со стабильным кодом и HTTP-статусом."""

    _HTTP = {
        "VERSION_CONFLICT": 409, "ACTIVE_VP_CONFLICT": 409, "DECISION_UNRESOLVED": 409,
        "PROJECT_NOT_AVAILABLE": 409, "EVIDENCE_INVALID": 422, "ENVELOPE_INVALID": 422,
        "MAP_INVALID": 422, "INTAKE_INVALID": 400, "NOT_FOUND": 404,
    }

    def __init__(self, code: str, reason: str):
        self.code = code
        self.reason = redact(reason)
        self.http = self._HTTP.get(code, 400)
        super().__init__(f"{code}: {self.reason}")

    def to_dict(self) -> dict:
        return {"code": self.code, "reason": self.reason}


# --- хеши/каноникализация --------------------------------------------------
def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- нормализация owner-ввода (данные, §30.2) ------------------------------
def _text(v, limit: int = _MAX_TEXT) -> str:
    if v is None:
        return ""
    s = redact(str(v))
    return s[:limit]


def _item(v) -> str:
    return _text(v, _MAX_ITEM)


def _str_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [x for x in (line.strip() for line in v.splitlines()) if x]
    out = []
    for x in list(v)[:_MAX_LIST]:
        s = _item(x)
        if s:
            out.append(s)
    return out


def _guard_no_secret(*values: str) -> None:
    """Двойная защита: owner-текст уже redacted; на вход в БД — жёсткая проверка."""
    for v in values:
        if isinstance(v, str) and contains_secret(v):
            raise ProductMapError("INTAKE_INVALID", "Ввод содержит секрет — отклонено")


# --- evidence (§36: единственный тип в VP-3 — VP-2 git baseline) -----------
def validate_evidence(s, project_id: str, ref: str, ev_hash: str) -> tuple[bool, str]:
    """VERIFIED требует resolvable evidence + совпадение content-hash.

    Поддержан ``kind=git_baseline`` (единственный тип VP-3):
    ``ref='git_baseline:<id>'`` (по id), ``ref='git_baseline:latest'`` или
    ``'git_baseline'`` (последний baseline проекта) или bare ``<baseline_id>``.
    Hash обязан совпасть с сохранённым baseline.content_hash — иначе отказ.
    """
    ref = (ref or "").strip()
    ev_hash = (ev_hash or "").strip()
    if not ref or not ev_hash:
        return False, "нет evidence-ссылки или hash"
    key = ref.split(":", 1)[1].strip() if ref.startswith("git_baseline:") else ref
    if key in ("", "latest", "git_baseline", "baseline"):
        row = s.execute(
            select(GitBaseline).where(GitBaseline.project_id == project_id)
            .order_by(GitBaseline.observed_at.desc()).limit(1)).scalars().first()
    else:
        row = s.get(GitBaseline, key)
    if row is None or row.project_id != project_id:
        return False, "baseline не найден для проекта"
    if row.content_hash != ev_hash:
        return False, "hash evidence не совпадает с baseline"
    return True, "ok"


def _normalize_fact(s, project_id: str, fact: dict) -> dict:
    text = _item(fact.get("text"))
    ts = fact.get("truth_status") or "OWNER_PROVIDED"
    if ts not in TRUTH_STATUSES:
        raise ProductMapError("INTAKE_INVALID", f"неизвестный truth_status: {ts}")
    ref = _text(fact.get("evidence_ref"), 120)
    ev_hash = _text(fact.get("evidence_hash"), 80)
    if ts == "VERIFIED":
        ok, reason = validate_evidence(s, project_id, ref, ev_hash)
        if not ok:
            raise ProductMapError("EVIDENCE_INVALID", f"VERIFIED без валидного evidence: {reason}")
    return {"text": text, "truth_status": ts, "evidence_ref": ref, "evidence_hash": ev_hash}


# --- envelope --------------------------------------------------------------
def _normalize_envelope(env: dict | None) -> dict:
    env = env or {}
    return {
        "in_scope": _str_list(env.get("in_scope")),
        "out_of_scope": _str_list(env.get("out_of_scope")),
        "constraints": _str_list(env.get("constraints")),
        "boundary_note": _item(env.get("boundary_note")),
    }


def _validate_envelope(env: dict) -> None:
    if not env.get("in_scope"):
        raise ProductMapError("ENVELOPE_INVALID", "scope envelope пуст (нет in_scope)")
    overlap = set(env["in_scope"]) & set(env.get("out_of_scope") or [])
    if overlap:
        raise ProductMapError("ENVELOPE_INVALID",
                              f"in_scope и out_of_scope пересекаются: {sorted(overlap)[:3]}")


class ProductMapService:
    def __init__(self):
        pass

    # --- идемпотентность ---------------------------------------------------
    def _idem_lookup(self, s, key: str) -> str | None:
        if not key:
            return None
        row = s.get(IdempotencyKey, key)
        return row.entity_id if row else None

    def _idem_store(self, s, key: str, scope: str, project_id: str, entity_id: str) -> None:
        if not key:
            return
        s.add(IdempotencyKey(key=key[:120], scope=scope, project_id=project_id, entity_id=entity_id))

    # --- проект ------------------------------------------------------------
    def _require_available(self, s, project_id: str) -> Project:
        p = s.get(Project, project_id)
        if p is None:
            raise ProductMapError("NOT_FOUND", f"проект не найден: {project_id}")
        if p.status != "connected":
            raise ProductMapError("PROJECT_NOT_AVAILABLE",
                                  f"проект недоступен (status={p.status})")
        return p

    # --- intake -> draft brief + draft map + decisions ---------------------
    def submit_intake(self, project_id: str, data: dict, *, actor: str = "owner",
                      correlation_id: str = "", idempotency_key: str = "") -> dict:
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key:
                seen = self._idem_lookup(s, idempotency_key)
                if seen:
                    return self.get_state(project_id)

            fields = self._normalize_intake(data)
            facts = [_normalize_fact(s, project_id, f) for f in (data.get("facts") or [])[:_MAX_LIST]]
            _guard_no_secret(*(_flatten_strings(fields)))

            intake_payload = {**fields, "facts": facts}
            intake_id = new_id("intk")
            ihash = content_hash(intake_payload)
            now = _now()
            s.add(ProductIntake(
                id=intake_id, project_id=project_id, actor=actor,
                correlation_id=correlation_id,
                payload_json=canonical_json(intake_payload),
                refs_json=canonical_json(fields["links"] + fields["baseline_refs"]),
                content_hash=ihash, created_at=now, updated_at=now))

            # Draft Brief v1
            content, decisions_spec = _brief_from_intake(fields, facts)
            envelope = _normalize_envelope({
                "in_scope": fields["desired_result_list"],
                "out_of_scope": [],
                "constraints": fields["constraints"],
                "boundary_note": "Черновик: границу подтверждает владелец.",
            })
            brief = self._insert_brief(s, project_id, version=1, parent_id="",
                                       content=content, envelope=envelope,
                                       actor=actor, correlation_id=correlation_id)

            # Decisions (proposed)
            for spec in decisions_spec:
                did = new_id("dec")
                s.add(Decision(
                    id=did, project_id=project_id, decision_key=spec["key"],
                    title=spec["title"], detail=spec["detail"], status="proposed",
                    required=spec["required"], truth_status=spec["truth_status"],
                    actor=actor, correlation_id=correlation_id, version=1,
                    created_at=now, updated_at=now))
                s.add(DecisionEvent(id=new_id("dev"), decision_id=did, project_id=project_id,
                                    from_status="", to_status="proposed", note="intake",
                                    actor=actor, correlation_id=correlation_id, created_at=now))

            # Parking lot suggestions
            for sug in fields["parking_suggestions"]:
                s.add(ParkingItem(
                    id=new_id("park"), project_id=project_id, title=sug["title"],
                    reason=sug["reason"], return_condition=sug["return_condition"],
                    status="parked", actor=actor, correlation_id=correlation_id,
                    version=1, created_at=now, updated_at=now))

            # Draft Map v1
            nodes, edges = _map_from_intake(fields, decisions_spec)
            self._insert_map_version(s, project_id, version=1, parent_id="",
                                     nodes=nodes, edges=edges, actor=actor,
                                     correlation_id=correlation_id)

            self._idem_store(s, idempotency_key, "intake", project_id, intake_id)
            s.commit()

        audit.record("productmap.intake", f"project={project_id} intake={intake_id} hash={ihash}",
                     actor=actor, correlation_id=correlation_id)
        audit.record("productmap.brief.created", f"project={project_id} brief={brief} v=1",
                     actor=actor, correlation_id=correlation_id)
        return self.get_state(project_id)

    def _normalize_intake(self, data: dict) -> dict:
        idea = _text(data.get("idea") or data.get("problem"))
        target_user = _text(data.get("target_user"))
        desired_result = _text(data.get("desired_result"))
        if not (idea or target_user or desired_result):
            raise ProductMapError("INTAKE_INVALID",
                                  "intake требует idea/target_user/desired_result")
        parking = []
        for p in (data.get("parking_suggestions") or [])[:_MAX_LIST]:
            if isinstance(p, str):
                parking.append({"title": _item(p), "reason": "", "return_condition": ""})
            elif isinstance(p, dict):
                parking.append({"title": _item(p.get("title")),
                                "reason": _item(p.get("reason")),
                                "return_condition": _item(p.get("return_condition"))})
        return {
            "idea": idea, "target_user": target_user, "desired_result": desired_result,
            "desired_result_list": _str_list(desired_result) or ([desired_result] if desired_result else []),
            "constraints": _str_list(data.get("constraints")),
            "risks": _str_list(data.get("risks")),
            "links": _sanitize_links(data.get("links")),
            "baseline_refs": _str_list(data.get("baseline_refs")),
            "permissions_notes": _text(data.get("permissions_notes")),
            "parking_suggestions": [p for p in parking if p["title"]],
        }

    # --- brief вставка/версии ----------------------------------------------
    def _insert_brief(self, s, project_id: str, *, version: int, parent_id: str,
                      content: dict, envelope: dict, actor: str, correlation_id: str) -> str:
        bid = new_id("brf")
        ch = content_hash(content)
        eh = content_hash(envelope)
        now = _now()
        s.add(Brief(id=bid, project_id=project_id, version=version, parent_id=parent_id,
                    status="draft", content_json=canonical_json(content), content_hash=ch,
                    envelope_json=canonical_json(envelope), envelope_hash=eh,
                    actor=actor, correlation_id=correlation_id, created_at=now))
        return bid

    def _latest_brief(self, s, project_id: str) -> Brief | None:
        return s.execute(
            select(Brief).where(Brief.project_id == project_id, Brief.archived == False)  # noqa: E712
            .order_by(Brief.version.desc()).limit(1)).scalars().first()

    def _approved_brief(self, s, project_id: str) -> Brief | None:
        """Утверждённая версия = та, что связана последним Approval-record.

        Источник истины об approval — неизменяемая запись approvals, а не
        изменчивый статус Brief: создание нового черновика не «разутверждает»
        ранее принятую версию (§36 «superseding without deleting history»)."""
        a = s.execute(select(Approval).where(Approval.project_id == project_id)
                      .order_by(Approval.created_at.desc()).limit(1)).scalars().first()
        return s.get(Brief, a.brief_id) if a else None

    def revise_brief(self, project_id: str, brief_id: str, changes: dict, *,
                     expected_version: int | None = None, actor: str = "owner",
                     correlation_id: str = "", idempotency_key: str = "") -> dict:
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key:
                seen = self._idem_lookup(s, idempotency_key)
                if seen:
                    return self.get_brief(project_id, entity_id=seen)

            parent = s.get(Brief, brief_id)
            if parent is None or parent.project_id != project_id:
                raise ProductMapError("NOT_FOUND", f"brief не найден: {brief_id}")
            latest = self._latest_brief(s, project_id)
            if expected_version is not None and expected_version != latest.version:
                raise ProductMapError("VERSION_CONFLICT",
                                      f"ожидалась версия {expected_version}, актуальна {latest.version}")
            if parent.id != latest.id:
                raise ProductMapError("VERSION_CONFLICT",
                                      "правка не от последней версии Brief")

            base = json.loads(parent.content_json)
            new_content = _apply_brief_changes(s, project_id, base, changes)
            env = parent_env = json.loads(parent.envelope_json)
            if "envelope" in changes and changes["envelope"] is not None:
                env = _normalize_envelope(changes["envelope"])
            _guard_no_secret(canonical_json(new_content), canonical_json(env))

            new_version = latest.version + 1
            bid = self._insert_brief(s, project_id, version=new_version, parent_id=parent.id,
                                     content=new_content, envelope=env, actor=actor,
                                     correlation_id=correlation_id)
            # Черновик-родитель становится superseded; УТВЕРЖДЁННУЮ версию не
            # трогаем — она остаётся принятой (по Approval-record) до нового
            # approval. Content всегда immutable (§36).
            if parent.status == "draft":
                parent.status = "superseded"
                parent.superseded_at = _now()
            self._idem_store(s, idempotency_key, "brief.revise", project_id, bid)
            _ = parent_env
            s.commit()

        audit.record("productmap.brief.revised",
                     f"project={project_id} brief={bid} v={new_version} parent={brief_id}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_brief(project_id, entity_id=bid)

    # --- decisions ---------------------------------------------------------
    def decide(self, project_id: str, decision_id: str, action: str, *, note: str = "",
               expected_version: int | None = None, actor: str = "owner",
               correlation_id: str = "", idempotency_key: str = "") -> dict:
        if action not in ("accept", "reject"):
            raise ProductMapError("INTAKE_INVALID", f"неизвестное действие: {action}")
        target = "accepted" if action == "accept" else "rejected"
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key and self._idem_lookup(s, idempotency_key):
                return self.get_decision(project_id, decision_id)
            d = s.get(Decision, decision_id)
            if d is None or d.project_id != project_id:
                raise ProductMapError("NOT_FOUND", f"решение не найдено: {decision_id}")
            if expected_version is not None and expected_version != d.version:
                raise ProductMapError("VERSION_CONFLICT",
                                      f"ожидалась версия {expected_version}, актуальна {d.version}")
            from_status = d.status
            res = s.execute(
                update(Decision).where(Decision.id == decision_id, Decision.version == d.version)
                .values(status=target, note=_item(note), version=d.version + 1, updated_at=_now()))
            if res.rowcount != 1:
                raise ProductMapError("VERSION_CONFLICT", "конкурентное изменение решения")
            s.add(DecisionEvent(id=new_id("dev"), decision_id=decision_id, project_id=project_id,
                                from_status=from_status, to_status=target, note=_item(note),
                                actor=actor, correlation_id=correlation_id, created_at=_now()))
            self._idem_store(s, idempotency_key, f"decision.{action}", project_id, decision_id)
            s.commit()
        audit.record(f"productmap.decision.{action}",
                     f"project={project_id} decision={decision_id} {from_status}->{target}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_decision(project_id, decision_id)

    # --- approval ----------------------------------------------------------
    def approve_brief(self, project_id: str, brief_id: str, *, expected_version: int | None = None,
                      map_version_id: str | None = None, actor: str = "owner",
                      correlation_id: str = "", idempotency_key: str = "") -> dict:
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key:
                seen = self._idem_lookup(s, idempotency_key)
                if seen:
                    return self._approval_dict(s, seen)

            brief = s.get(Brief, brief_id)
            if brief is None or brief.project_id != project_id or brief.archived:
                raise ProductMapError("NOT_FOUND", f"brief не найден: {brief_id}")
            latest = self._latest_brief(s, project_id)
            # stale-write: approve только последней версии
            if expected_version is not None and expected_version != latest.version:
                raise ProductMapError("VERSION_CONFLICT",
                                      f"ожидалась версия {expected_version}, актуальна {latest.version}")
            if brief.id != latest.id:
                raise ProductMapError("VERSION_CONFLICT",
                                      "approve не последней версии Brief (stale)")

            # unresolved required decisions
            unresolved = s.execute(
                select(func.count()).select_from(Decision).where(
                    Decision.project_id == project_id, Decision.required == True,  # noqa: E712
                    Decision.status == "proposed")).scalar_one()
            if unresolved:
                raise ProductMapError("DECISION_UNRESOLVED",
                                      f"есть неразрешённые required-решения: {unresolved}")

            # envelope
            env = json.loads(brief.envelope_json)
            _validate_envelope(env)

            # VERIFIED facts must have valid evidence
            content = json.loads(brief.content_json)
            for fact in content.get("confirmed_facts", []):
                if fact.get("truth_status") == "VERIFIED":
                    ok, reason = validate_evidence(s, project_id, fact.get("evidence_ref", ""),
                                                   fact.get("evidence_hash", ""))
                    if not ok:
                        raise ProductMapError("EVIDENCE_INVALID",
                                              f"VERIFIED-факт с невалидным evidence: {reason}")

            # map references valid
            mv = self._resolve_map_version(s, project_id, map_version_id)
            self._validate_map_rows(s, mv.id)

            # bind decisions state
            dstate = self._decisions_state_hash(s, project_id)

            approval_id = new_id("appr")
            now = _now()
            s.add(Approval(id=approval_id, project_id=project_id, brief_id=brief.id,
                           brief_hash=brief.content_hash, map_version_id=mv.id,
                           envelope_hash=brief.envelope_hash, decisions_hash=dstate,
                           actor=actor, correlation_id=correlation_id, created_at=now))
            # supersede previously approved
            for prev in s.execute(select(Brief).where(
                    Brief.project_id == project_id, Brief.status == "approved")).scalars().all():
                prev.status = "superseded"
                prev.superseded_at = now
            brief.status = "approved"
            mv.status = "approved"
            self._idem_store(s, idempotency_key, "brief.approve", project_id, approval_id)
            s.commit()
            result = self._approval_dict(s, approval_id)

        audit.record("productmap.brief.approved",
                     f"project={project_id} brief={brief_id} approval={approval_id} "
                     f"brief_hash={result['brief_hash']} map={result['map_version_id']}",
                     actor=actor, correlation_id=correlation_id)
        return result

    # --- Project Map -------------------------------------------------------
    def create_map_version(self, project_id: str, nodes: list[dict], edges: list[dict], *,
                           expected_version: int | None = None, actor: str = "owner",
                           correlation_id: str = "", idempotency_key: str = "") -> dict:
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key:
                seen = self._idem_lookup(s, idempotency_key)
                if seen:
                    return self.get_map(project_id)
            latest = self._latest_map(s, project_id)
            cur = latest.version if latest else 0
            if expected_version is not None and expected_version != cur:
                raise ProductMapError("VERSION_CONFLICT",
                                      f"ожидалась версия карты {expected_version}, актуальна {cur}")
            nnodes, nedges = _normalize_map(nodes, edges)  # validates types/dangling/cycles
            mv_id = self._insert_map_version(s, project_id, version=cur + 1,
                                             parent_id=latest.id if latest else "",
                                             nodes=nnodes, edges=nedges, actor=actor,
                                             correlation_id=correlation_id)
            if latest:
                latest.status = "superseded"
                latest.superseded_at = _now()
            self._idem_store(s, idempotency_key, "map.version", project_id, mv_id)
            s.commit()
        audit.record("productmap.map.version",
                     f"project={project_id} map={mv_id} v={cur + 1} nodes={len(nodes)} edges={len(edges)}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_map(project_id)

    def _insert_map_version(self, s, project_id: str, *, version: int, parent_id: str,
                            nodes: list[dict], edges: list[dict], actor: str,
                            correlation_id: str) -> str:
        snapshot = {"nodes": sorted(nodes, key=lambda n: n["node_key"]),
                    "edges": sorted(edges, key=lambda e: (e["src_key"], e["dst_key"], e["edge_type"]))}
        mv_id = new_id("map")
        ch = content_hash(snapshot)
        now = _now()
        s.add(MapVersion(id=mv_id, project_id=project_id, version=version, parent_id=parent_id,
                         status="draft", content_hash=ch, actor=actor,
                         correlation_id=correlation_id, created_at=now))
        for n in nodes:
            s.add(MapNode(id=new_id("mnd"), map_version_id=mv_id, project_id=project_id,
                          node_key=n["node_key"], node_type=n["node_type"], title=n["title"],
                          detail=n["detail"], truth_status=n["truth_status"],
                          evidence_ref=n["evidence_ref"], evidence_hash=n["evidence_hash"],
                          data_json=canonical_json(n.get("data") or {}), created_at=now))
        for e in edges:
            s.add(MapEdge(id=new_id("meg"), map_version_id=mv_id, project_id=project_id,
                          src_key=e["src_key"], dst_key=e["dst_key"], edge_type=e["edge_type"],
                          created_at=now))
        return mv_id

    def _latest_map(self, s, project_id: str) -> MapVersion | None:
        return s.execute(
            select(MapVersion).where(MapVersion.project_id == project_id,
                                     MapVersion.archived == False)  # noqa: E712
            .order_by(MapVersion.version.desc()).limit(1)).scalars().first()

    def _resolve_map_version(self, s, project_id: str, map_version_id: str | None) -> MapVersion:
        if map_version_id:
            mv = s.get(MapVersion, map_version_id)
            if mv is None or mv.project_id != project_id:
                raise ProductMapError("MAP_INVALID", "указанная версия карты не найдена")
            return mv
        mv = self._latest_map(s, project_id)
        if mv is None:
            raise ProductMapError("MAP_INVALID", "у проекта нет версии карты")
        return mv

    def _validate_map_rows(self, s, map_version_id: str) -> None:
        nodes = s.execute(select(MapNode).where(MapNode.map_version_id == map_version_id)).scalars().all()
        edges = s.execute(select(MapEdge).where(MapEdge.map_version_id == map_version_id)).scalars().all()
        _normalize_map([_node_row_dict(n) for n in nodes],
                       [{"src_key": e.src_key, "dst_key": e.dst_key, "edge_type": e.edge_type} for e in edges])

    # --- one active VP -----------------------------------------------------
    def activate_vp(self, project_id: str, vp_key: str, *, actor: str = "owner",
                    correlation_id: str = "", idempotency_key: str = "") -> dict:
        vp_key = _item(vp_key)
        if not vp_key:
            raise ProductMapError("INTAKE_INVALID", "vp_key обязателен")
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key and self._idem_lookup(s, idempotency_key):
                return self.get_active_vp(project_id)
            vid = new_id("vpa")
            s.add(VpActivation(id=vid, project_id=project_id, vp_key=vp_key,
                               active_slot="ACTIVE", actor=actor,
                               correlation_id=correlation_id, activated_at=_now()))
            try:
                if idempotency_key:
                    self._idem_store(s, idempotency_key, "vp.activate", project_id, vid)
                s.commit()
            except IntegrityError:
                s.rollback()
                raise ProductMapError("ACTIVE_VP_CONFLICT",
                                      "у проекта уже есть активный VP") from None
        audit.record("productmap.vp.activated", f"project={project_id} vp={vp_key}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_active_vp(project_id)

    def deactivate_vp(self, project_id: str, *, actor: str = "owner",
                      correlation_id: str = "") -> dict:
        with session_scope() as s:
            self._require_available(s, project_id)
            row = s.execute(select(VpActivation).where(
                VpActivation.project_id == project_id,
                VpActivation.active_slot == "ACTIVE")).scalars().first()
            if row is None:
                raise ProductMapError("NOT_FOUND", "нет активного VP")
            row.active_slot = row.id
            row.deactivated_at = _now()
            s.commit()
        audit.record("productmap.vp.deactivated", f"project={project_id}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_active_vp(project_id)

    def get_active_vp(self, project_id: str) -> dict:
        with session_scope() as s:
            row = s.execute(select(VpActivation).where(
                VpActivation.project_id == project_id,
                VpActivation.active_slot == "ACTIVE")).scalars().first()
            return {"active_vp": row.vp_key if row else None,
                    "activated_at": _iso(row.activated_at) if row else None}

    # --- parking lot -------------------------------------------------------
    def add_parking_item(self, project_id: str, title: str, *, reason: str = "",
                         return_condition: str = "", actor: str = "owner",
                         correlation_id: str = "", idempotency_key: str = "") -> dict:
        title = _item(title)
        if not title:
            raise ProductMapError("INTAKE_INVALID", "title обязателен для parking-item")
        with session_scope() as s:
            self._require_available(s, project_id)
            if idempotency_key and (seen := self._idem_lookup(s, idempotency_key)):
                return self.get_parking_item(project_id, seen)
            pid = new_id("park")
            now = _now()
            s.add(ParkingItem(id=pid, project_id=project_id, title=title, reason=_item(reason),
                              return_condition=_item(return_condition), status="parked",
                              actor=actor, correlation_id=correlation_id, version=1,
                              created_at=now, updated_at=now))
            self._idem_store(s, idempotency_key, "parking.add", project_id, pid)
            s.commit()
        audit.record("productmap.parking.added", f"project={project_id} item={pid}",
                     actor=actor, correlation_id=correlation_id)
        return self.get_parking_item(project_id, pid)

    # --- diffs -------------------------------------------------------------
    def diff_briefs(self, project_id: str, from_v: int, to_v: int) -> dict:
        with session_scope() as s:
            a = self._brief_by_version(s, project_id, from_v)
            b = self._brief_by_version(s, project_id, to_v)
            ca, cb = json.loads(a.content_json), json.loads(b.content_json)
            ea, eb = json.loads(a.envelope_json), json.loads(b.envelope_json)
            return {"from": from_v, "to": to_v,
                    "from_hash": a.content_hash, "to_hash": b.content_hash,
                    "content": _diff_tree(ca, cb),   # точный field-level diff
                    "envelope": _diff_tree(ea, eb)}

    def diff_maps(self, project_id: str, from_v: int, to_v: int) -> dict:
        with session_scope() as s:
            a = self._map_by_version(s, project_id, from_v)
            b = self._map_by_version(s, project_id, to_v)
            sa = self._map_snapshot(s, a.id)
            sb = self._map_snapshot(s, b.id)
        return {"from": from_v, "to": to_v, "from_hash": a.content_hash, "to_hash": b.content_hash,
                "nodes": _diff_keyed(sa["nodes"], sb["nodes"], "node_key"),
                "edges": _diff_keyed(sa["edges"], sb["edges"], "edge_id")}

    # --- reads -------------------------------------------------------------
    def get_state(self, project_id: str) -> dict:
        with session_scope() as s:
            p = s.get(Project, project_id)
            if p is None:
                raise ProductMapError("NOT_FOUND", f"проект не найден: {project_id}")
            briefs = s.execute(select(Brief).where(Brief.project_id == project_id)
                               .order_by(Brief.version.asc())).scalars().all()
            latest = briefs[-1] if briefs else None
            approved = self._approved_brief(s, project_id)
            decisions = s.execute(select(Decision).where(Decision.project_id == project_id)
                                  .order_by(Decision.created_at.asc())).scalars().all()
            parking = s.execute(select(ParkingItem).where(ParkingItem.project_id == project_id)
                                .order_by(ParkingItem.created_at.asc())).scalars().all()
            mv = self._latest_map(s, project_id)
            map_dict = self._map_dict(s, mv) if mv else None
            active = s.execute(select(VpActivation).where(
                VpActivation.project_id == project_id,
                VpActivation.active_slot == "ACTIVE")).scalars().first()
            state = {
                "project": {"id": p.id, "name": p.name, "status": p.status,
                            "source_kind": p.source_kind},
                "brief": _brief_dict(latest, full=True) if latest else None,
                "approved_brief_version": approved.version if approved else None,
                "brief_versions": [{"version": b.version, "id": b.id, "status": b.status,
                                    "content_hash": b.content_hash, "created_at": _iso(b.created_at)}
                                   for b in briefs],
                "decisions": [_decision_dict(d) for d in decisions],
                "parking_lot": [_parking_dict(x) for x in parking],
                "map": map_dict,
                "active_vp": active.vp_key if active else None,
                "stage": _stage(latest, approved),
                "next_action": _next_action(latest, approved, decisions, active),
            }
            return state

    def get_brief(self, project_id: str, *, version: int | None = None,
                  entity_id: str | None = None) -> dict:
        with session_scope() as s:
            if entity_id:
                b = s.get(Brief, entity_id)
            elif version is not None:
                b = self._brief_by_version(s, project_id, version)
            else:
                b = self._latest_brief(s, project_id)
            if b is None or b.project_id != project_id:
                raise ProductMapError("NOT_FOUND", "brief не найден")
            return _brief_dict(b, full=True)

    def list_briefs(self, project_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(Brief).where(Brief.project_id == project_id)
                             .order_by(Brief.version.asc())).scalars().all()
            return [_brief_dict(b) for b in rows]

    def get_decision(self, project_id: str, decision_id: str) -> dict:
        with session_scope() as s:
            d = s.get(Decision, decision_id)
            if d is None or d.project_id != project_id:
                raise ProductMapError("NOT_FOUND", f"решение не найдено: {decision_id}")
            events = s.execute(select(DecisionEvent).where(DecisionEvent.decision_id == decision_id)
                               .order_by(DecisionEvent.created_at.asc())).scalars().all()
            out = _decision_dict(d)
            out["history"] = [{"from": e.from_status, "to": e.to_status, "note": e.note,
                               "at": _iso(e.created_at)} for e in events]
            return out

    def list_decisions(self, project_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(Decision).where(Decision.project_id == project_id)
                             .order_by(Decision.created_at.asc())).scalars().all()
            return [_decision_dict(d) for d in rows]

    def get_parking_item(self, project_id: str, item_id: str) -> dict:
        with session_scope() as s:
            x = s.get(ParkingItem, item_id)
            if x is None or x.project_id != project_id:
                raise ProductMapError("NOT_FOUND", "parking-item не найден")
            return _parking_dict(x)

    def list_parking(self, project_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(ParkingItem).where(ParkingItem.project_id == project_id)
                             .order_by(ParkingItem.created_at.asc())).scalars().all()
            return [_parking_dict(x) for x in rows]

    def get_map(self, project_id: str) -> dict:
        with session_scope() as s:
            mv = self._latest_map(s, project_id)
            if mv is None:
                raise ProductMapError("NOT_FOUND", "у проекта нет карты")
            return self._map_dict(s, mv)

    def list_map_versions(self, project_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(MapVersion).where(MapVersion.project_id == project_id)
                             .order_by(MapVersion.version.asc())).scalars().all()
            return [{"version": m.version, "id": m.id, "status": m.status,
                     "content_hash": m.content_hash, "created_at": _iso(m.created_at)} for m in rows]

    def get_intake(self, project_id: str) -> dict | None:
        with session_scope() as s:
            row = s.execute(select(ProductIntake).where(ProductIntake.project_id == project_id)
                            .order_by(ProductIntake.created_at.desc()).limit(1)).scalars().first()
            if row is None:
                return None
            return {"id": row.id, "content_hash": row.content_hash,
                    "payload": json.loads(row.payload_json), "created_at": _iso(row.created_at)}

    # --- Portfolio ---------------------------------------------------------
    def portfolio(self) -> list[dict]:
        """Правдивая проекция по проектам (§27.3). Отсутствующее — UNKNOWN."""
        out = []
        with session_scope() as s:
            projects = s.execute(select(Project).order_by(Project.created_at.desc())).scalars().all()
            for p in projects:
                briefs = s.execute(select(Brief).where(Brief.project_id == p.id)
                                   .order_by(Brief.version.asc())).scalars().all()
                latest = briefs[-1] if briefs else None
                approved = self._approved_brief(s, p.id)
                active = s.execute(select(VpActivation).where(
                    VpActivation.project_id == p.id,
                    VpActivation.active_slot == "ACTIVE")).scalars().first()
                mv = self._latest_map(s, p.id)
                blocker = None
                if mv:
                    bn = s.execute(select(MapNode).where(
                        MapNode.map_version_id == mv.id, MapNode.node_type == "blocker")
                        .limit(1)).scalars().first()
                    blocker = bn.title if bn else None
                decisions = s.execute(select(Decision).where(Decision.project_id == p.id)).scalars().all()
                out.append({
                    "project_id": p.id, "name": p.name, "status": p.status,
                    "stage": _stage(latest, approved) if latest else "intake_pending",
                    "active_vp": active.vp_key if active else "UNKNOWN",
                    "last_known_state": (latest.status if latest else "UNKNOWN"),
                    "blocker": blocker or "UNKNOWN",
                    "truth_state": _portfolio_truth(latest, approved),
                    "brief_version": latest.version if latest else None,
                    "approved_version": approved.version if approved else None,
                    "next_action": _next_action(latest, approved, decisions, active),
                })
        return out

    # --- helpers для reads -------------------------------------------------
    def _brief_by_version(self, s, project_id: str, version: int) -> Brief:
        b = s.execute(select(Brief).where(Brief.project_id == project_id,
                                          Brief.version == version)).scalars().first()
        if b is None:
            raise ProductMapError("NOT_FOUND", f"версия Brief не найдена: {version}")
        return b

    def _map_by_version(self, s, project_id: str, version: int) -> MapVersion:
        m = s.execute(select(MapVersion).where(MapVersion.project_id == project_id,
                                               MapVersion.version == version)).scalars().first()
        if m is None:
            raise ProductMapError("NOT_FOUND", f"версия карты не найдена: {version}")
        return m

    def _map_snapshot(self, s, map_version_id: str) -> dict:
        nodes = s.execute(select(MapNode).where(MapNode.map_version_id == map_version_id)
                          .order_by(MapNode.node_key.asc())).scalars().all()
        edges = s.execute(select(MapEdge).where(MapEdge.map_version_id == map_version_id)).scalars().all()
        return {
            "nodes": [_node_row_dict(n) for n in nodes],
            "edges": [{"edge_id": f"{e.src_key}->{e.dst_key}:{e.edge_type}", "src_key": e.src_key,
                       "dst_key": e.dst_key, "edge_type": e.edge_type} for e in edges],
        }

    def _map_dict(self, s, mv: MapVersion) -> dict:
        snap = self._map_snapshot(s, mv.id)
        return {"id": mv.id, "version": mv.version, "status": mv.status,
                "content_hash": mv.content_hash, "created_at": _iso(mv.created_at),
                "nodes": snap["nodes"], "edges": snap["edges"]}

    def _decisions_state_hash(self, s, project_id: str) -> str:
        rows = s.execute(select(Decision).where(Decision.project_id == project_id)
                         .order_by(Decision.decision_key.asc())).scalars().all()
        return content_hash([{"key": d.decision_key, "status": d.status} for d in rows])

    def _approval_dict(self, s, approval_id: str) -> dict:
        a = s.get(Approval, approval_id)
        if a is None:
            raise ProductMapError("NOT_FOUND", "approval не найден")
        return {"id": a.id, "project_id": a.project_id, "brief_id": a.brief_id,
                "brief_hash": a.brief_hash, "map_version_id": a.map_version_id,
                "envelope_hash": a.envelope_hash, "decisions_hash": a.decisions_hash,
                "actor": a.actor, "created_at": _iso(a.created_at)}

    def latest_approval(self, project_id: str) -> dict | None:
        with session_scope() as s:
            a = s.execute(select(Approval).where(Approval.project_id == project_id)
                          .order_by(Approval.created_at.desc()).limit(1)).scalars().first()
            return self._approval_dict(s, a.id) if a else None

    # --- экспорт (детерминированный, §36 Phase H) --------------------------
    def export_payload(self, project_id: str, *, version: int | None = None) -> dict:
        """Каноническая экспорт-проекция точной версии. Детерминирована для
        одной и той же принятой версии (кроме блока ``_generated``)."""
        with session_scope() as s:
            p = s.get(Project, project_id)
            if p is None:
                raise ProductMapError("NOT_FOUND", f"проект не найден: {project_id}")
            approval = s.execute(select(Approval).where(Approval.project_id == project_id)
                                 .order_by(Approval.created_at.desc()).limit(1)).scalars().first()
            if version is not None:
                brief = self._brief_by_version(s, project_id, version)
            elif approval is not None:
                brief = s.get(Brief, approval.brief_id)
            else:
                brief = self._latest_brief(s, project_id)
            if brief is None:
                raise ProductMapError("NOT_FOUND", "нет Brief для экспорта")

            # карта: у принятой версии — связанная approval-карта; иначе последняя
            if approval is not None and approval.brief_id == brief.id and approval.map_version_id:
                mv = s.get(MapVersion, approval.map_version_id)
            else:
                mv = self._latest_map(s, project_id)
            map_snapshot = self._map_snapshot(s, mv.id) if mv else {"nodes": [], "edges": []}
            map_meta = ({"id": mv.id, "version": mv.version, "status": mv.status,
                         "content_hash": mv.content_hash} if mv else None)

            decisions = s.execute(select(Decision).where(Decision.project_id == project_id)
                                  .order_by(Decision.decision_key.asc())).scalars().all()
            parking = s.execute(select(ParkingItem).where(ParkingItem.project_id == project_id)
                                .order_by(ParkingItem.created_at.asc())).scalars().all()
            active = s.execute(select(VpActivation).where(
                VpActivation.project_id == project_id,
                VpActivation.active_slot == "ACTIVE")).scalars().first()

            payload = {
                "schema_version": EXPORT_SCHEMA_VERSION,
                "project": {"id": p.id, "name": p.name, "status": p.status,
                            "source_kind": p.source_kind},
                "brief": {"id": brief.id, "version": brief.version, "status": brief.status,
                          "content_hash": brief.content_hash, "envelope_hash": brief.envelope_hash,
                          "created_at": _iso(brief.created_at),
                          "content": json.loads(brief.content_json),
                          "envelope": json.loads(brief.envelope_json)},
                "map": {"meta": map_meta, "nodes": map_snapshot["nodes"],
                        "edges": map_snapshot["edges"]},
                "decisions": [{"decision_key": d.decision_key, "title": d.title,
                               "status": d.status, "required": bool(d.required),
                               "truth_status": d.truth_status, "note": d.note}
                              for d in decisions],
                "parking_lot": [{"title": x.title, "reason": x.reason,
                                 "return_condition": x.return_condition, "status": x.status}
                                for x in parking],
                "active_vp": active.vp_key if active else None,
                "approval": (self._approval_dict(s, approval.id)
                             if approval is not None and approval.brief_id == brief.id else None),
            }
        # детерминированный content-hash payload (без _generated)
        payload_hash = content_hash(payload)
        payload["_generated"] = {"generated_at": _iso(_now()), "payload_hash": payload_hash}
        return payload


# --- модульные функции: генерация/диф/валидация ----------------------------
def _flatten_strings(fields: dict) -> list[str]:
    out = []
    for v in fields.values():
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict):
                    out.extend(str(z) for z in x.values())
    return out


def _sanitize_links(links) -> list[str]:
    """Хранить только санированные метаданные ссылок; не ходить по ним (§36)."""
    out = []
    for raw in _str_list(links):
        s = redact(raw)
        # обрезать query/fragment и credentials-часть
        s = s.split("#", 1)[0]
        out.append(s[:_MAX_ITEM])
    return out


def _brief_from_intake(fields: dict, facts: list[dict]) -> tuple[dict, list[dict]]:
    content = {
        "product_statement": fields["idea"] or fields["desired_result"],
        "user_and_problem": (fields["target_user"] + (" — " if fields["target_user"] and fields["idea"] else "") + fields["idea"]).strip(" —"),
        "current_alternative": "",
        "promised_result": fields["desired_result"],
        "confirmed_facts": facts,
        "hypotheses": [{"text": r, "truth_status": "HYPOTHESIS"} for r in fields["risks"]],
        "main_scenario": "",
        "mvp_scope": fields["desired_result_list"],
        "out_of_scope": [],
        "success_metric": "",
        "risks": fields["risks"],
        "minimum_validation": "",
        "stop_criterion": "",
        "linked_decisions": ["confirm_target_user", "confirm_mvp_scope"],
    }
    decisions_spec = [
        {"key": "confirm_target_user", "title": "Подтвердить целевого пользователя",
         "detail": fields["target_user"] or "Владелец не указал пользователя — требуется подтверждение.",
         "required": True, "truth_status": "OWNER_PROVIDED" if fields["target_user"] else "UNKNOWN"},
        {"key": "confirm_mvp_scope", "title": "Подтвердить границу MVP",
         "detail": "; ".join(fields["desired_result_list"]) or "Границу MVP нужно подтвердить.",
         "required": True, "truth_status": "HYPOTHESIS"},
        {"key": "confirm_success_metric", "title": "Подтвердить метрику успеха",
         "detail": "Метрика/наблюдение успеха не задано.",
         "required": False, "truth_status": "UNKNOWN"},
    ]
    return content, decisions_spec


def _map_from_intake(fields: dict, decisions_spec: list[dict]) -> tuple[list[dict], list[dict]]:
    def node(key, ntype, title, detail="", truth="INFERRED"):
        return {"node_key": key, "node_type": ntype, "title": title[:300], "detail": detail,
                "truth_status": truth, "evidence_ref": "", "evidence_hash": "", "data": {}}

    nodes = [
        node("goal", "goal", fields["desired_result"] or "Цель продукта", truth="OWNER_PROVIDED"
             if fields["desired_result"] else "UNKNOWN"),
        node("user_problem", "user_problem",
             fields["target_user"] or "Целевой пользователь", fields["idea"],
             truth="OWNER_PROVIDED" if fields["target_user"] else "HYPOTHESIS"),
        node("next_action", "next_action", "Подтвердить решения и утвердить Brief"),
    ]
    edges = [
        {"src_key": "goal", "dst_key": "user_problem", "edge_type": "includes"},
        {"src_key": "goal", "dst_key": "next_action", "edge_type": "next"},
    ]
    for spec in decisions_spec:
        nk = f"decision:{spec['key']}"
        nodes.append(node(nk, "brief_decision", spec["title"], spec["detail"], "HYPOTHESIS"))
        edges.append({"src_key": nk, "dst_key": "goal", "edge_type": "blocks"})
    for i, sug in enumerate(fields["parking_suggestions"]):
        nodes.append(node(f"parking:{i}", "parking_item", sug["title"], sug["reason"], "UNKNOWN"))
    return nodes, edges


def _normalize_map(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    if len(nodes) > _MAX_NODES or len(edges) > _MAX_EDGES:
        raise ProductMapError("MAP_INVALID", "превышен предел узлов/рёбер карты")
    seen_keys: set[str] = set()
    nnodes = []
    for n in nodes:
        key = _item(n.get("node_key"))
        ntype = n.get("node_type")
        if not key:
            raise ProductMapError("MAP_INVALID", "у узла нет node_key")
        if ntype not in NODE_TYPES:
            raise ProductMapError("MAP_INVALID", f"неизвестный node_type: {ntype}")
        if key in seen_keys:
            raise ProductMapError("MAP_INVALID", f"дубликат node_key: {key}")
        seen_keys.add(key)
        ts = n.get("truth_status") or "UNKNOWN"
        if ts not in TRUTH_STATUSES:
            raise ProductMapError("MAP_INVALID", f"неизвестный truth_status узла: {ts}")
        nnodes.append({"node_key": key, "node_type": ntype, "title": _text(n.get("title"), 300),
                       "detail": _text(n.get("detail")), "truth_status": ts,
                       "evidence_ref": _text(n.get("evidence_ref"), 120),
                       "evidence_hash": _text(n.get("evidence_hash"), 80),
                       "data": n.get("data") or {}})
    nedges = []
    adj: dict[str, list[str]] = {k: [] for k in seen_keys}
    for e in edges:
        src, dst, et = _item(e.get("src_key")), _item(e.get("dst_key")), e.get("edge_type")
        if et not in EDGE_TYPES:
            raise ProductMapError("MAP_INVALID", f"неизвестный edge_type: {et}")
        if src not in seen_keys or dst not in seen_keys:
            raise ProductMapError("MAP_INVALID", f"висячее ребро {src}->{dst}")
        nedges.append({"src_key": src, "dst_key": dst, "edge_type": et})
        adj[src].append(dst)
    _reject_cycles(adj)
    return nnodes, nedges


def _reject_cycles(adj: dict[str, list[str]]) -> None:
    """Семантика зависимостей ациклична; цикл → MAP_INVALID."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in adj}

    def visit(u: str, stack: list[str]) -> None:
        color[u] = GRAY
        for v in adj.get(u, []):
            if color.get(v) == GRAY:
                raise ProductMapError("MAP_INVALID", f"цикл в карте: {'->'.join(stack + [v])}")
            if color.get(v) == WHITE:
                visit(v, stack + [v])
        color[u] = BLACK

    for k in adj:
        if color[k] == WHITE:
            visit(k, [k])


def _apply_brief_changes(s, project_id: str, base: dict, changes: dict) -> dict:
    out = dict(base)
    text_fields = ("product_statement", "user_and_problem", "current_alternative",
                   "promised_result", "main_scenario", "success_metric",
                   "minimum_validation", "stop_criterion")
    list_fields = ("mvp_scope", "out_of_scope", "risks", "linked_decisions")
    for f in text_fields:
        if f in changes:
            out[f] = _text(changes[f])
    for f in list_fields:
        if f in changes:
            out[f] = _str_list(changes[f])
    if "confirmed_facts" in changes:
        out["confirmed_facts"] = [_normalize_fact(s, project_id, x)
                                  for x in (changes["confirmed_facts"] or [])[:_MAX_LIST]]
    if "hypotheses" in changes:
        out["hypotheses"] = [{"text": _item(x.get("text") if isinstance(x, dict) else x),
                              "truth_status": (x.get("truth_status") if isinstance(x, dict) else "HYPOTHESIS")}
                             for x in (changes["hypotheses"] or [])[:_MAX_LIST]]
    return out


def _diff_tree(a: dict, b: dict) -> dict:
    added, removed, changed = {}, {}, {}
    keys = set(a) | set(b)
    for k in sorted(keys):
        if k not in a:
            added[k] = b[k]
        elif k not in b:
            removed[k] = a[k]
        elif a[k] != b[k]:
            changed[k] = {"from": a[k], "to": b[k]}
    return {"added": added, "removed": removed, "changed": changed}


def _diff_keyed(a: list[dict], b: list[dict], key: str) -> dict:
    am = {x[key]: x for x in a}
    bm = {x[key]: x for x in b}
    added = [bm[k] for k in sorted(bm) if k not in am]
    removed = [am[k] for k in sorted(am) if k not in bm]
    changed = [{"key": k, "from": am[k], "to": bm[k]} for k in sorted(am.keys() & bm.keys())
               if am[k] != bm[k]]
    return {"added": added, "removed": removed, "changed": changed}


# --- сериализация ----------------------------------------------------------
def _node_row_dict(n) -> dict:
    return {"node_key": n.node_key, "node_type": n.node_type, "title": n.title,
            "detail": n.detail, "truth_status": n.truth_status,
            "evidence_ref": n.evidence_ref, "evidence_hash": n.evidence_hash,
            "data": json.loads(n.data_json) if n.data_json else {}}


def _brief_dict(b: Brief, *, full: bool = False) -> dict:
    out = {"id": b.id, "version": b.version, "parent_id": b.parent_id, "status": b.status,
           "content_hash": b.content_hash, "envelope_hash": b.envelope_hash,
           "created_at": _iso(b.created_at)}
    if full:
        out["content"] = json.loads(b.content_json)
        out["envelope"] = json.loads(b.envelope_json)
    return out


def _decision_dict(d: Decision) -> dict:
    return {"id": d.id, "decision_key": d.decision_key, "title": d.title, "detail": d.detail,
            "status": d.status, "required": bool(d.required), "truth_status": d.truth_status,
            "note": d.note, "version": d.version, "updated_at": _iso(d.updated_at)}


def _parking_dict(x: ParkingItem) -> dict:
    return {"id": x.id, "title": x.title, "reason": x.reason,
            "return_condition": x.return_condition, "status": x.status,
            "version": x.version, "created_at": _iso(x.created_at)}


def _stage(latest: Brief | None, approved: Brief | None) -> str:
    if approved is not None:
        return "approved"
    if latest is None:
        return "intake_pending"
    return "draft"


def _portfolio_truth(latest: Brief | None, approved: Brief | None) -> str:
    if approved is not None:
        return "approved"
    if latest is None:
        return "UNKNOWN"
    return "draft"


def _next_action(latest, approved, decisions, active) -> str:
    if latest is None:
        return "Отправьте структурный intake, чтобы создать Draft Brief и Draft Map."
    unresolved = [d for d in decisions if getattr(d, "required", False)
                  and getattr(d, "status", "") == "proposed"]
    if unresolved:
        return f"Разрешите {len(unresolved)} required-решение(й) перед утверждением Brief."
    if approved is None:
        return "Утвердите точную версию Brief и scope envelope."
    if active is None:
        return "Активируйте ровно один VP как следующий шаг."
    return "Готово: экспортируйте принятое состояние (Markdown/JSON) для owner-ревью."


__all__ = ["ProductMapService", "ProductMapError", "content_hash", "canonical_json",
           "validate_evidence", "TRUTH_STATUSES", "NODE_TYPES", "EDGE_TYPES",
           "EXPORT_SCHEMA_VERSION"]
