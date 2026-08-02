import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  api, type AuditPage, type Health, type HealthStatus, type Overview,
  type ProjectSummary, type ProjectState, type SourceKind,
} from "./api";
import { catalogs, detectInitialLocale, type Locale, type LocaleKey } from "./i18n";
import { PortfolioView, ProductMapView } from "./productmap";

type NavView = "projects" | "pulse" | "portfolio";

type T = (key: LocaleKey) => string;
function useT(locale: Locale): T {
  return useMemo(() => {
    const cat = catalogs[locale];
    return (key: LocaleKey) => cat[key];
  }, [locale]);
}

// --- статус/состояние: никогда не только цветом (§29.3): цвет + символ + текст.
function statusKey(s: HealthStatus): LocaleKey {
  switch (s) {
    case "READY": return "status.READY";
    case "DEGRADED": return "status.DEGRADED";
    case "OFFLINE": return "status.OFFLINE";
    case "UNAUTHORIZED": return "status.UNAUTHORIZED";
    default: return "status.UNKNOWN";
  }
}
function StatusBadge({ status, label }: { status: HealthStatus; label: string }) {
  const symbol = status === "READY" ? "●" : status === "DEGRADED" ? "▲" : "■";
  return (
    <span className={`badge badge-${status}`} role="status">
      <span aria-hidden="true">{symbol}</span> {label}
    </span>
  );
}

const STATE_META: Record<ProjectState, { key: LocaleKey; sym: string; cls: string }> = {
  clean: { key: "state.clean", sym: "●", cls: "st-ok" },
  dirty: { key: "state.dirty", sym: "▲", cls: "st-warn" },
  stale: { key: "state.stale", sym: "◆", cls: "st-info" },
  empty: { key: "state.empty", sym: "○", cls: "st-muted" },
  pending: { key: "state.pending", sym: "◔", cls: "st-info" },
  error: { key: "state.error", sym: "■", cls: "st-danger" },
  disconnected: { key: "state.disconnected", sym: "▢", cls: "st-muted" },
};
function StateBadge({ state, t }: { state: ProjectState; t: T }) {
  const m = STATE_META[state];
  return (
    <span className={`badge ${m.cls}`} role="status">
      <span aria-hidden="true">{m.sym}</span> {t(m.key)}
    </span>
  );
}
function sourceKey(k: SourceKind): LocaleKey {
  return (`source.${k}`) as LocaleKey;
}

// ---------------------------------------------------------------------------
export function App() {
  const [locale, setLocale] = useState<Locale>(detectInitialLocale());
  const [view, setView] = useState<NavView>("projects");
  const [selected, setSelected] = useState<string | null>(null);
  const [coreOffline, setCoreOffline] = useState(false);
  const t = useT(locale);

  // Dark — утверждённый default Atlas (§28). Светлое OS-предпочтение НЕ должно
  // молча заменять тёмную тему: без явного сохранённого выбора ставим dark.
  useEffect(() => {
    const saved = localStorage.getItem("atlas.theme");
    document.documentElement.dataset.theme = saved === "light" ? "light" : "dark";
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
    localStorage.setItem("atlas.locale", locale);
    document.title = catalogs[locale]["app.title"];
  }, [locale]);

  useEffect(() => {
    let alive = true;
    const ping = () => api.health().then(() => alive && setCoreOffline(false))
      .catch(() => alive && setCoreOffline(true));
    ping();
    const id = setInterval(ping, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  return (
    <div className="app">
      <a href="#main" className="skip-link">{t("skip.toContent")}</a>
      <Sidebar
        t={t} locale={locale} setLocale={setLocale} view={view}
        onNav={(v) => { setView(v); setSelected(null); }}
      />
      <main id="main" className="main" tabIndex={-1}>
        {coreOffline && (
          <div className="offline-banner" role="alert">
            <strong>{t("common.offline")}</strong> — {t("common.offlineHint")}
          </div>
        )}
        {selected ? (
          <ProjectDetail t={t} id={selected} onBack={() => setSelected(null)} />
        ) : view === "pulse" ? (
          <PulseView t={t} />
        ) : view === "portfolio" ? (
          <PortfolioView t={t} onOpen={setSelected} />
        ) : (
          <ProjectsView t={t} onOpen={setSelected} />
        )}
      </main>
    </div>
  );
}

// --- Sidebar ---------------------------------------------------------------
function Sidebar({ t, locale, setLocale, view, onNav }: {
  t: T; locale: Locale; setLocale: (l: Locale) => void;
  view: string; onNav: (v: NavView) => void;
}) {
  const item = (id: NavView, label: string, sym: string) => (
    <button
      className={`nav-item ${view === id ? "active" : ""}`}
      aria-current={view === id ? "page" : undefined}
      onClick={() => onNav(id)}
    >
      <span className="nav-ico" aria-hidden="true">{sym}</span> {label}
    </button>
  );
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">◆</span>
        <span className="brand-text">
          <strong>{t("app.title")}</strong>
          <span className="muted">{t("app.subtitle")}</span>
        </span>
      </div>
      <nav className="nav" aria-label={t("nav.projects")}>
        {item("projects", t("nav.projects"), "▤")}
        {item("portfolio", t("nav.portfolio"), "◫")}
        {item("pulse", t("nav.pulse"), "◈")}
      </nav>
      <div className="lang" role="group" aria-label={t("lang.switch")}>
        <button aria-pressed={locale === "ru"} onClick={() => setLocale("ru")}>{t("lang.ru")}</button>
        <button aria-pressed={locale === "en"} onClick={() => setLocale("en")}>{t("lang.en")}</button>
      </div>
    </aside>
  );
}

// --- Projects list + connect ----------------------------------------------
function ProjectsView({ t, onOpen }: { t: T; onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setRows((await api.listProjects()).projects);
      setError(null);
    } catch {
      setError(t("common.error"));
    }
  }, [t]);
  useEffect(() => { refresh(); }, [refresh]);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("projects.title")}</h1>
          <p className="muted page-desc">{t("projects.subtitle")}</p>
        </div>
        <button className="btn-primary" onClick={() => setShowForm(true)}>
          {t("projects.connect")}
        </button>
      </header>

      {showForm && (
        <ConnectForm t={t} onClose={() => setShowForm(false)}
          onDone={(id) => { setShowForm(false); refresh(); onOpen(id); }} />
      )}

      {error && <p className="error" role="alert">{error}</p>}
      {rows === null && !error && <p className="muted">{t("common.loading")}</p>}
      {rows && rows.length === 0 && (
        <div className="empty-state">
          <p>{t("projects.empty")}</p>
          <p className="muted">{t("projects.emptyHint")}</p>
        </div>
      )}
      {rows && rows.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th scope="col">{t("projects.col.name")}</th>
                <th scope="col">{t("projects.col.source")}</th>
                <th scope="col">{t("projects.col.branch")}</th>
                <th scope="col">{t("projects.col.state")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id} className="row-click" onClick={() => onOpen(p.id)}
                    tabIndex={0} role="link"
                    onKeyDown={(e) => { if (e.key === "Enter") onOpen(p.id); }}>
                  <td><span className="link-like">{p.name}</span></td>
                  <td>{t(sourceKey(p.source_kind))}</td>
                  <td className="mono">{p.branch || t("common.none")}</td>
                  <td><StateBadge state={projState(p)} t={t} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function projState(p: ProjectSummary): ProjectState {
  if (p.status === "disconnected") return "disconnected";
  if (p.source_kind === "empty") return "empty";
  if (!p.has_baseline) return "pending";
  return p.dirty ? "dirty" : "clean";
}

const KINDS: SourceKind[] = ["local_git", "github", "archive", "empty"];

function ConnectForm({ t, onClose, onDone }: {
  t: T; onClose: () => void; onDone: (id: string) => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<SourceKind>("local_git");
  const [path, setPath] = useState("");
  const [github, setGithub] = useState("");
  const [archive, setArchive] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const ov = await api.connectProject({
        name, source_kind: kind,
        path: kind === "local_git" ? path : undefined,
        github_ref: kind === "github" ? github : undefined,
        archive_path: kind === "archive" ? archive : undefined,
      });
      onDone(ov.project.id);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel form-panel" aria-labelledby="cf-h">
      <h2 id="cf-h">{t("connect.title")}</h2>
      <form onSubmit={submit}>
        <label className="field">
          <span>{t("connect.name")}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="field">
          <span>{t("connect.kind")}</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as SourceKind)}>
            {KINDS.map((k) => <option key={k} value={k}>{t(sourceKey(k))}</option>)}
          </select>
        </label>
        {kind === "local_git" && (
          <label className="field">
            <span>{t("connect.path")}</span>
            <input className="mono" value={path} onChange={(e) => setPath(e.target.value)} required />
          </label>
        )}
        {kind === "github" && (
          <label className="field">
            <span>{t("connect.github")}</span>
            <input className="mono" value={github} onChange={(e) => setGithub(e.target.value)} required />
          </label>
        )}
        {kind === "archive" && (
          <label className="field">
            <span>{t("connect.archive")}</span>
            <input className="mono" value={archive} onChange={(e) => setArchive(e.target.value)} required />
          </label>
        )}
        <p className="muted field-hint">{t((`connect.hint.${kind}`) as LocaleKey)}</p>
        {err && <p className="error" role="alert">{err}</p>}
        <div className="form-actions">
          <button type="submit" className="btn-primary" disabled={busy}>{t("connect.submit")}</button>
          <button type="button" className="btn" onClick={onClose}>{t("connect.cancel")}</button>
        </div>
      </form>
    </section>
  );
}

// --- Project detail: tabs Overview (VP-2) / Product Map (VP-3) --------------
function ProjectDetail({ t, id, onBack }: { t: T; id: string; onBack: () => void }) {
  const [tab, setTab] = useState<"overview" | "map">("overview");
  return (
    <>
      <button className="btn back" onClick={onBack}>← {t("overview.back")}</button>
      <div className="tabs" role="tablist" aria-label={t("nav.projects")}>
        <button role="tab" id="tab-overview" aria-selected={tab === "overview"}
          aria-controls="panel-project" className={`tab ${tab === "overview" ? "active" : ""}`}
          onClick={() => setTab("overview")}>{t("pm.tab.overview")}</button>
        <button role="tab" id="tab-map" aria-selected={tab === "map"}
          aria-controls="panel-project" className={`tab ${tab === "map" ? "active" : ""}`}
          onClick={() => setTab("map")}>{t("pm.tab.map")}</button>
      </div>
      <div id="panel-project" role="tabpanel"
        aria-labelledby={tab === "overview" ? "tab-overview" : "tab-map"}>
        {tab === "overview" ? <OverviewView t={t} id={id} /> : <ProductMapView t={t} id={id} />}
      </div>
    </>
  );
}

// --- Project Overview ------------------------------------------------------
function OverviewView({ t, id }: { t: T; id: string }) {
  const [ov, setOv] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [branch, setBranch] = useState("atlas/vp-2-");

  const load = useCallback(async () => {
    try { setOv(await api.getProject(id)); setError(null); }
    catch { setError(t("overview.notFound")); }
  }, [id, t]);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<Overview>) => {
    setBusy(true); setError(null);
    try { setOv(await fn()); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  if (error && !ov) return <p className="error" role="alert">{error}</p>;
  if (!ov) return <p className="muted">{t("common.loading")}</p>;

  const bl = ov.baseline;
  return (
    <>
      <header className="page-head">
        <div>
          <h1>{ov.project.name}</h1>
          <p className="muted page-desc">
            {t(sourceKey(ov.project.source_kind))} · <StateBadge state={ov.state} t={t} />
          </p>
        </div>
        <div className="head-actions">
          {(ov.project.source_kind === "local_git" || ov.project.source_kind === "archive") &&
            ov.project.status !== "disconnected" && (
              <button className="btn" disabled={busy}
                onClick={() => act(() => api.refreshBaseline(id))}>{t("overview.refresh")}</button>
            )}
          {ov.project.status !== "disconnected" && (
            <button className="btn btn-danger" disabled={busy}
              onClick={() => act(() => api.disconnect(id))}>{t("overview.disconnect")}</button>
          )}
        </div>
      </header>

      {error && <p className="error" role="alert">{error}</p>}

      <section className="next-action" aria-label={t("overview.nextAction")}>
        <span className="na-label">{t("overview.nextAction")}</span>
        <span className="na-text">{ov.next_action}</span>
      </section>

      {ov.state === "dirty" && (
        <p className="warn" role="note">{t("overview.dirtyWarn")}</p>
      )}

      <section className="panel" aria-labelledby="src-h">
        <h2 id="src-h">{t("overview.source")}</h2>
        <dl className="kv">
          <dt>{t("overview.location")}</dt>
          <dd className="mono wrap">{ov.project.source_location || t("common.none")}</dd>
          {bl && (
            <>
              <dt>{t("overview.remotes")}</dt>
              <dd className="mono wrap">
                {bl.remotes.length
                  ? bl.remotes.map((r) => `${r.name} → ${r.url}`).join("  |  ")
                  : t("common.none")}
              </dd>
            </>
          )}
        </dl>
      </section>

      {bl ? (
        <>
          <section className="panel" aria-labelledby="bl-h">
            <h2 id="bl-h">{t("overview.baseline")}</h2>
            <div className="stat-row">
              <Stat label={t("overview.branch")} value={bl.branch} mono />
              <Stat label={t("overview.head")} value={bl.head.slice(0, 12) || "—"} mono />
              <Stat label={t("overview.tracked")} value={String(bl.tracked_total)} />
              <Stat label={t("overview.changes")} value={String(bl.tracked_changes)} />
              <Stat label={t("overview.untracked")} value={String(bl.untracked)} />
            </div>
            <dl className="kv">
              <dt>{t("overview.observedAt")}</dt><dd className="mono">{bl.observed_at}</dd>
              <dt>{t("overview.contentHash")}</dt><dd className="mono wrap">{bl.content_hash}</dd>
              <dt>{t("overview.secretScan")}</dt>
              <dd>{bl.secret_scan.clean ? `✓ ${t("common.yes")}` : `✗ ${t("common.no")}`} ({bl.secret_scan.scanned_files})</dd>
            </dl>
          </section>

          <section className="panel" aria-labelledby="in-h">
            <h2 id="in-h">{t("overview.instructions")}</h2>
            {bl.instructions.length === 0 ? <p className="muted">{t("common.none")}</p> : (
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead><tr>
                    <th scope="col">{t("overview.instr.path")}</th>
                    <th scope="col">{t("overview.instr.scope")}</th>
                    <th scope="col">{t("overview.instr.precedence")}</th>
                    <th scope="col">{t("overview.instr.read")}</th>
                    <th scope="col">{t("overview.instr.summary")}</th>
                  </tr></thead>
                  <tbody>
                    {bl.instructions.map((i) => (
                      <tr key={i.path}>
                        <td className="mono">{i.path}</td>
                        <td className="mono">{i.scope}</td>
                        <td>{i.precedence}</td>
                        <td>{i.read_ok ? t("common.yes") : t("common.no")}</td>
                        <td className="summary">{i.summary || t("common.none")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel" aria-labelledby="pm-h">
            <h2 id="pm-h">{t("overview.packageManagers")}</h2>
            <div className="chips">
              {bl.package_managers.length
                ? bl.package_managers.map((p, i) => (
                  <span key={i} className="chip">{p.name} <span className="mono muted">{p.evidence}</span></span>))
                : <span className="muted">{t("common.none")}</span>}
            </div>
            <h3 className="sub-h">{t("overview.commands")}</h3>
            <p className="muted field-hint">{t("overview.commandsNote")}</p>
            {bl.baseline_commands.length === 0 ? <p className="muted">{t("common.none")}</p> : (
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead><tr>
                    <th scope="col">{t("overview.col.name")}</th>
                    <th scope="col">{t("overview.col.command")}</th>
                    <th scope="col">{t("overview.col.source")}</th>
                  </tr></thead>
                  <tbody>
                    {bl.baseline_commands.map((c, i) => (
                      <tr key={i}>
                        <td>{c.name}</td>
                        <td className="mono">{c.command}</td>
                        <td className="mono muted">{c.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : (
        <section className="panel"><p className="muted">{t("overview.noBaseline")}</p></section>
      )}

      <section className="panel" aria-labelledby="wt-h">
        <h2 id="wt-h">{t("overview.worktrees")}</h2>
        <p className={ov.lease.active ? "lease lease-on" : "lease muted"}>
          <span aria-hidden="true">{ov.lease.active ? "🔒" : "○"}</span>{" "}
          {ov.lease.active ? t("overview.leaseActive") : t("overview.leaseNone")}
        </p>
        {ov.worktrees.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr>
                <th scope="col">{t("overview.wt.branch")}</th>
                <th scope="col">{t("overview.wt.path")}</th>
                <th scope="col">{t("overview.wt.status")}</th>
              </tr></thead>
              <tbody>
                {ov.worktrees.map((w) => (
                  <tr key={w.id}>
                    <td className="mono">{w.branch}</td>
                    <td className="mono wrap">{w.path}</td>
                    <td>{w.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {bl && ov.project.status !== "disconnected" && (
          <form className="wt-form" onSubmit={(e) => { e.preventDefault(); act(() => api.createWorktree(id, branch)); }}>
            <label className="field inline">
              <span>{t("overview.branchPrompt")}</span>
              <input className="mono" value={branch} onChange={(e) => setBranch(e.target.value)}
                     pattern="atlas/vp-\d+-[a-z0-9-]+" />
            </label>
            <button type="submit" className="btn-primary" disabled={busy}>{t("overview.createWorktree")}</button>
          </form>
        )}
      </section>
    </>
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

// --- Pulse (health + audit) ------------------------------------------------
function PulseView({ t }: { t: T }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [audit, setAudit] = useState<AuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [h, a] = await Promise.all([api.health(), api.audit()]);
      setHealth(h); setAudit(a); setError(null);
    } catch { setError(t("common.error")); }
  }, [t]);
  useEffect(() => { refresh(); const id = setInterval(refresh, 5000); return () => clearInterval(id); }, [refresh]);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("health.title")}</h1>
          <p className="muted page-desc">{t("app.subtitle")}</p>
        </div>
        <button className="btn" onClick={refresh}>{t("common.refresh")}</button>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {health && (
        <section className="panel" aria-labelledby="h-h">
          <h2 id="h-h">{t("health.core")}</h2>
          <div className="stat-row">
            <div className="stat"><span className="label">{t("health.core")}</span>
              <StatusBadge status={health.status} label={t(statusKey(health.status))} /></div>
            <div className="stat"><span className="label">{t("health.runner")}</span>
              <StatusBadge status={health.runner.status} label={t(statusKey(health.runner.status))} /></div>
            <div className="stat"><span className="label">{t("health.db")}</span>
              <StatusBadge status={health.core.db.ok ? "READY" : "DEGRADED"}
                label={t(health.core.db.ok ? "status.READY" : "status.DEGRADED")} /></div>
            <div className="stat"><span className="label">{t("health.version")}</span>
              <span className="stat-val mono">{health.version}</span></div>
          </div>
          {health.runner.status !== "READY" && (
            <p className="warn" role="note">{t("runner.offlineHint")}</p>
          )}
        </section>
      )}
      <section className="panel" aria-labelledby="a-h">
        <h2 id="a-h">{t("audit.title")}</h2>
        {audit && <p className="muted">{t("audit.total")}: <span className="mono">{audit.total}</span></p>}
        {audit && audit.events.length === 0 && <p className="muted">{t("audit.empty")}</p>}
        {audit && audit.events.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr>
                <th scope="col">{t("audit.title")}</th><th scope="col">actor</th><th scope="col">time (UTC)</th>
              </tr></thead>
              <tbody>
                {audit.events.map((e) => (
                  <tr key={e.id}>
                    <td className="mono">{e.event_type}</td>
                    <td>{e.actor}</td>
                    <td className="mono">{e.created_at}</td>
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
