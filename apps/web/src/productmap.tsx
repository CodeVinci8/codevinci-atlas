// VP-3 Product Map UI (Master Spec §36). Ember-направление, RU/EN, a11y.
// Truth-status и решения — не только цветом: символ + текст + класс.
import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  api, type BriefDiff, type DecisionRow, type PortfolioRow, type ProductState,
  type TruthStatus,
} from "./api";
import { type LocaleKey } from "./i18n";

type T = (key: LocaleKey) => string;

const TRUTH_META: Record<TruthStatus, { sym: string; cls: string }> = {
  VERIFIED: { sym: "✓", cls: "tb-verified" },
  OWNER_PROVIDED: { sym: "◍", cls: "tb-owner" },
  INFERRED: { sym: "◇", cls: "tb-inferred" },
  HYPOTHESIS: { sym: "?", cls: "tb-hypo" },
  STALE: { sym: "⌛", cls: "tb-stale" },
  UNKNOWN: { sym: "—", cls: "tb-unknown" },
};

function TruthBadge({ status, t }: { status: TruthStatus; t: T }) {
  const m = TRUTH_META[status] ?? TRUTH_META.UNKNOWN;
  return (
    <span className={`tbadge ${m.cls}`} role="status">
      <span aria-hidden="true">{m.sym}</span> {t((`truth.${status}`) as LocaleKey)}
    </span>
  );
}

function DecisionBadge({ status, t }: { status: DecisionRow["status"]; t: T }) {
  const sym = status === "accepted" ? "✓" : status === "rejected" ? "✗" : "○";
  const cls = status === "accepted" ? "db-acc" : status === "rejected" ? "db-rej" : "db-prop";
  return (
    <span className={`tbadge ${cls}`} role="status">
      <span aria-hidden="true">{sym}</span> {t((`decision.${status}`) as LocaleKey)}
    </span>
  );
}

function unk(v: string, t: T): string {
  return v === "UNKNOWN" ? t("truth.UNKNOWN") : v;
}

function stageLabel(stage: string, t: T): string {
  if (stage === "intake_pending" || stage === "draft" || stage === "approved") {
    return t((`stage.${stage}`) as LocaleKey);
  }
  return stage;
}

// --- Portfolio Map ---------------------------------------------------------
export function PortfolioView({ t, onOpen }: { t: T; onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<PortfolioRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setRows((await api.portfolio()).projects); setError(null); }
    catch { setError(t("common.error")); }
  }, [t]);
  useEffect(() => { refresh(); }, [refresh]);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("portfolio.title")}</h1>
          <p className="muted page-desc">{t("portfolio.subtitle")}</p>
        </div>
        <button className="btn" onClick={refresh}>{t("common.refresh")}</button>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {rows === null && !error && <p className="muted">{t("common.loading")}</p>}
      {rows && rows.length === 0 && <div className="empty-state"><p>{t("portfolio.empty")}</p></div>}
      {rows && rows.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col">{t("portfolio.col.project")}</th>
              <th scope="col">{t("portfolio.col.stage")}</th>
              <th scope="col">{t("portfolio.col.activeVp")}</th>
              <th scope="col">{t("portfolio.col.state")}</th>
              <th scope="col">{t("portfolio.col.blocker")}</th>
              <th scope="col">{t("portfolio.col.truth")}</th>
              <th scope="col">{t("portfolio.col.next")}</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.project_id} className="row-click" tabIndex={0} role="link"
                    onClick={() => onOpen(r.project_id)}
                    onKeyDown={(e) => { if (e.key === "Enter") onOpen(r.project_id); }}>
                  <td><span className="link-like">{r.name}</span></td>
                  <td>{stageLabel(r.stage, t)}</td>
                  <td className="mono">{unk(r.active_vp, t)}</td>
                  <td>{unk(r.last_known_state, t)}</td>
                  <td>{unk(r.blocker, t)}</td>
                  <td>{unk(r.truth_state, t)}</td>
                  <td className="summary">{r.next_action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// --- Product Map (per project) --------------------------------------------
export function ProductMapView({ t, id }: { t: T; id: string }) {
  const [st, setSt] = useState<ProductState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notAvail, setNotAvail] = useState(false);

  const load = useCallback(async () => {
    try { setSt(await api.productState(id)); setError(null); setNotAvail(false); }
    catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("PROJECT_NOT_AVAILABLE")) setNotAvail(true);
      else setError(t("common.error"));
    }
  }, [id, t]);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  if (notAvail) return <section className="panel"><p className="muted">{t("pm.notAvailable")}</p></section>;
  if (!st && !error) return <p className="muted">{t("common.loading")}</p>;
  if (!st) return <p className="error" role="alert">{error}</p>;

  return (
    <>
      {error && <p className="error" role="alert">{error}</p>}
      <section className="next-action" aria-label={t("pm.nextAction")}>
        <span className="na-label">{t("pm.nextAction")}</span>
        <span className="na-text">{st.next_action}</span>
      </section>

      <div className="pm-meta">
        <span className="chip"><span className="muted">{t("pm.stageLabel")}:</span> {stageLabel(st.stage, t)}</span>
        <span className="chip">
          <span className="muted">{t("pm.activeVp")}:</span>{" "}
          {st.active_vp ?? <em className="muted">{t("pm.noActiveVp")}</em>}
        </span>
        {!st.active_vp && st.brief && (
          <button className="btn" disabled={busy} onClick={() => {
            const k = window.prompt(t("pm.vp.activatePrompt"), "VP-3");
            if (k) act(() => api.activateVp(id, k));
          }}>{t("pm.vp.activate")}</button>
        )}
      </div>

      {!st.brief ? (
        <IntakeForm t={t} busy={busy} onSubmit={(body) => act(() => api.submitIntake(id, body))} />
      ) : (
        <>
          <BriefPanel t={t} id={id} st={st} busy={busy} act={act} />
          <DecisionsPanel t={t} id={id} st={st} busy={busy} act={act} />
          <ParkingPanel t={t} id={id} st={st} busy={busy} act={act} />
          <MapPanel t={t} st={st} />
          <VersionsPanel t={t} id={id} st={st} />
          <ExportPanel t={t} id={id} />
        </>
      )}
    </>
  );
}

function splitLines(v: string): string[] {
  return v.split("\n").map((x) => x.trim()).filter(Boolean);
}

function IntakeForm({ t, busy, onSubmit }: {
  t: T; busy: boolean; onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [idea, setIdea] = useState("");
  const [user, setUser] = useState("");
  const [result, setResult] = useState("");
  const [constraints, setConstraints] = useState("");
  const [risks, setRisks] = useState("");
  const [links, setLinks] = useState("");
  const [refs, setRefs] = useState("");
  const [parking, setParking] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({
      idea, target_user: user, desired_result: result,
      constraints: splitLines(constraints), risks: splitLines(risks),
      links: splitLines(links), baseline_refs: splitLines(refs),
      parking_suggestions: splitLines(parking),
    });
  };
  return (
    <section className="panel form-panel" aria-labelledby="intake-h">
      <h2 id="intake-h">{t("pm.intake.title")}</h2>
      <p className="muted field-hint">{t("pm.intake.desc")}</p>
      <form onSubmit={submit}>
        <label className="field"><span>{t("pm.intake.idea")}</span>
          <textarea value={idea} onChange={(e) => setIdea(e.target.value)} rows={2} required /></label>
        <label className="field"><span>{t("pm.intake.targetUser")}</span>
          <input value={user} onChange={(e) => setUser(e.target.value)} /></label>
        <label className="field"><span>{t("pm.intake.desiredResult")}</span>
          <textarea value={result} onChange={(e) => setResult(e.target.value)} rows={2} /></label>
        <label className="field"><span>{t("pm.intake.constraints")}</span>
          <textarea value={constraints} onChange={(e) => setConstraints(e.target.value)} rows={2} /></label>
        <label className="field"><span>{t("pm.intake.risks")}</span>
          <textarea value={risks} onChange={(e) => setRisks(e.target.value)} rows={2} /></label>
        <label className="field"><span>{t("pm.intake.links")}</span>
          <textarea className="mono" value={links} onChange={(e) => setLinks(e.target.value)} rows={2} /></label>
        <label className="field"><span>{t("pm.intake.baselineRefs")}</span>
          <input className="mono" value={refs} onChange={(e) => setRefs(e.target.value)} /></label>
        <label className="field"><span>{t("pm.intake.parking")}</span>
          <textarea value={parking} onChange={(e) => setParking(e.target.value)} rows={2} /></label>
        <p className="muted field-hint">{t("pm.intake.listHint")} · {t("pm.intake.hint")}</p>
        <div className="form-actions">
          <button type="submit" className="btn-primary" disabled={busy}>{t("pm.intake.submit")}</button>
        </div>
      </form>
    </section>
  );
}

const REVISE_FIELDS: { field: string; key: LocaleKey }[] = [
  { field: "product_statement", key: "pm.brief.product" },
  { field: "promised_result", key: "pm.brief.promised" },
  { field: "main_scenario", key: "pm.brief.scenario" },
  { field: "success_metric", key: "pm.brief.metric" },
  { field: "minimum_validation", key: "pm.brief.validation" },
  { field: "stop_criterion", key: "pm.brief.stop" },
];

function BriefPanel({ t, id, st, busy, act }: {
  t: T; id: string; st: ProductState; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const b = st.brief!;
  const c = b.content;
  const approved = b.status === "approved";
  const [reviseOpen, setReviseOpen] = useState(false);
  const [field, setField] = useState(REVISE_FIELDS[0].field);
  const [value, setValue] = useState("");

  const doRevise = (e: FormEvent) => {
    e.preventDefault();
    act(() => api.reviseBrief(id, b.id, { [field]: value }, b.version)).then(() => {
      setReviseOpen(false); setValue("");
    });
  };

  const row = (label: string, v: string) => v ? (
    <><dt>{label}</dt><dd>{v}</dd></>
  ) : null;

  return (
    <section className="panel" aria-labelledby="brief-h">
      <div className="panel-head">
        <h2 id="brief-h">{t("pm.brief.title")}</h2>
        <div className="head-actions">
          <span className={`tbadge ${approved ? "db-acc" : "db-prop"}`} role="status">
            <span aria-hidden="true">{approved ? "✓" : "○"}</span>{" "}
            {t("pm.brief.version")} {b.version}
            {approved ? ` · ${t("pm.brief.approved")}` : ""}
          </span>
          <button className="btn" disabled={busy} onClick={() => setReviseOpen((x) => !x)}>{t("pm.brief.revise")}</button>
          {!approved && (
            <button className="btn-primary" disabled={busy}
              onClick={() => act(() => api.approveBrief(id, b.id, b.version))}>{t("pm.brief.approve")}</button>
          )}
        </div>
      </div>
      <p className="muted field-hint">{t("pm.brief.approveHint")}</p>
      <dl className="kv">
        {row(t("pm.brief.product"), c.product_statement)}
        {row(t("pm.brief.userProblem"), c.user_and_problem)}
        {row(t("pm.brief.alternative"), c.current_alternative)}
        {row(t("pm.brief.promised"), c.promised_result)}
        {row(t("pm.brief.scenario"), c.main_scenario)}
        {row(t("pm.brief.metric"), c.success_metric)}
        {row(t("pm.brief.validation"), c.minimum_validation)}
        {row(t("pm.brief.stop"), c.stop_criterion)}
        <dt>{t("pm.brief.hash")}</dt><dd className="mono wrap">{b.content_hash}</dd>
      </dl>

      <h3 className="sub-h">{t("pm.brief.facts")}</h3>
      {c.confirmed_facts.length === 0 ? <p className="muted">{t("pm.brief.noFacts")}</p> : (
        <ul className="fact-list">
          {c.confirmed_facts.map((f, i) => (
            <li key={i}><TruthBadge status={f.truth_status} t={t} /> {f.text}
              {f.evidence_ref && <span className="mono muted"> · {f.evidence_ref}</span>}</li>
          ))}
        </ul>
      )}
      {c.hypotheses.length > 0 && (
        <>
          <h3 className="sub-h">{t("pm.brief.hypotheses")}</h3>
          <ul className="fact-list">
            {c.hypotheses.map((h, i) => (
              <li key={i}><TruthBadge status={h.truth_status} t={t} /> {h.text}</li>
            ))}
          </ul>
        </>
      )}

      <div className="scope-grid">
        <ScopeList title={t("pm.brief.mvp")} items={c.mvp_scope} />
        <ScopeList title={t("pm.brief.outScope")} items={c.out_of_scope} />
        <ScopeList title={t("pm.brief.risks")} items={c.risks} />
      </div>

      <h3 className="sub-h">{t("pm.envelope.title")}</h3>
      <div className="scope-grid">
        <ScopeList title={t("pm.envelope.inScope")} items={b.envelope.in_scope} />
        <ScopeList title={t("pm.envelope.outScope")} items={b.envelope.out_of_scope} />
        <ScopeList title={t("pm.envelope.constraints")} items={b.envelope.constraints} />
      </div>
      {b.envelope.boundary_note && (
        <p className="muted field-hint">{t("pm.envelope.note")}: {b.envelope.boundary_note}</p>
      )}

      {reviseOpen && (
        <form className="revise-form" onSubmit={doRevise} aria-label={t("pm.revise.title")}>
          <h3 className="sub-h">{t("pm.revise.title")}</h3>
          <p className="muted field-hint">{t("pm.revise.hint")}</p>
          <label className="field inline"><span>{t("pm.revise.field")}</span>
            <select value={field} onChange={(e) => setField(e.target.value)}>
              {REVISE_FIELDS.map((r) => <option key={r.field} value={r.field}>{t(r.key)}</option>)}
            </select></label>
          <label className="field"><span>{t("pm.revise.value")}</span>
            <textarea value={value} onChange={(e) => setValue(e.target.value)} rows={2} required /></label>
          <div className="form-actions">
            <button type="submit" className="btn-primary" disabled={busy}>{t("pm.revise.submit")}</button>
            <button type="button" className="btn" onClick={() => setReviseOpen(false)}>{t("common.cancel")}</button>
          </div>
        </form>
      )}
    </section>
  );
}

function ScopeList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="scope-col">
      <h4>{title}</h4>
      {items.length === 0 ? <p className="muted">—</p> : (
        <ul>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
      )}
    </div>
  );
}

function DecisionsPanel({ t, id, st, busy, act }: {
  t: T; id: string; st: ProductState; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  return (
    <section className="panel" aria-labelledby="dec-h">
      <h2 id="dec-h">{t("pm.decisions.title")}</h2>
      {st.decisions.length === 0 ? <p className="muted">{t("pm.decisions.empty")}</p> : (
        <ul className="decision-list">
          {st.decisions.map((d) => (
            <DecisionItem key={d.id} d={d} t={t} id={id} busy={busy} act={act} />
          ))}
        </ul>
      )}
    </section>
  );
}

function DecisionItem({ d, t, id, busy, act }: {
  d: DecisionRow; t: T; id: string; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [rejectOpen, setRejectOpen] = useState(false);
  const [note, setNote] = useState("");
  return (
    <li className="decision-item">
      <div className="decision-head">
        <div>
          <strong>{d.title}</strong>{" "}
          <span className="muted">({t(d.required ? "pm.decisions.required" : "pm.decisions.optional")})</span>
          <div className="muted decision-detail">{d.detail}</div>
          {d.note && <div className="muted">{t("pm.decisions.note")}: {d.note}</div>}
        </div>
        <div className="decision-actions">
          <DecisionBadge status={d.status} t={t} />
          <TruthBadge status={d.truth_status} t={t} />
          {d.status !== "accepted" && (
            <button className="btn btn-sm" disabled={busy}
              onClick={() => act(() => api.decide(id, d.id, "accept", "", d.version))}>{t("pm.decisions.accept")}</button>
          )}
          {d.status !== "rejected" && (
            <button className="btn btn-sm" disabled={busy}
              onClick={() => setRejectOpen((x) => !x)}>{t("pm.decisions.reject")}</button>
          )}
        </div>
      </div>
      {rejectOpen && (
        <div className="reject-box">
          <label className="field"><span>{t("pm.decisions.notePrompt")}</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} /></label>
          <button className="btn btn-sm" disabled={busy}
            onClick={() => act(() => api.decide(id, d.id, "reject", note, d.version)).then(() => setRejectOpen(false))}>
            {t("pm.decisions.reject")}</button>
          <button className="btn btn-sm" onClick={() => setRejectOpen(false)}>{t("common.cancel")}</button>
        </div>
      )}
    </li>
  );
}

function ParkingPanel({ t, id, st, busy, act }: {
  t: T; id: string; st: ProductState; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [reason, setReason] = useState("");
  const [ret, setRet] = useState("");
  const add = (e: FormEvent) => {
    e.preventDefault();
    act(() => api.addParking(id, { title, reason, return_condition: ret })).then(() => {
      setTitle(""); setReason(""); setRet("");
    });
  };
  return (
    <section className="panel" aria-labelledby="park-h">
      <h2 id="park-h">{t("pm.parking.title")}</h2>
      <p className="muted field-hint">{t("pm.parking.note")}</p>
      {st.parking_lot.length === 0 ? <p className="muted">{t("pm.parking.empty")}</p> : (
        <ul className="parking-list">
          {st.parking_lot.map((p) => (
            <li key={p.id}>
              <strong>{p.title}</strong>
              {p.reason && <span className="muted"> — {t("pm.parking.reason")}: {p.reason}</span>}
              {p.return_condition && <span className="muted"> · {t("pm.parking.return")}: {p.return_condition}</span>}
            </li>
          ))}
        </ul>
      )}
      <form className="park-form" onSubmit={add}>
        <label className="field inline"><span>{t("pm.parking.addTitle")}</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} required /></label>
        <label className="field inline"><span>{t("pm.parking.reason")}</span>
          <input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <label className="field inline"><span>{t("pm.parking.return")}</span>
          <input value={ret} onChange={(e) => setRet(e.target.value)} /></label>
        <button type="submit" className="btn" disabled={busy}>{t("pm.parking.add")}</button>
      </form>
    </section>
  );
}

function MapPanel({ t, st }: { t: T; st: ProductState }) {
  const m = st.map;
  return (
    <section className="panel" aria-labelledby="map-h">
      <h2 id="map-h">{t("pm.map.title")}</h2>
      {!m ? <p className="muted">{t("pm.map.empty")}</p> : (
        <>
          <p className="muted">{t("pm.map.version")}: <span className="mono">v{m.version}</span> ·{" "}
            <span className="mono wrap">{m.content_hash}</span></p>
          <h3 className="sub-h">{t("pm.map.nodes")}</h3>
          <ul className="node-list">
            {m.nodes.map((n) => (
              <li key={n.node_key}>
                <span className="node-type">{t((`node.${n.node_type}`) as LocaleKey)}</span>
                <strong>{n.title}</strong> <TruthBadge status={n.truth_status} t={t} />
              </li>
            ))}
          </ul>
          <h3 className="sub-h">{t("pm.map.edges")}</h3>
          <ul className="edge-list mono">
            {m.edges.map((e) => (
              <li key={e.edge_id}>{e.src_key} —{t((`edge.${e.edge_type}`) as LocaleKey)}→ {e.dst_key}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function VersionsPanel({ t, id, st }: { t: T; id: string; st: ProductState }) {
  const versions = st.brief_versions;
  const [from, setFrom] = useState(versions[0]?.version ?? 1);
  const [to, setTo] = useState(versions[versions.length - 1]?.version ?? 1);
  const [diff, setDiff] = useState<BriefDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  const compare = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try { setDiff(await api.briefDiff(id, from, to)); }
    catch (e2) { setError(e2 instanceof Error ? e2.message : String(e2)); }
  };

  const changedKeys = diff ? Object.keys(diff.content.changed) : [];
  const addedKeys = diff ? Object.keys(diff.content.added) : [];
  const removedKeys = diff ? Object.keys(diff.content.removed) : [];
  const noDiff = diff && !changedKeys.length && !addedKeys.length && !removedKeys.length;

  return (
    <section className="panel" aria-labelledby="ver-h">
      <h2 id="ver-h">{t("pm.versions.title")}</h2>
      <ul className="version-list">
        {versions.map((v) => (
          <li key={v.id}>
            <span className="mono">v{v.version}</span> · {v.status} ·{" "}
            <span className="mono muted">{v.content_hash.slice(0, 22)}…</span>
          </li>
        ))}
      </ul>
      {versions.length > 1 && (
        <form className="diff-form" onSubmit={compare}>
          <label className="field inline"><span>{t("pm.versions.from")}</span>
            <select value={from} onChange={(e) => setFrom(Number(e.target.value))}>
              {versions.map((v) => <option key={v.id} value={v.version}>v{v.version}</option>)}
            </select></label>
          <label className="field inline"><span>{t("pm.versions.to")}</span>
            <select value={to} onChange={(e) => setTo(Number(e.target.value))}>
              {versions.map((v) => <option key={v.id} value={v.version}>v{v.version}</option>)}
            </select></label>
          <button type="submit" className="btn">{t("pm.versions.compare")}</button>
        </form>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {diff && (
        <div className="diff-out" aria-live="polite">
          <h3 className="sub-h">{t("pm.diff.title")} (v{diff.from} → v{diff.to})</h3>
          {noDiff ? <p className="muted">{t("pm.diff.none")}</p> : (
            <>
              {changedKeys.length > 0 && (
                <div><strong>{t("pm.diff.changed")}:</strong>
                  <ul className="mono">{changedKeys.map((k) => (
                    <li key={k}>{k}: <span className="diff-from">{JSON.stringify(diff.content.changed[k].from)}</span>
                      {" → "}<span className="diff-to">{JSON.stringify(diff.content.changed[k].to)}</span></li>
                  ))}</ul></div>
              )}
              {addedKeys.length > 0 && (
                <div><strong>{t("pm.diff.added")}:</strong> <span className="mono">{addedKeys.join(", ")}</span></div>
              )}
              {removedKeys.length > 0 && (
                <div><strong>{t("pm.diff.removed")}:</strong> <span className="mono">{removedKeys.join(", ")}</span></div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

function ExportPanel({ t, id }: { t: T; id: string }) {
  return (
    <section className="panel" aria-labelledby="exp-h">
      <h2 id="exp-h">{t("pm.export.title")}</h2>
      <p className="muted field-hint">{t("pm.export.note")}</p>
      <div className="form-actions">
        <a className="btn" href={api.exportUrl(id, "json")} download={`product-map-${id}.json`}>
          {t("common.download")} {t("pm.export.json")}</a>
        <a className="btn" href={api.exportUrl(id, "md")} download={`product-map-${id}.md`}>
          {t("common.download")} {t("pm.export.md")}</a>
      </div>
    </section>
  );
}
