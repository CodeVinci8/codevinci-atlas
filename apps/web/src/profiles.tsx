import { useCallback, useEffect, useState } from "react";
import { api, type ProfileView } from "./api";
import type { LocaleKey } from "./i18n";

type T = (key: LocaleKey) => string;

const STATE_CLS: Record<string, { sym: string; cls: string }> = {
  READY: { sym: "●", cls: "st-ok" },
  LEASED: { sym: "◑", cls: "st-info" },
  COOLDOWN: { sym: "◆", cls: "st-warn" },
  AUTH_REQUIRED: { sym: "▲", cls: "st-warn" },
  ERROR: { sym: "■", cls: "st-danger" },
  UNCONFIGURED: { sym: "○", cls: "st-muted" },
  DRAINING: { sym: "◇", cls: "st-muted" },
  DISABLED: { sym: "▢", cls: "st-muted" },
  RETIRED: { sym: "▢", cls: "st-muted" },
};
function StateBadge({ state, t }: { state: string; t: T }) {
  const m = STATE_CLS[state] ?? { sym: "○", cls: "st-muted" };
  return (
    <span className={`badge ${m.cls}`} role="status">
      <span aria-hidden="true">{m.sym}</span> {t((`profiles.state.${state}`) as LocaleKey)}
    </span>
  );
}
function CapBadge({ status, t }: { status: string; t: T }) {
  const cls = status === "AVAILABLE" ? "st-ok" : status === "EXHAUSTED" ? "st-danger"
    : status === "LOW" ? "st-warn" : "st-muted";
  const key = (`cap.${status}`) as LocaleKey;
  return <span className={`badge ${cls}`} role="status"><span aria-hidden="true">◈</span> {t(key)}</span>;
}

export function ProfilesView({ t }: { t: T }) {
  const [rows, setRows] = useState<ProfileView[] | null>(null);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"cards" | "table">("cards");
  const [filter, setFilter] = useState<string>("");

  const refresh = useCallback(async () => {
    try { const r = await api.listProfiles(); setRows(r.profiles); setSummary(r.summary); setError(null); }
    catch { setError(t("common.error")); }
  }, [t]);
  useEffect(() => { refresh(); }, [refresh]);

  const shown = (rows ?? []).filter((p) => !filter || p.state === filter);
  const summaryChips = ["READY", "LEASED", "COOLDOWN", "AUTH_REQUIRED", "ERROR"]
    .filter((k) => (summary[k] ?? 0) > 0);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("profiles.title")}</h1>
          <p className="muted page-desc">{t("profiles.subtitle")}</p>
        </div>
        <div className="head-actions">
          <div className="seg" role="group" aria-label={t("profiles.cards")}>
            <button className={mode === "cards" ? "active" : ""} aria-pressed={mode === "cards"}
              onClick={() => setMode("cards")}>{t("profiles.cards")}</button>
            <button className={mode === "table" ? "active" : ""} aria-pressed={mode === "table"}
              onClick={() => setMode("table")}>{t("profiles.table")}</button>
          </div>
          <button className="btn" onClick={refresh}>{t("common.refresh")}</button>
        </div>
      </header>
      {error && <p className="error" role="alert">{error}</p>}

      {summaryChips.length > 0 && (
        <div className="chips" aria-label="summary">
          {summaryChips.map((k) => (
            <span key={k} className="chip"><StateBadge state={k} t={t} />
              <span className="mono muted"> {summary[k]}</span></span>
          ))}
        </div>
      )}

      <label className="field inline filter-field">
        <span>{t("profiles.filter")}</span>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">{t("profiles.all")}</option>
          {Object.keys(STATE_CLS).map((k) => (
            <option key={k} value={k}>{t((`profiles.state.${k}`) as LocaleKey)}</option>
          ))}
        </select>
      </label>

      {rows === null && !error && <p className="muted">{t("common.loading")}</p>}
      {rows && rows.length === 0 && (
        <div className="empty-state">
          <p>{t("profiles.empty")}</p>
          <p className="muted">{t("profiles.emptyHint")}</p>
        </div>
      )}

      {rows && rows.length > 0 && mode === "cards" && (
        <div className="card-grid">
          {shown.map((p) => (
            <article key={p.id} className="p-card">
              <header className="p-card-h">
                <span className="mono p-alias">{p.alias}</span>
                <span className="muted p-prov">{p.provider}</span>
              </header>
              <div className="p-row"><StateBadge state={p.state} t={t} /></div>
              <div className="p-row">
                <span className="label">{t("profiles.plan")}</span>
                <span>{p.health?.plan_label || t("profiles.noPlan")}</span>
              </div>
              <div className="p-row">
                <span className="label">{t("profiles.capacity")}</span>
                <CapBadge status={p.capacity.status} t={t} />
              </div>
              {p.active_lease && (
                <div className="p-row"><span className="label">{t("profiles.currentRun")}</span>
                  <span className="mono">{p.active_lease.role}</span></div>
              )}
              {p.next_action && <p className="muted p-next">{p.next_action}</p>}
            </article>
          ))}
        </div>
      )}

      {rows && rows.length > 0 && mode === "table" && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col">{t("profiles.col.alias")}</th>
              <th scope="col">{t("profiles.col.provider")}</th>
              <th scope="col">{t("profiles.col.state")}</th>
              <th scope="col">{t("profiles.col.plan")}</th>
              <th scope="col">{t("profiles.col.capacity")}</th>
              <th scope="col">{t("profiles.col.lease")}</th>
            </tr></thead>
            <tbody>
              {shown.map((p) => (
                <tr key={p.id}>
                  <td className="mono">{p.alias}</td>
                  <td>{p.provider}</td>
                  <td><StateBadge state={p.state} t={t} /></td>
                  <td>{p.health?.plan_label || t("profiles.noPlan")}</td>
                  <td><CapBadge status={p.capacity.status} t={t} /></td>
                  <td className="mono">{p.active_lease ? p.active_lease.role : t("common.none")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <section className="panel" aria-labelledby="onb-h">
        <h2 id="onb-h">{t("profiles.onboarding")}</h2>
        <ul className="onb-list">
          <li>{t("profiles.onboarding.official")}</li>
          <li>{t("profiles.onboarding.attach")}</li>
          <li>{t("profiles.onboarding.cookie")} — <span className="muted">{t("profiles.cookieNote")}</span></li>
        </ul>
      </section>
    </>
  );
}
