import { useCallback, useEffect, useState } from "react";
import {
  api, type RunDetail, type RunEventRow, type RunRow, type RouterDecisionRow,
  type ProviderSessionRow, type RunState,
} from "./api";
import type { LocaleKey } from "./i18n";

type T = (key: LocaleKey) => string;

// Состояние — цвет + символ + текст (§29.3), никогда не только цвет.
const STATE_SYM: Record<RunState, { sym: string; cls: string }> = {
  QUEUED: { sym: "◔", cls: "st-muted" },
  PREPARING: { sym: "◑", cls: "st-info" },
  RUNNING: { sym: "▶", cls: "st-info" },
  COLLECTING: { sym: "◕", cls: "st-info" },
  SUCCEEDED: { sym: "●", cls: "st-ok" },
  RATE_LIMITED: { sym: "▲", cls: "st-warn" },
  AUTH_REQUIRED: { sym: "▲", cls: "st-warn" },
  PAUSED: { sym: "❙❙", cls: "st-muted" },
  INTERRUPTED: { sym: "◆", cls: "st-warn" },
  FAILED: { sym: "■", cls: "st-danger" },
  CANCELLED: { sym: "▢", cls: "st-muted" },
  OWNER_REQUIRED: { sym: "◆", cls: "st-warn" },
};
function RunStateBadge({ state, t }: { state: RunState; t: T }) {
  const m = STATE_SYM[state] ?? { sym: "○", cls: "st-muted" };
  return (
    <span className={`badge ${m.cls}`} role="status">
      <span aria-hidden="true">{m.sym}</span> {t((`runs.state.${state}`) as LocaleKey)}
    </span>
  );
}

export function RunsView({ t }: { t: T }) {
  const [rows, setRows] = useState<RunRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setRows((await api.listRuns()).runs); setError(null); }
    catch { setError(t("common.error")); }
  }, [t]);
  useEffect(() => { refresh(); }, [refresh]);

  if (open) return <RunDetailView t={t} rid={open} onBack={() => { setOpen(null); refresh(); }} />;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("runs.title")}</h1>
          <p className="muted page-desc">{t("runs.subtitle")}</p>
        </div>
        <button className="btn" onClick={refresh}>{t("common.refresh")}</button>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {rows === null && !error && <p className="muted">{t("common.loading")}</p>}
      {rows && rows.length === 0 && (
        <div className="empty-state">
          <p>{t("runs.empty")}</p>
          <p className="muted">{t("runs.emptyHint")}</p>
        </div>
      )}
      {rows && rows.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col">{t("runs.col.id")}</th>
              <th scope="col">{t("runs.col.state")}</th>
              <th scope="col">{t("runs.col.vp")}</th>
              <th scope="col">{t("runs.col.wo")}</th>
              <th scope="col">{t("runs.col.created")}</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="row-click" tabIndex={0} role="link"
                    onClick={() => setOpen(r.id)}
                    onKeyDown={(e) => { if (e.key === "Enter") setOpen(r.id); }}>
                  <td className="mono"><span className="link-like">{r.id}</span></td>
                  <td><RunStateBadge state={r.state} t={t} /></td>
                  <td>{r.vp_key || t("common.none")}</td>
                  <td className="mono">{r.work_order_id || t("common.none")}</td>
                  <td className="mono">{r.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

const TERMINAL: RunState[] = ["SUCCEEDED", "FAILED", "CANCELLED"];

function RunDetailView({ t, rid, onBack }: { t: T; rid: string; onBack: () => void }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<RunEventRow[] | null>(null);
  const [router, setRouter] = useState<{ decisions: RouterDecisionRow[]; sessions: ProviderSessionRow[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [r, ev, rt] = await Promise.all([api.getRun(rid), api.runEvents(rid), api.runRouter(rid)]);
      setRun(r.run); setEvents(ev.events); setRouter(rt); setError(null);
    } catch { setError(t("common.error")); }
  }, [rid, t]);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<{ run: RunDetail }>) => {
    setBusy(true); setError(null);
    try { setRun((await fn()).run); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  if (error && !run) return (
    <><button className="btn back" onClick={onBack}>← {t("runs.detail.back")}</button>
      <p className="error" role="alert">{error}</p></>
  );
  if (!run) return (
    <><button className="btn back" onClick={onBack}>← {t("runs.detail.back")}</button>
      <p className="muted">{t("common.loading")}</p></>
  );

  const reviewer = run.role_steps.find((s) => s.role === "reviewer");
  const verdict = reviewer?.verdict || "";
  const pausable = run.state === "RUNNING" || run.state === "PREPARING";
  const resumable = run.state === "PAUSED";
  const cancellable = !TERMINAL.includes(run.state);
  const transitions = (events ?? []).filter((e) => e.type === "run.transition");

  return (
    <>
      <button className="btn back" onClick={onBack}>← {t("runs.detail.back")}</button>
      <header className="page-head">
        <div>
          <h1 className="mono">{run.id}</h1>
          <p className="muted page-desc">
            <RunStateBadge state={run.state} t={t} />{" "}
            {run.vp_key && <>· {run.vp_key}</>}
          </p>
        </div>
        <div className="head-actions">
          {pausable && <button className="btn" disabled={busy}
            onClick={() => act(() => api.pauseRun(rid, run.version))}>{t("runs.pause")}</button>}
          {resumable && <button className="btn-primary" disabled={busy}
            onClick={() => act(() => api.resumeRun(rid, run.version))}>{t("runs.resume")}</button>}
          {cancellable && <button className="btn btn-danger" disabled={busy}
            onClick={() => { if (confirm(t("runs.confirmCancel"))) act(() => api.cancelRun(rid, run.version)); }}>
            {t("runs.cancel")}</button>}
        </div>
      </header>
      {error && <p className="error" role="alert">{error}</p>}

      {run.next_action && (
        <section className="next-action" aria-label={t("runs.nextAction")}>
          <span className="na-label">{t("runs.nextAction")}</span>
          <span className="na-text">{run.next_action}</span>
        </section>
      )}
      {run.blocker && <p className="warn" role="note">{t("runs.blocker")}: {run.blocker}</p>}

      <section className="panel" aria-labelledby="rl-h">
        <h2 id="rl-h">{t("runs.roles")}</h2>
        <p className="muted field-hint">{t("runs.silentFallback")}</p>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col">role</th>
              <th scope="col">{t("runs.requested")}</th>
              <th scope="col">{t("runs.effective")}</th>
              <th scope="col">{t("runs.reason")}</th>
              <th scope="col">status</th>
            </tr></thead>
            <tbody>
              {run.role_steps.map((s) => (
                <tr key={s.id}>
                  <td>{t((`runs.role.${s.role}`) as LocaleKey)}</td>
                  <td className="mono">{s.requested_profile || "—"} / {s.requested_model || "—"}</td>
                  <td className="mono">{s.effective_profile || "—"} / {s.effective_model || "—"}</td>
                  <td className="mono">{s.reason_code || "—"}</td>
                  <td>{s.verdict || s.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" aria-labelledby="rv-h">
        <h2 id="rv-h">{t("runs.verdict")}</h2>
        {verdict
          ? <p><span className={`badge ${verdict === "PASS" ? "st-ok" : "st-warn"}`} role="status">
              <span aria-hidden="true">{verdict === "PASS" ? "●" : "▲"}</span>{" "}
              {t((`runs.verdict.${verdict}`) as LocaleKey)}</span></p>
          : <p className="muted">{t("runs.verdict.none")}</p>}
      </section>

      <section className="panel" aria-labelledby="lease-h">
        <h2 id="lease-h">{t("runs.lease")}</h2>
        {run.active_lease.length === 0
          ? <p className="lease muted"><span aria-hidden="true">○</span> {t("runs.leaseNone")}</p>
          : run.active_lease.map((l, i) => (
              <p key={i} className="lease lease-on"><span aria-hidden="true">🔒</span>{" "}
                <span className="mono">{l.profile_id}</span> · {l.role}
                {l.worktree && <> · <span className="mono">{l.worktree}</span></>}</p>
            ))}
      </section>

      <section className="panel" aria-labelledby="tl-h">
        <h2 id="tl-h">{t("runs.timeline")}</h2>
        {transitions.length === 0 ? <p className="muted">{t("runs.noEvents")}</p> : (
          <ol className="timeline">
            {transitions.map((e) => (
              <li key={e.id}>
                <span className="mono t-time">{e.occurred_at.slice(11, 19)}</span>
                <span className="mono">{String(e.payload.from ?? "")} → {String(e.payload.to ?? "")}</span>
                {e.payload.reason ? <span className="muted"> · {String(e.payload.reason)}</span> : null}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="panel" aria-labelledby="ev-h">
        <h2 id="ev-h">{t("runs.events")}</h2>
        {events && events.length === 0 && <p className="muted">{t("runs.noEvents")}</p>}
        {events && events.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th scope="col">seq</th><th scope="col">type</th><th scope="col">time (UTC)</th></tr></thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id}>
                    <td className="mono">{e.seq}</td>
                    <td className="mono">{e.type}</td>
                    <td className="mono">{e.occurred_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {router && router.sessions.length > 0 && (
        <section className="panel" aria-labelledby="ps-h">
          <h2 id="ps-h">{t("runs.sessions")}</h2>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th scope="col">role</th><th scope="col">provider</th><th scope="col">session</th></tr></thead>
              <tbody>
                {router.sessions.map((s) => (
                  <tr key={s.id}>
                    <td>{s.role}</td><td>{s.provider}</td><td className="mono">{s.session_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
