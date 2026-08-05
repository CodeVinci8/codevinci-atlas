import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  api, type AutonomyMode, type AutonomySummary, type GithubDelivery, type Grant,
} from "./api";
import type { Locale, LocaleKey } from "./i18n";
import { fmtLocal } from "./fmt";

type T = (key: LocaleKey) => string;

const MODES: AutonomyMode[] = ["GUIDED", "STANDARD", "AUTONOMOUS", "TRUSTED"];

const GRANT_STATE: Record<string, { sym: string; cls: string; key: LocaleKey }> = {
  ACTIVE: { sym: "●", cls: "st-ok", key: "status.READY" },
  EXPIRED: { sym: "◇", cls: "st-warn", key: "st.expired" },
  REVOKED: { sym: "■", cls: "st-danger", key: "st.revoked" },
};

function GrantStateBadge({ state, t }: { state: string; t: T }) {
  const m = GRANT_STATE[state] ?? { sym: "○", cls: "st-muted", key: "status.UNKNOWN" as LocaleKey };
  return (
    <span className={`badge ${m.cls}`} role="status">
      <span aria-hidden="true">{m.sym}</span> {t(m.key)}
    </span>
  );
}

export function AutonomyView({ t, locale }: { t: T; locale: Locale }) {
  const [data, setData] = useState<AutonomySummary | null>(null);
  const [deliveries, setDeliveries] = useState<GithubDelivery[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([api.autonomySummary(), api.listDeliveries()]);
      setData(s);
      setDeliveries(d.deliveries);
      setError(null);
    } catch {
      setError(t("common.error"));
    }
  }, [t]);
  useEffect(() => { refresh(); }, [refresh]);

  const emergency = data?.emergency;
  const activeGrants = (data?.grants ?? []).filter((g) => g.state === "ACTIVE");

  const engage = async () => {
    setBusy(true);
    try { await api.emergencyEngage("Owner-инициированный аварийный стоп"); await refresh(); }
    finally { setBusy(false); }
  };
  const resume = async () => {
    setBusy(true);
    try { await api.emergencyResume(); await refresh(); }
    finally { setBusy(false); }
  };
  const revoke = async (g: Grant) => {
    if (!window.confirm(t("auto.confirmRevoke"))) return;
    setBusy(true);
    try { await api.revokeGrant(g.id, g.version, "Отозвано владельцем"); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("auto.title")}</h1>
          <p className="muted page-desc">{t("auto.subtitle")}</p>
        </div>
        <div className="head-actions">
          <button className="btn" onClick={refresh}>{t("common.refresh")}</button>
          <button className="btn-primary" onClick={() => setShowForm(true)}>{t("auto.createGrant")}</button>
        </div>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {data === null && !error && <p className="muted">{t("common.loading")}</p>}

      {/* Emergency Stop — самое важное сверху */}
      {emergency && (
        <section className={`panel ${emergency.active ? "estop-on na-glow" : ""}`} aria-labelledby="es-h">
          <div className="hero-head">
            <h2 id="es-h">{t("auto.emergency")}</h2>
            <span className={`badge ${emergency.active ? "st-danger" : "st-ok"}`} role="status">
              <span aria-hidden="true">{emergency.active ? "■" : "●"}</span>{" "}
              {t(emergency.active ? "auto.emergencyActive" : "auto.emergencyInactive")}
            </span>
          </div>
          <p className="muted field-hint">{t("auto.emergencyHint")}</p>
          {emergency.active && (
            <dl className="kv">
              <dt>{t("auto.interrupted")}</dt><dd className="mono">{emergency.interrupted_runs.length}</dd>
              <dt>{t("auto.released")}</dt><dd className="mono">{emergency.released_leases.length}</dd>
              {emergency.since && (<><dt>{t("tm.col.created")}</dt>
                <dd><time dateTime={emergency.since} title={`${emergency.since} UTC`}>{fmtLocal(emergency.since, locale)}</time></dd></>)}
            </dl>
          )}
          <div className="form-actions">
            {emergency.active
              ? <button className="btn-primary" disabled={busy} onClick={resume}>{t("auto.resume")}</button>
              : <button className="btn btn-danger" disabled={busy} onClick={engage}>{t("auto.engage")}</button>}
          </div>
        </section>
      )}

      {/* Режимы автономии */}
      <section className="panel" aria-labelledby="modes-h">
        <h2 id="modes-h">{t("auto.modes")}</h2>
        <div className="mode-grid">
          {MODES.map((m) => (
            <div key={m} className="mode-card">
              <span className="mode-name mono">{m}</span>
              <span className="muted">{t((`auto.modeDesc.${m}`) as LocaleKey)}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Гранты */}
      {showForm && <CreateGrantForm t={t} onClose={() => setShowForm(false)}
        onDone={() => { setShowForm(false); refresh(); }} />}
      <section className="panel" aria-labelledby="grants-h">
        <div className="hero-head">
          <h2 id="grants-h">{t("auto.grants")}</h2>
          <span className="muted">{t("auto.scope")}: {activeGrants.length} / {data?.grants.length ?? 0}</span>
        </div>
        {data && data.grants.length === 0 && <p className="muted">{t("auto.noGrants")}</p>}
        {data && data.grants.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr>
                <th scope="col">{t("auto.state")}</th>
                <th scope="col">{t("auto.capabilities")}</th>
                <th scope="col">{t("auto.scope")}</th>
                <th scope="col">{t("auto.budget")}</th>
                <th scope="col">{t("auto.expires")}</th>
                <th scope="col">{t("auto.reason")}</th>
                <th scope="col"></th>
              </tr></thead>
              <tbody>
                {data.grants.map((g) => (
                  <tr key={g.id}>
                    <td><GrantStateBadge state={g.state} t={t} />
                      <div className="mono small muted">{g.mode}</div></td>
                    <td>
                      <div className="chips">
                        {g.capabilities.map((c) => <span key={c} className="chip mono">{c}</span>)}
                      </div>
                    </td>
                    <td className="mono small">
                      {(g.allowed_repos.join(", ") || "—")}<br />
                      <span className="muted">{g.allowed_bases.join(", ") || "—"} · {g.environment || "—"}</span>
                    </td>
                    <td className="mono">
                      {g.budget && typeof g.budget.max_invocations === "number"
                        ? `${g.budget.used_invocations ?? 0}/${g.budget.max_invocations}` : "∞"}
                    </td>
                    <td className="mono small">{g.expires_at ? fmtLocal(g.expires_at, locale) : "—"}</td>
                    <td className="summary">{g.reason || "—"}</td>
                    <td>
                      {g.state === "ACTIVE" && (
                        <button className="btn btn-danger btn-sm" disabled={busy}
                          onClick={() => revoke(g)}>{t("auto.revoke")}</button>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Матрица возможностей */}
      <section className="panel" aria-labelledby="cap-h">
        <h2 id="cap-h">{t("auto.capMatrix")}</h2>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col">{t("auto.capabilities")}</th>
              <th scope="col">{t("auto.capAvailable")}</th>
            </tr></thead>
            <tbody>
              {(data?.capability_matrix ?? []).map((c) => (
                <tr key={c.code}>
                  <td><span className="mono">{c.code}</span> <span className="muted">{c.label}</span></td>
                  <td>
                    {c.available_via_autonomy
                      ? <span className="badge st-ok"><span aria-hidden="true">●</span> {t("auto.capAvailable")}</span>
                      : <span className="badge st-warn"><span aria-hidden="true">▲</span> {t("auto.capOwnerOnly")}</span>}
                    {c.separate_grant && <span className="badge st-info"><span aria-hidden="true">◆</span> {t("auto.capSeparate")}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* GitHub-доставка */}
      <section className="panel" aria-labelledby="del-h">
        <h2 id="del-h">{t("auto.deliveries")}</h2>
        {deliveries.length === 0 ? <p className="muted">{t("auto.noDeliveries")}</p> : (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr>
                <th scope="col">{t("auto.col.repo")}</th>
                <th scope="col">{t("auto.col.branch")}</th>
                <th scope="col">{t("auto.col.head")}</th>
                <th scope="col">{t("auto.col.pr")}</th>
                <th scope="col">{t("auto.col.checks")}</th>
                <th scope="col">{t("auto.col.gate")}</th>
              </tr></thead>
              <tbody>
                {deliveries.map((d) => (
                  <tr key={d.id}>
                    <td className="mono small">{d.repo}</td>
                    <td className="mono small">{d.branch}</td>
                    <td className="mono">{d.head_sha.slice(0, 10) || "—"}</td>
                    <td className="mono">{d.pr_number ? `#${d.pr_number}` : "—"} <span className="muted">{d.pr_state}</span></td>
                    <td>
                      <span className={`badge ${d.checks_state === "GREEN" ? "st-ok" : d.checks_state === "FAILING" ? "st-danger" : "st-warn"}`}>
                        <span aria-hidden="true">{d.checks_state === "GREEN" ? "●" : d.checks_state === "FAILING" ? "■" : "▲"}</span> {d.checks_state}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${d.gate_decision === "PERMIT" ? "st-ok" : "st-danger"}`}>
                        <span aria-hidden="true">{d.gate_decision === "PERMIT" ? "●" : "■"}</span> {d.gate_decision || "—"}
                      </span>
                      {d.gate_reason && <div className="muted small mono">{d.gate_reason}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function CreateGrantForm({ t, onClose, onDone }: { t: T; onClose: () => void; onDone: () => void }) {
  const [mode, setMode] = useState<AutonomyMode>("STANDARD");
  const [caps, setCaps] = useState<string[]>(["repo_read", "commit", "push_feature", "create_pr"]);
  const [environment, setEnvironment] = useState("synthetic");
  const [repos, setRepos] = useState("");
  const [bases, setBases] = useState("main");
  const [budget, setBudget] = useState("");
  const [reason, setReason] = useState("");
  const [ttl, setTtl] = useState("3600");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ALL_CAPS = ["repo_read", "repo_write", "commands", "deps_install", "commit",
    "push_feature", "create_pr", "merge_after_pass"];
  const toggle = (c: string) => setCaps((cs) => cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      await api.createGrant({
        project_id: "synthetic", mode, capabilities: caps, environment,
        allowed_repos: repos.split(",").map((s) => s.trim()).filter(Boolean),
        allowed_bases: bases.split(",").map((s) => s.trim()).filter(Boolean),
        budget: budget ? { max_invocations: Number(budget) } : {},
        reason, ttl_seconds: ttl ? Number(ttl) : null,
      });
      onDone();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally { setBusy(false); }
  };

  return (
    <section className="panel form-panel" aria-labelledby="cg-h">
      <h2 id="cg-h">{t("auto.createGrant")}</h2>
      <form onSubmit={submit}>
        <label className="field">
          <span>{t("auto.modes")}</span>
          <select value={mode} onChange={(e) => setMode(e.target.value as AutonomyMode)}>
            {MODES.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <fieldset className="field">
          <legend>{t("auto.capabilities")}</legend>
          <div className="chips">
            {ALL_CAPS.map((c) => (
              <label key={c} className={`chip toggle ${caps.includes(c) ? "on" : ""}`}>
                <input type="checkbox" checked={caps.includes(c)} onChange={() => toggle(c)} />
                <span className="mono">{c}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className="field"><span>{t("auto.form.env")}</span>
          <input className="mono" value={environment} onChange={(e) => setEnvironment(e.target.value)} /></label>
        <label className="field"><span>{t("auto.form.repos")}</span>
          <input className="mono" value={repos} onChange={(e) => setRepos(e.target.value)} placeholder="acme/demo" /></label>
        <label className="field"><span>{t("auto.form.bases")}</span>
          <input className="mono" value={bases} onChange={(e) => setBases(e.target.value)} /></label>
        <label className="field"><span>{t("auto.form.budget")}</span>
          <input className="mono" type="number" min="0" value={budget} onChange={(e) => setBudget(e.target.value)} /></label>
        <label className="field"><span>{t("auto.form.ttl")}</span>
          <input className="mono" type="number" min="0" value={ttl} onChange={(e) => setTtl(e.target.value)} /></label>
        <label className="field"><span>{t("auto.form.reason")}</span>
          <input value={reason} onChange={(e) => setReason(e.target.value)} required /></label>
        {err && <p className="error" role="alert">{err}</p>}
        <div className="form-actions">
          <button type="submit" className="btn-primary" disabled={busy}>{t("auto.form.submit")}</button>
          <button type="button" className="btn" onClick={onClose}>{t("auto.form.cancel")}</button>
        </div>
      </form>
    </section>
  );
}
