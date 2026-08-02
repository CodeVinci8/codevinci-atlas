import { useCallback, useEffect, useState } from "react";
import {
  api, type FindingRow, type ReviewDetail, type ReviewSummary, type Severity, type Verdict,
} from "./api";
import { fmtLocal } from "./fmt";
import type { Locale, LocaleKey } from "./i18n";

type T = (key: LocaleKey) => string;

// Вердикт — цвет + символ + текст (§29.3), никогда не только цвет.
const VERDICT_SYM: Record<string, { sym: string; cls: string }> = {
  PASS: { sym: "●", cls: "st-ok" },
  REVISE: { sym: "◑", cls: "st-warn" },
  BLOCKED: { sym: "■", cls: "st-danger" },
  OWNER_REQUIRED: { sym: "◆", cls: "st-warn" },
  INVALID_EVIDENCE: { sym: "▲", cls: "st-danger" },
  "": { sym: "○", cls: "st-muted" },
};
const SEV_CLS: Record<Severity, string> = {
  blocker: "st-danger", major: "st-warn", minor: "st-info", info: "st-muted",
};

function VerdictBadge({ v, t }: { v: Verdict; t: T }) {
  const m = VERDICT_SYM[v] ?? VERDICT_SYM[""];
  const key = (v ? `quality.verdict.${v}` : "quality.verdict.none") as LocaleKey;
  return (
    <span className={`badge ${m.cls}`} role="status">
      <span aria-hidden="true">{m.sym}</span> {t(key)}
    </span>
  );
}

const VERDICTS: Verdict[] = ["PASS", "REVISE", "BLOCKED", "OWNER_REQUIRED", "INVALID_EVIDENCE"];
const SEVS: Severity[] = ["blocker", "major", "minor", "info"];

export function QualityView({ t, locale }: { t: T; locale: Locale }) {
  const [rows, setRows] = useState<ReviewSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [fVerdict, setFVerdict] = useState("");
  const [fSev, setFSev] = useState("");
  const [fFresh, setFFresh] = useState("");
  const [summary, setSummary] = useState<Record<string, number>>({});

  const refresh = useCallback(async () => {
    try {
      const r = await api.listReviews({
        verdict: fVerdict || undefined, severity: fSev || undefined,
        freshness: fFresh || undefined,
      });
      setRows(r.reviews); setSummary(r.summary); setError(null);
    } catch { setError(t("quality.state.offline")); }
  }, [t, fVerdict, fSev, fFresh]);
  useEffect(() => { refresh(); }, [refresh]);

  if (open) return <QualityDetail t={t} locale={locale} id={open} onBack={() => { setOpen(null); refresh(); }} />;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("quality.title")}</h1>
          <p className="muted page-desc">{t("quality.subtitle")}</p>
        </div>
        <button className="btn" onClick={refresh}>{t("common.refresh")}</button>
      </header>

      {Object.keys(summary).length > 0 && (
        <div className="chips q-summary">
          {Object.entries(summary).map(([v, n]) => (
            <span key={v} className="chip"><VerdictBadge v={v as Verdict} t={t} /> <b>{n}</b></span>
          ))}
        </div>
      )}

      <div className="q-filters" role="group" aria-label={t("quality.filter.verdict")}>
        <label className="field inline">
          <span>{t("quality.filter.verdict")}</span>
          <select value={fVerdict} onChange={(e) => setFVerdict(e.target.value)}>
            <option value="">{t("quality.all")}</option>
            {VERDICTS.map((v) => <option key={v} value={v}>{t((`quality.verdict.${v}`) as LocaleKey)}</option>)}
          </select>
        </label>
        <label className="field inline">
          <span>{t("quality.filter.severity")}</span>
          <select value={fSev} onChange={(e) => setFSev(e.target.value)}>
            <option value="">{t("quality.all")}</option>
            {SEVS.map((sv) => <option key={sv} value={sv}>{t((`quality.sev.${sv}`) as LocaleKey)}</option>)}
          </select>
        </label>
        <label className="field inline">
          <span>{t("quality.filter.freshness")}</span>
          <select value={fFresh} onChange={(e) => setFFresh(e.target.value)}>
            <option value="">{t("quality.all")}</option>
            <option value="FRESH">FRESH</option>
            <option value="STALE">STALE</option>
          </select>
        </label>
      </div>

      {error && <p className="error" role="alert">{error}</p>}
      {rows === null && !error && <p className="muted">{t("quality.state.loading")}</p>}
      {rows && rows.length === 0 && (
        <div className="empty-state compact">
          <p>{t("quality.empty")}</p>
          <p className="muted">{t("quality.emptyHint")}</p>
        </div>
      )}
      {rows && rows.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col">{t("quality.col.verdict")}</th>
              <th scope="col">{t("quality.col.vp")}</th>
              <th scope="col">{t("quality.col.impact")}</th>
              <th scope="col">{t("quality.col.blocking")}</th>
              <th scope="col">{t("quality.col.reviewer")}</th>
              <th scope="col">{t("quality.col.freshness")}</th>
              <th scope="col">{t("quality.col.created")}</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="row-click" tabIndex={0} role="link"
                    onClick={() => setOpen(r.id)}
                    onKeyDown={(e) => { if (e.key === "Enter") setOpen(r.id); }}>
                  <td><VerdictBadge v={r.verdict} t={t} /></td>
                  <td className="mono">{r.vp_key || "—"}</td>
                  <td>{r.impact_class
                    ? t((`quality.impact.${r.impact_class}`) as LocaleKey) : "—"}</td>
                  <td>{r.blocking_count > 0
                    ? <span className="badge st-danger">{r.blocking_count}</span>
                    : <span className="muted">0</span>}</td>
                  <td className="mono">{r.reviewer_alias || "—"}</td>
                  <td>{r.freshness === "STALE"
                    ? <span className="badge st-warn">▲ STALE</span>
                    : <span className="muted">● FRESH</span>}</td>
                  <td><time dateTime={r.created_at}>{fmtLocal(r.created_at, locale)}</time></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function QualityDetail({ t, locale, id, onBack }: {
  t: T; locale: Locale; id: string; onBack: () => void;
}) {
  const [d, setD] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setD(await api.getReview(id)); setError(null); }
    catch { setError(t("quality.state.error")); }
  }, [id, t]);
  useEffect(() => { load(); }, [load]);

  if (error) return (<><button className="btn back" onClick={onBack}>{t("quality.detail.back")}</button>
    <p className="error" role="alert">{error}</p></>);
  if (!d) return (<><button className="btn back" onClick={onBack}>{t("quality.detail.back")}</button>
    <p className="muted">{t("quality.state.loading")}</p></>);

  const pkg = d.package;
  const rep = d.report;
  const invalid = pkg.status === "invalid";
  const verdict = (rep?.verdict ?? "") as Verdict;

  const act = async (fn: () => Promise<void>) => {
    setMsg(null);
    try { await fn(); await load(); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <>
      <button className="btn back" onClick={onBack}>{t("quality.detail.back")}</button>
      <header className="page-head">
        <div>
          <h1><VerdictBadge v={verdict} t={t} /> <span className="mono">{pkg.vp_key || pkg.id}</span></h1>
          <p className="muted page-desc">
            {t("quality.reviewer")}: <span className="mono">{d.reviewer_alias || "—"}</span>
          </p>
        </div>
      </header>
      {msg && <p className="error" role="alert">{msg}</p>}

      {/* QualityReport — объяснение решения, above fold */}
      {rep && (
        <section className={`panel na-glow ${invalid ? "panel-danger" : ""}`} aria-labelledby="qr-h">
          <h2 id="qr-h">{t("quality.report")}</h2>
          <div className="next-action">
            <span className="na-label">{t("quality.reportNext")}</span>
            <span className="na-text">{rep.next_action}</span>
          </div>
          <dl className="kv">
            <dt>{t("quality.reportGate")}</dt><dd className="mono">{rep.gate_fired}</dd>
            <dt>{t("quality.reportEvidence")}</dt><dd>{rep.evidence_summary}</dd>
            <dt>{t("quality.reportSufficiency")}</dt><dd>{rep.sufficiency_reason}</dd>
            <dt>{t("quality.reportStop")}</dt><dd className="muted">{rep.stop_reason}</dd>
          </dl>
        </section>
      )}

      {/* ReviewPackage — SHA-bound */}
      <section className="panel" aria-labelledby="rp-h">
        <h2 id="rp-h">{t("quality.package")}</h2>
        {invalid && <p className="warn" role="note">{t("quality.state.invalid")}: {pkg.invalid_code} — {pkg.invalid_reason}</p>}
        <div className="stat-row">
          <Stat label={t("quality.branch")} value={pkg.branch || "—"} mono />
          <Stat label={t("quality.head")} value={(pkg.head_sha || "—").slice(0, 12)} mono />
          <Stat label={t("quality.col.impact")} value={pkg.impact_class
            ? t((`quality.impact.${pkg.impact_class}`) as LocaleKey) : "—"} />
        </div>
        <dl className="kv">
          <dt>{t("quality.hash")}</dt><dd className="mono wrap">{pkg.content_hash}</dd>
        </dl>
        {d.impact && (
          <p className="muted field-hint">
            {t("quality.impactGroups")}: <span className="mono">{d.impact.check_groups.join(", ")}</span>
            {" · "}{t("quality.fullReg")}: {d.impact.full_regression ? t("common.yes") : t("common.no")}
            {d.impact.risk_trigger && <> · {t("quality.riskTrigger")}: {d.impact.risk_trigger}</>}
          </p>
        )}
      </section>

      {/* Claim vs evidence */}
      {pkg.claims.length > 0 && (
        <section className="panel" aria-labelledby="cl-h">
          <h2 id="cl-h">{t("quality.claimVsEvidence")}</h2>
          <ul className="claim-list">
            {pkg.claims.map((c, i) => <li key={i}>{c.claim ?? JSON.stringify(c)}</li>)}
          </ul>
        </section>
      )}

      {/* Findings — blocking first */}
      <section className="panel" aria-labelledby="fn-h">
        <h2 id="fn-h">{t("quality.findings")}</h2>
        {d.findings.length === 0 ? <p className="muted">{t("quality.noFindings")}</p> : (
          <ul className="finding-list">
            {d.findings.map((f) => <FindingItem key={f.id} f={f} t={t} rid={id} onAct={act} />)}
          </ul>
        )}
      </section>

      {/* Evidence Cache reuse */}
      <section className="panel" aria-labelledby="cc-h">
        <h2 id="cc-h">{t("quality.cacheReuse")}</h2>
        {d.cache_reuse.length === 0 ? <p className="muted">{t("quality.noCacheReuse")}</p> : (
          <ul className="claim-list">
            {d.cache_reuse.map((c, i) => <li key={i} className="mono">{c.command} — {c.cache}</li>)}
          </ul>
        )}
      </section>

      {/* Manual audit */}
      <section className="panel" aria-labelledby="ma-h">
        <h2 id="ma-h">{t("quality.manualAudit")}</h2>
        <p className="muted field-hint">{t("quality.audit.readonly")}</p>
        <button className="btn" onClick={() => act(async () => {
          await api.manualAudit(id, { target: "diff", scope: pkg.branch || pkg.id, note: "" });
        })}>{t("quality.audit.create")}</button>
        {d.manual_audits.length === 0 ? <p className="muted">{t("quality.audit.empty")}</p> : (
          <ul className="claim-list">
            {d.manual_audits.map((a) => (
              <li key={a.id}><span className="mono">{a.target}</span> · {a.scope}
                {" · "}<span className="badge st-ok">✓ {t("quality.audit.readonly")}</span>
                {" · "}<time dateTime={a.created_at}>{fmtLocal(a.created_at, locale)}</time></li>
            ))}
          </ul>
        )}
      </section>

      {/* Waivers */}
      <section className="panel" aria-labelledby="wv-h">
        <h2 id="wv-h">{t("quality.waiver")}</h2>
        {d.waivers.length === 0 ? <p className="muted">{t("quality.waiver.empty")}</p> : (
          <ul className="claim-list">
            {d.waivers.map((w) => (
              <li key={w.id}>
                {w.waivable
                  ? <span className="badge st-ok">✓ {w.scope}</span>
                  : <span className="badge st-danger">■ {t("quality.waiver.nonWaivable")}</span>}
                {" "}{w.reason}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function FindingItem({ f, t, rid, onAct }: {
  f: FindingRow; t: T; rid: string; onAct: (fn: () => Promise<void>) => void;
}) {
  const [waiverOpen, setWaiverOpen] = useState(false);
  const [reason, setReason] = useState("");
  return (
    <li className={`finding sev-${f.severity} ${f.blocking ? "finding-blocking" : ""} ${f.waived ? "finding-waived" : ""}`}>
      <div className="finding-head">
        <span className={`badge ${SEV_CLS[f.severity]}`}>
          {t((`quality.sev.${f.severity}`) as LocaleKey)}
        </span>
        {f.blocking && <span className="badge st-danger">{t("quality.blocking")}</span>}
        {f.waived && <span className="badge st-muted">{t("quality.waived")}</span>}
        <span className="finding-crit">{f.criterion}</span>
        <span className="mono muted finding-code">{f.code}</span>
      </div>
      <dl className="kv finding-kv">
        <dt>{t("quality.finding.location")}</dt><dd className="mono wrap">{f.location || "—"}</dd>
        <dt>{t("quality.finding.evidence")}</dt><dd className="wrap">{f.evidence}</dd>
        <dt>{t("quality.finding.action")}</dt><dd>{f.action}</dd>
        <dt>{t("quality.finding.source")}</dt>
        <dd className="mono">{f.source} · {f.freshness}</dd>
      </dl>
      {f.blocking && !f.waived && (
        <div className="finding-actions">
          <button className="btn" onClick={() => onAct(async () => {
            await api.createFixWorkOrder(rid, { finding_id: f.id });
          })}>{t("quality.fixWo.create")}</button>
          <button className="btn" onClick={() => setWaiverOpen((v) => !v)}>{t("quality.waiver.create")}</button>
          {waiverOpen && (
            <div className="waiver-form">
              <input placeholder={t("quality.waiver.reason")} value={reason}
                     onChange={(e) => setReason(e.target.value)} />
              <button className="btn-primary" onClick={() => onAct(async () => {
                await api.createWaiver(rid, {
                  finding_id: f.id, reason: reason || "—", scope: f.criterion,
                  expiry: "review", review_condition: "next-review",
                });
              })}>OK</button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="stat">
      <span className="label">{label}</span>
      <span className={mono ? "stat-val mono" : "stat-val"}>{value}</span>
    </div>
  );
}
