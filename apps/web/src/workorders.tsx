// VP-4 Work Orders & Context UI (Master Spec §16, §37). Ember, RU/EN, a11y.
// Статусы/решения — не только цветом: символ + текст + класс. IDs/хеши — моноширинно.
import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  api, type Criterion, type HandoffRow, type JobPackage, type MergePreview,
  type OptimizerDecision, type ReconstructResult, type VpSpecFull, type VpSpecSummary,
  type WorkOrderFull, type WorkOrderRow, type WoStatus,
} from "./api";
import { type LocaleKey } from "./i18n";

type T = (key: LocaleKey) => string;

const WO_META: Record<WoStatus, { sym: string; cls: string }> = {
  draft: { sym: "○", cls: "tb-unknown" },
  ready: { sym: "◔", cls: "tb-inferred" },
  active: { sym: "●", cls: "tb-active" },
  checkpointed: { sym: "◆", cls: "tb-info" },
  handoff_ready: { sym: "⇄", cls: "tb-info" },
  blocked: { sym: "▲", cls: "tb-hypo" },
  completed: { sym: "✓", cls: "tb-verified" },
  cancelled: { sym: "✗", cls: "tb-stale" },
};

function WoBadge({ status, t }: { status: WoStatus; t: T }) {
  const m = WO_META[status] ?? WO_META.draft;
  return (
    <span className={`tbadge ${m.cls}`} role="status">
      <span aria-hidden="true">{m.sym}</span> {t((`wo.status.${status}`) as LocaleKey)}
    </span>
  );
}

// допустимые переходы (зеркало Core VALID_TRANSITIONS) — для кнопок действий
const NEXT: Record<WoStatus, WoStatus[]> = {
  draft: ["ready", "cancelled"],
  ready: ["active", "cancelled"],
  active: ["checkpointed", "handoff_ready", "blocked", "completed"],
  checkpointed: ["active", "handoff_ready", "completed", "blocked"],
  handoff_ready: ["active", "completed", "blocked"],
  blocked: ["ready", "active", "cancelled"],
  completed: [],
  cancelled: [],
};

export function WorkOrdersView({ t, id }: { t: T; id: string }) {
  const [specs, setSpecs] = useState<VpSpecSummary[] | null>(null);
  const [rows, setRows] = useState<WorkOrderRow[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, w] = await Promise.all([api.listVpSpecs(id), api.listWorkOrders(id)]);
      setSpecs(s.vp_specs);
      setRows(w.work_orders);
      setError(null);
    } catch {
      setError(t("common.error"));
    }
  }, [id, t]);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  if (specs === null && !error) return <p className="muted">{t("common.loading")}</p>;
  if (error && specs === null) return <p className="error" role="alert">{error}</p>;

  const spec = specs && specs.length ? specs[specs.length - 1] : null;

  if (selected) {
    return <WorkOrderDetail t={t} id={id} wid={selected} onBack={() => { setSelected(null); load(); }} />;
  }

  return (
    <>
      {error && <p className="error" role="alert">{error}</p>}
      <SpecPanel t={t} id={id} spec={spec} busy={busy}
        onCreate={() => act(() => api.createVpSpec(id, "VP-4"))} />
      {spec && (
        <WorkOrderListPanel t={t} id={id} rows={rows} spec={spec} busy={busy}
          onOpen={setSelected}
          onCreate={(goal) => act(() => api.createWorkOrder(id, { vp_spec_id: spec.id, goal }))} />
      )}
    </>
  );
}

function SpecPanel({ t, id, spec, busy, onCreate }: {
  t: T; id: string; spec: VpSpecSummary | null; busy: boolean; onCreate: () => void;
}) {
  const [full, setFull] = useState<VpSpecFull | null>(null);
  useEffect(() => {
    let alive = true;
    if (spec) api.getVpSpec(id, spec.id).then((f) => alive && setFull(f)).catch(() => {});
    else setFull(null);
    return () => { alive = false; };
  }, [id, spec]);

  if (!spec) {
    return (
      <section className="panel" aria-labelledby="spec-h">
        <div className="panel-head">
          <h2 id="spec-h">{t("wo.spec.title")}</h2>
          <button className="btn-primary" disabled={busy} onClick={onCreate}>{t("wo.createSpec")}</button>
        </div>
        <div className="empty-state">
          <p>{t("wo.needApproval")}</p>
          <p className="muted">{t("wo.needApprovalHint")}</p>
        </div>
      </section>
    );
  }
  return (
    <section className="panel" aria-labelledby="spec-h">
      <div className="panel-head">
        <h2 id="spec-h">{t("wo.spec.title")}</h2>
        <span className="tbadge tb-owner" role="status">
          <span aria-hidden="true">◍</span> {t("wo.spec.version")} {spec.version}
        </span>
      </div>
      {full && <p className="na-text">{full.content.result}</p>}
      <dl className="kv">
        <dt>{t("wo.spec.hash")}</dt><dd className="mono wrap">{spec.content_hash}</dd>
        <dt>{t("wo.spec.binding")}</dt>
        <dd className="mono wrap">brief {spec.binding.brief_hash.slice(0, 22)}… · map {spec.binding.map_hash.slice(0, 14)}… · approval {spec.binding.approval_id}</dd>
        <dt>{t("wo.spec.baseline")}</dt>
        <dd className="mono">{spec.binding.baseline_branch} @ {spec.binding.baseline_head.slice(0, 12) || "—"}</dd>
      </dl>
      {full && (
        <>
          <h3 className="sub-h">{t("wo.spec.criteria")}</h3>
          <CriteriaList t={t} items={full.content.acceptance_criteria} />
          <p className="muted field-hint">{t("wo.spec.nextAction")}: {full.content.exact_next_action}</p>
        </>
      )}
    </section>
  );
}

function WorkOrderListPanel({ t, id, rows, spec, busy, onOpen, onCreate }: {
  t: T; id: string; rows: WorkOrderRow[] | null; spec: VpSpecSummary; busy: boolean;
  onOpen: (wid: string) => void; onCreate: (goal: string) => void;
}) {
  const [goal, setGoal] = useState("");
  const [decision, setDecision] = useState<OptimizerDecision | null>(null);
  const mine = (rows ?? []).filter((r) => r.vp_spec_id === spec.id);
  const submit = (e: FormEvent) => { e.preventDefault(); if (goal.trim()) { onCreate(goal.trim()); setGoal(""); } };
  const evaluate = async () => {
    const ids = mine.filter((r) => r.status === "draft" || r.status === "ready").map((r) => r.id);
    try { setDecision(await api.evaluate(id, ids.length ? ids : mine.map((r) => r.id))); }
    catch { setDecision(null); }
  };
  return (
    <section className="panel" aria-labelledby="wolist-h">
      <div className="panel-head">
        <h2 id="wolist-h">{t("wo.list.title")}</h2>
        <button className="btn" disabled={busy || mine.length === 0} onClick={evaluate}>
          {t("wo.action.evaluate")}</button>
      </div>
      {decision && (
        <div className="next-action" aria-live="polite">
          <span className="na-label">{t("wo.optimizer.decision")}</span>
          <span className="na-text">
            {t((`wo.decision.${decision.decision}`) as LocaleKey)} — {decision.exact_next_action}
          </span>
        </div>
      )}
      {mine.length === 0 ? <p className="muted">{t("wo.list.empty")}</p> : (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col">{t("wo.col.id")}</th>
              <th scope="col">{t("wo.col.role")}</th>
              <th scope="col">{t("wo.col.status")}</th>
              <th scope="col">{t("wo.col.goal")}</th>
            </tr></thead>
            <tbody>
              {mine.map((w) => (
                <tr key={w.id} className="row-click" tabIndex={0} role="link"
                    onClick={() => onOpen(w.id)}
                    onKeyDown={(e) => { if (e.key === "Enter") onOpen(w.id); }}>
                  <td className="mono">{w.id}</td>
                  <td>{w.role}</td>
                  <td><WoBadge status={w.status} t={t} /></td>
                  <td className="summary">{w.goal}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <form className="wt-form" onSubmit={submit}>
        <label className="field inline"><span>{t("wo.createGoal")}</span>
          <input value={goal} onChange={(e) => setGoal(e.target.value)} required /></label>
        <button type="submit" className="btn-primary" disabled={busy}>{t("wo.create")}</button>
      </form>
      <p className="muted field-hint">{t("wo.createHint")}</p>
    </section>
  );
}

function CriteriaList({ t, items }: { t: T; items: Criterion[] }) {
  return (
    <ul className="fact-list">
      {items.map((c) => (
        <li key={c.id} className="wo-crit">
          <span className="mono">{c.id}</span> {c.text}
          <span className="muted">
            ({c.required ? t("wo.criterion.required") : t("wo.criterion.optional")}
            {c.shared ? `, ${t("wo.criterion.shared")}` : ""})
          </span>
        </li>
      ))}
    </ul>
  );
}

function WorkOrderDetail({ t, id, wid, onBack }: {
  t: T; id: string; wid: string; onBack: () => void;
}) {
  const [wo, setWo] = useState<WorkOrderFull | null>(null);
  const [pkg, setPkg] = useState<JobPackage | null>(null);
  const [handoffs, setHandoffs] = useState<HandoffRow[]>([]);
  const [recon, setRecon] = useState<ReconstructResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const w = await api.getWorkOrder(id, wid);
      setWo(w);
      const h = await api.listHandoffs(id, wid);
      setHandoffs(h.handoffs);
      setError(null);
    } catch { setError(t("common.error")); }
  }, [id, wid, t]);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  if (!wo && !error) return <p className="muted">{t("common.loading")}</p>;
  if (!wo) return <p className="error" role="alert">{error}</p>;

  const c = wo.content;
  const transitions = NEXT[wo.status] ?? [];

  const buildAndCheckpoint = async () => {
    const cp = await api.buildCheckpoint(id, wid, { current_head: "HEAD", completed_criteria: [] });
    // если активен — перевести в checkpointed для наглядности
    return cp;
  };
  const buildHandoff = async () => {
    const cps = await api.listCheckpoints(id, wid);
    const cp = cps.checkpoints[0];
    if (cp) await api.buildHandoff(id, wid, cp.id);
  };
  const runReconstruct = async () => {
    if (handoffs[0]) setRecon(await api.reconstruct(id, handoffs[0].id));
  };

  return (
    <>
      <button className="btn back" onClick={onBack}>← {t("wo.detail.back")}</button>
      {error && <p className="error" role="alert">{error}</p>}
      <header className="page-head">
        <div>
          <h1>{wo.goal}</h1>
          <p className="muted page-desc">
            <span className="mono">{wo.id}</span> · {wo.role} · <WoBadge status={wo.status} t={t} />
          </p>
        </div>
      </header>

      <section className="next-action" aria-label={t("wo.nextAction")}>
        <span className="na-label">{t("wo.nextAction")}</span>
        <span className="na-text">{c.exact_next_action}</span>
      </section>

      <section className="panel" aria-labelledby="woa-h">
        <h2 id="woa-h">{t("wo.action.title")}</h2>
        <div className="head-actions">
          {transitions.map((to) => (
            <button key={to} className="btn btn-sm" disabled={busy}
              onClick={() => act(() => api.transitionWo(id, wid, to, wo.version))}>
              <span aria-hidden="true">{WO_META[to].sym}</span> {t((`wo.status.${to}`) as LocaleKey)}
            </button>
          ))}
        </div>
        <div className="head-actions">
          <button className="btn btn-sm" disabled={busy}
            onClick={() => act(async () => setPkg(await api.buildJobPackage(id, wid)))}>{t("wo.action.jobpackage")}</button>
          {(wo.status === "active" || wo.status === "checkpointed") && (
            <button className="btn btn-sm" disabled={busy}
              onClick={() => act(buildAndCheckpoint)}>{t("wo.action.checkpoint")}</button>
          )}
          {wo.status === "checkpointed" && (
            <button className="btn btn-sm" disabled={busy}
              onClick={() => act(buildHandoff)}>{t("wo.action.handoff")}</button>
          )}
        </div>
        <p className={wo.lease_active ? "lease lease-on" : "lease muted"}>
          <span aria-hidden="true">{wo.lease_active ? "🔒" : "○"}</span>{" "}
          {wo.lease_active ? t("wo.leaseActive") : t("wo.leaseNone")}
          {wo.writer_holder && <span className="mono muted"> · {wo.writer_holder}</span>}
        </p>
      </section>

      <div className="wo-detail-grid">
        <section className="panel" aria-labelledby="wof-h">
          <h2 id="wof-h">{t("wo.criteria")}</h2>
          <CriteriaList t={t} items={c.acceptance_criteria} />
          <h3 className="sub-h">{t("wo.checks")}</h3>
          <div className="chips">
            {c.required_checks.map((k) => <span key={k.id} className="chip mono">{k.id}</span>)}
          </div>
          <h3 className="sub-h">{t("wo.capabilities")}</h3>
          <div className="chips">
            {c.capabilities.map((k) => <span key={k} className="chip mono">{k}</span>)}
          </div>
        </section>
        <section className="panel" aria-labelledby="wos-h">
          <h2 id="wos-h">{t("wo.scope")}</h2>
          <ScopeCol title={t("wo.scope")} items={[...c.scope.files, ...c.scope.components]} />
          <ScopeCol title={t("wo.outScope")} items={c.out_of_scope} />
          <ScopeCol title={t("wo.prohibited")} items={c.prohibited_actions} />
          <ScopeCol title={t("wo.stop")} items={c.stop_conditions} />
          <dl className="kv">
            <dt>{t("wo.hash")}</dt><dd className="mono wrap">{wo.content_hash}</dd>
            <dt>{t("wo.testImpact")}</dt><dd>{c.test_impact.join(", ") || "—"}</dd>
          </dl>
        </section>
      </div>

      {pkg && (
        <section className="panel" aria-labelledby="woj-h">
          <h2 id="woj-h">{t("wo.context.title")}</h2>
          <dl className="kv">
            <dt>{t("wo.hash")}</dt><dd className="mono wrap">{pkg.content_hash}</dd>
            <dt>{t("wo.context.bytes")}</dt><dd>{pkg.byte_size} B · {t("wo.context.compact")}: {pkg.compact ? t("common.yes") : t("common.no")}</dd>
            <dt>{t("wo.context.capabilities")}</dt><dd className="mono">{pkg.capabilities.join(", ")}</dd>
            <dt>{t("wo.context.provenance")}</dt><dd className="mono wrap">{pkg.provenance.map((p) => p.source).join(", ")}</dd>
          </dl>
        </section>
      )}

      <section className="panel" aria-labelledby="woh-h">
        <h2 id="woh-h">{t("wo.handoff.title")}</h2>
        {handoffs.length === 0 ? <p className="muted">{t("wo.handoff.empty")}</p> : (
          <>
            <dl className="kv">
              <dt>{t("wo.hash")}</dt><dd className="mono wrap">{handoffs[0].content_hash}</dd>
              <dt>{t("wo.handoff.status")}</dt><dd>{handoffs[0].status}</dd>
            </dl>
            {handoffs[0].content?.acceptance_matrix && (
              <>
                <h3 className="sub-h">{t("wo.handoff.matrix")}</h3>
                <ul className="fact-list">
                  {handoffs[0].content.acceptance_matrix.map((m) => (
                    <li key={m.id} className="wo-crit">
                      <span aria-hidden="true">{m.status === "completed" ? "✓" : "○"}</span>
                      <span className="mono">{m.id}</span>
                      <span className="muted">{t((`wo.matrix.${m.status}`) as LocaleKey)}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <button className="btn" disabled={busy} onClick={() => act(runReconstruct)}>
              {t("wo.reconstruct.run")}</button>
          </>
        )}
        {recon && (
          <div className="diff-out" aria-live="polite">
            <h3 className="sub-h">{t("wo.reconstruct.title")}</h3>
            {recon.ok ? (
              <>
                <p><span aria-hidden="true">✓</span> {t("wo.reconstruct.isolated")} · {t("wo.reconstruct.valid")}
                  {recon.ack ? ` · ${t("wo.reconstruct.ack")}: ${recon.ack.result}` : ""}</p>
                <p className="muted">{t("wo.reconstruct.nextAction")}: {recon.next_action}</p>
                <p className="muted field-hint">{t("wo.reconstruct.hint")}</p>
              </>
            ) : (
              <p className="warn" role="note">
                <span aria-hidden="true">▲</span> {t("wo.reconstruct.stale")}
                {recon.rejections?.length ? ` (${recon.rejections.map((r) => r.code).join(", ")})` : ""}
              </p>
            )}
          </div>
        )}
      </section>

      <MergePanel t={t} id={id} wo={wo} />

      <section className="panel" aria-labelledby="wohist-h">
        <h2 id="wohist-h">{t("wo.history")}</h2>
        <ul className="version-list">
          {wo.history.map((h, i) => (
            <li key={i}><span className="mono">{h.from || "∅"} → {h.to}</span>
              {" "}· {h.reason} <span className="mono muted">{h.at}</span></li>
          ))}
        </ul>
      </section>
    </>
  );
}

function MergePanel({ t, id, wo }: { t: T; id: string; wo: WorkOrderFull }) {
  const [siblings, setSiblings] = useState<WorkOrderRow[]>([]);
  const [preview, setPreview] = useState<MergePreview | null>(null);
  useEffect(() => {
    api.listWorkOrders(id).then((w) =>
      setSiblings(w.work_orders.filter(
        (r) => r.vp_spec_id === wo.vp_spec_id && r.id !== wo.id
          && (r.status === "draft" || r.status === "ready")))).catch(() => {});
  }, [id, wo.vp_spec_id, wo.id]);
  if ((wo.status !== "draft" && wo.status !== "ready") || siblings.length === 0) return null;
  const other = siblings[0];
  const run = async () => {
    try { setPreview(await api.mergePreview(id, [wo.id, other.id])); } catch { setPreview(null); }
  };
  return (
    <section className="panel" aria-labelledby="wom-h">
      <h2 id="wom-h">{t("wo.optimizer.mergePreview")}</h2>
      <button className="btn" onClick={run}>
        {t("wo.optimizer.evaluate")} <span className="mono">{other.id}</span></button>
      {preview && (
        <div className="diff-out" aria-live="polite">
          <p>
            <span className={`tbadge ${preview.compatible ? "tb-verified" : "tb-stale"}`} role="status">
              <span aria-hidden="true">{preview.compatible ? "✓" : "✗"}</span>
              {preview.compatible ? t("wo.optimizer.compatible") : t("wo.optimizer.incompatible")}
            </span>{" "}
            {t("wo.optimizer.conservation")}: {preview.criterion_conservation ? t("common.yes") : t("common.no")}
          </p>
          {preview.shared_criteria.length > 0 && (
            <p className="muted">{t("wo.optimizer.shared")}: <span className="mono">{preview.shared_criteria.join(", ")}</span></p>
          )}
          <p className="muted">{t("wo.optimizer.mapping")}:</p>
          <ul className="mono">
            {Object.entries(preview.criterion_mapping).map(([k, v]) => (
              <li key={k}>{k}: {v.join(", ")}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function ScopeCol({ title, items }: { title: string; items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="scope-col">
      <h4>{title}</h4>
      <ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}
