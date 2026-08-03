import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  api, type AuditPage, type Health, type HealthStatus, type Overview,
  type ProjectSummary, type ProjectState, type SourceKind,
} from "./api";
import { catalogs, detectInitialLocale, type Locale, type LocaleKey } from "./i18n";
import { PortfolioView, ProductMapView } from "./productmap";
import { WorkOrdersView } from "./workorders";
import { RunsView } from "./runs";
import { ProfilesView } from "./profiles";
import { QualityView } from "./quality";
import { AutonomyView } from "./autonomy";
import { TimeMachineView } from "./timemachine";
import { NavIcon, type IconName } from "./icons";
import { fmtBytes, fmtDuration, fmtLocal, fmtRelative } from "./fmt";
import type { NextAction, SystemSummary } from "./api";

type NavView = "projects" | "pulse" | "portfolio" | "runs" | "profiles" | "quality"
  | "autonomy" | "timemachine";

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
        <div className="route-fade" key={selected ? `p:${selected}` : view}>
          {selected ? (
            <ProjectDetail t={t} id={selected} onBack={() => setSelected(null)} />
          ) : view === "pulse" ? (
            <PulseView t={t} locale={locale} />
          ) : view === "portfolio" ? (
            <PortfolioView t={t} onOpen={setSelected} />
          ) : view === "runs" ? (
            <RunsView t={t} />
          ) : view === "profiles" ? (
            <ProfilesView t={t} locale={locale} />
          ) : view === "quality" ? (
            <QualityView t={t} locale={locale} />
          ) : view === "autonomy" ? (
            <AutonomyView t={t} locale={locale} />
          ) : view === "timemachine" ? (
            <TimeMachineView t={t} locale={locale} />
          ) : (
            <ProjectsView t={t} onOpen={setSelected} />
          )}
        </div>
      </main>
    </div>
  );
}

// --- Sidebar ---------------------------------------------------------------
function Sidebar({ t, locale, setLocale, view, onNav }: {
  t: T; locale: Locale; setLocale: (l: Locale) => void;
  view: string; onNav: (v: NavView) => void;
}) {
  const item = (id: NavView, label: string, icon: IconName) => (
    <button
      className={`nav-item ${view === id ? "active" : ""}`}
      aria-current={view === id ? "page" : undefined}
      onClick={() => onNav(id)}
    >
      <span className="nav-ico" aria-hidden="true"><NavIcon name={icon} /></span> {label}
    </button>
  );
  return (
    <aside className="sidebar">
      <button type="button" className="brand brand-home" aria-label={t("brand.home")}
        title={t("brand.home")} onClick={() => onNav("pulse")}>
        <span className="brand-mark" aria-hidden="true">
          <BrandMark />
        </span>
        <span className="brand-text">
          <strong>{t("app.title")}</strong>
          <span className="muted">{t("app.subtitle")}</span>
        </span>
      </button>
      <nav className="nav" aria-label={t("nav.projects")}>
        {item("pulse", t("nav.pulse"), "pulse")}
        {item("projects", t("nav.projects"), "projects")}
        {item("profiles", t("nav.profiles"), "profiles")}
        {item("runs", t("nav.runs"), "runs")}
        {item("quality", t("nav.quality"), "quality")}
        {item("autonomy", t("nav.autonomy"), "autonomy")}
        {item("timemachine", t("nav.timemachine"), "timemachine")}
        {item("portfolio", t("nav.portfolio"), "portfolio")}
      </nav>
      <LangSwitch t={t} locale={locale} setLocale={setLocale} />
    </aside>
  );
}

// Оригинальная марка Atlas (CodeVinci Ember): компас/ромб, графит + ember-акцент.
function BrandMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
      <rect x="1.5" y="1.5" width="29" height="29" rx="8" fill="#141416" stroke="#2a2928" />
      <path d="M16 5.5 26.5 16 16 26.5 5.5 16 16 5.5Z" stroke="#3a3936" strokeWidth="1.1" fill="none" />
      <path d="M16 9.2 20 16 16 22.8 12 16 16 9.2Z" fill="#f28a3d" fillOpacity="0.16"
        stroke="#f28a3d" strokeWidth="1.3" strokeLinejoin="round" />
      <circle cx="16" cy="16" r="1.9" fill="#ffad66" />
    </svg>
  );
}

// --- Polished RU|EN segmented control (§29: видимый текст, sliding indicator,
// клавиатура + screen-reader, видимый focus, RU по умолчанию). -----------------
function LangSwitch({ t, locale, setLocale }: {
  t: T; locale: Locale; setLocale: (l: Locale) => void;
}) {
  const langs: Locale[] = ["ru", "en"];
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      setLocale(locale === "ru" ? "en" : "ru");
    }
  };
  return (
    <div className="lang-seg" role="radiogroup" aria-label={t("lang.aria")} onKeyDown={onKey}>
      <span className="lang-globe" aria-hidden="true">◈</span>
      <div className={`seg-track seg-${locale}`}>
        <span className="seg-thumb" aria-hidden="true" />
        {langs.map((l) => (
          <button key={l} type="button" role="radio" aria-checked={locale === l}
            tabIndex={locale === l ? 0 : -1} className={`seg-opt ${locale === l ? "active" : ""}`}
            onClick={() => setLocale(l)}>
            {l === "ru" ? "RU" : "EN"}
          </button>
        ))}
      </div>
    </div>
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
        <div className="empty-state compact">
          <p>{t("projects.empty")}</p>
          <p className="muted">{t("projects.emptyHint")}</p>
          <button className="btn-primary" onClick={() => setShowForm(true)}>{t("projects.connect")}</button>
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
  const [tab, setTab] = useState<"overview" | "map" | "workorders">("overview");
  const tabId = `tab-${tab}`;
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
        <button role="tab" id="tab-workorders" aria-selected={tab === "workorders"}
          aria-controls="panel-project" className={`tab ${tab === "workorders" ? "active" : ""}`}
          onClick={() => setTab("workorders")}>{t("pm.tab.workorders")}</button>
      </div>
      <div id="panel-project" role="tabpanel" aria-labelledby={tabId}>
        {tab === "overview" ? <OverviewView t={t} id={id} />
          : tab === "map" ? <ProductMapView t={t} id={id} />
          : <WorkOrdersView t={t} id={id} />}
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

// --- Pulse (иерархия: состояние → риски → ресурсы → диагностика) -----------
function Kpi({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <span className={mono ? "kpi-val mono" : "kpi-val"}>{value}</span>
    </div>
  );
}

// Порог хранилища/памяти по факту ФС: 80–89% warning, >=90% critical (§VP6-D3).
function usageLevel(used: number | null | undefined, total: number | null | undefined):
  { pct: number | null; level: "ok" | "warn" | "crit" } {
  if (!used || !total || total <= 0) return { pct: null, level: "ok" };
  const pct = Math.round((used / total) * 100);
  return { pct, level: pct >= 90 ? "crit" : pct >= 80 ? "warn" : "ok" };
}

// Измеритель: бар + used/total + процент + символ порога (не только цвет, §29.3).
function Meter({ label, used, total, na }: {
  label: string; used: number | null; total: number | null; na: string;
}) {
  const { pct, level } = usageLevel(used, total);
  const sym = level === "crit" ? "■" : level === "warn" ? "▲" : "●";
  return (
    <div className="meter">
      <div className="meter-top">
        <span className="kpi-label">{label}</span>
        <span className={`meter-val mono st-${level === "ok" ? "ok" : level === "warn" ? "warn" : "danger"}`}>
          <span aria-hidden="true">{sym}</span> {fmtBytes(used, na)} / {fmtBytes(total, na)}
          {pct !== null && <> · {pct}%</>}
        </span>
      </div>
      <div className={`meter-bar level-${level}`} role="img"
           aria-label={`${label}: ${pct ?? "?"}%`}>
        <span className="meter-fill" style={{ width: `${pct ?? 0}%` }} />
      </div>
    </div>
  );
}

// Контекстное продуктовое next action (сервер вычисляет код; UI локализует).
function nextActionText(na: NextAction | undefined, t: T): string {
  if (!na) return t("sys.okAll");
  const base = t((`na.${na.code}`) as LocaleKey) ?? na.text;
  return na.count ? `${base} (${na.count})` : base;
}

// Реальная CPU-утилизация: компактное кольцо + текст + порог-символ (не только цвет).
function CpuGauge({ pct, window, na, label }: {
  pct: number | null; window: number | null; na: string; label: string;
}) {
  const level = pct === null ? "muted" : pct >= 90 ? "crit" : pct >= 75 ? "warn" : "ok";
  const sym = level === "crit" ? "■" : level === "warn" ? "▲" : level === "ok" ? "●" : "○";
  const r = 16, c = 2 * Math.PI * r;
  const frac = pct === null ? 0 : Math.max(0, Math.min(1, pct / 100));
  const stroke = level === "crit" ? "var(--danger)" : level === "warn" ? "var(--warn)"
    : level === "ok" ? "var(--ember)" : "var(--border)";
  return (
    <div className="cpu-gauge">
      <svg width="52" height="52" viewBox="0 0 44 44" role="img"
        aria-label={`${label}: ${pct === null ? na : pct + "%"}`}>
        <circle cx="22" cy="22" r={r} fill="none" stroke="var(--border)" strokeWidth="4" />
        <circle cx="22" cy="22" r={r} fill="none" stroke={stroke} strokeWidth="4"
          strokeDasharray={`${(c * frac).toFixed(1)} ${c.toFixed(1)}`} strokeLinecap="round"
          transform="rotate(-90 22 22)" />
      </svg>
      <div className="cpu-txt">
        <span className="kpi-label">{label}</span>
        <span className={`cpu-val mono st-${level === "ok" ? "ok" : level === "warn" ? "warn" : level === "crit" ? "danger" : "muted"}`}>
          <span aria-hidden="true">{sym}</span> {pct === null ? na : `${pct}%`}
        </span>
        {pct !== null && window !== null && (
          <span className="muted field-hint">{window.toFixed(1)}s ·  /proc/stat</span>)}
      </div>
    </div>
  );
}

// Человекочитаемая семья события Audit (§VP6-D3): канонический код сохраняется.
function auditFamily(eventType: string, t: T): string {
  const head = (eventType.split(".")[0] || "").toLowerCase();
  const map: Record<string, LocaleKey> = {
    core: "audit.fam.core", profiles: "audit.fam.profiles", review: "audit.fam.review",
    runs: "audit.fam.runs", work_orders: "audit.fam.workorders", workorders: "audit.fam.workorders",
    product: "audit.fam.product", productmap: "audit.fam.product", projects: "audit.fam.projects",
  };
  return t(map[head] ?? "audit.fam.other");
}

// Bounded Planner → Builder → Reviewer handoff-trace (единственная major-анимация).
function HandoffTrace({ t, active }: { t: T; active: boolean }) {
  return (
    <div className={`handoff-trace ${active ? "active" : ""}`} role="img"
         aria-label="Planner → Builder → Reviewer">
      <span className="ht-node">{t("runs.role.planner")}</span>
      <span className="ht-link" aria-hidden="true" />
      <span className="ht-node">{t("runs.role.builder")}</span>
      <span className="ht-link" aria-hidden="true" />
      <span className="ht-node">{t("runs.role.reviewer")}</span>
    </div>
  );
}

function PulseView({ t, locale }: { t: T; locale: Locale }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [sys, setSys] = useState<SystemSummary | null>(null);
  const [audit, setAudit] = useState<AuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [partial, setPartial] = useState(false);
  const [auditFilter, setAuditFilter] = useState("");
  const na = t("sys.na");

  const refresh = useCallback(async () => {
    const [h, a] = await Promise.allSettled([api.health(), api.audit()]);
    if (h.status === "fulfilled") setHealth(h.value); else setHealth(null);
    if (a.status === "fulfilled") setAudit(a.value);
    try { setSys((await api.systemSummary()).summary); setPartial(false); }
    catch { setPartial(true); }
    setError(h.status === "rejected" ? t("common.error") : null);
  }, [t]);
  useEffect(() => { refresh(); const id = setInterval(refresh, 5000); return () => clearInterval(id); }, [refresh]);

  const runner = health?.runner.status ?? "UNKNOWN";
  const dbOk = health?.core.db.ok ?? false;
  const overallReady = health?.status === "READY" && runner === "READY" && dbOk;
  const disk = usageLevel(sys?.disk.used_bytes, sys?.disk.total_bytes);
  const activeRuns = sys?.runs.active ?? 0;
  const ownerReq = sys?.runs.owner_required ?? 0;

  // Операционные риски (важнейшее первым).
  const risks: { sym: string; cls: string; text: string; action?: string }[] = [];
  if (disk.level === "crit")
    risks.push({ sym: "■", cls: "st-danger", text: `${t("sys.storageCrit")} — ${disk.pct}%`,
                 action: t("sys.storageAction") });
  else if (disk.level === "warn")
    risks.push({ sym: "▲", cls: "st-warn", text: `${t("sys.storageWarn")} — ${disk.pct}%` });
  if (runner !== "READY")
    risks.push({ sym: "▲", cls: "st-warn", text: t("runner.offlineHint") });
  if (ownerReq > 0)
    risks.push({ sym: "◆", cls: "st-warn", text: `${t("runs.state.OWNER_REQUIRED")}: ${ownerReq}` });

  const auditEvents = (audit?.events ?? []).filter(
    (e) => !auditFilter || e.event_type.toLowerCase().includes(auditFilter.toLowerCase()));

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("health.title")}</h1>
          <p className="muted page-desc">{t("sys.overall")}</p>
        </div>
        <button className="btn" onClick={refresh}>{t("common.refresh")}</button>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {partial && !sys && <p className="warn" role="note">{t("common.partial")}</p>}

      {/* Above fold: состояние Atlas + активный конвейер + следующий шаг */}
      <section className="panel hero na-glow" aria-labelledby="ov-h">
        <div className="hero-head">
          <h2 id="ov-h">{t("sys.overall")}</h2>
          <StatusBadge status={overallReady ? "READY" : "DEGRADED"}
            label={t(overallReady ? "status.READY" : "status.DEGRADED")} />
        </div>
        <HandoffTrace t={t} active={activeRuns > 0} />
        <p className="hero-line">
          {activeRuns > 0
            ? <>{t("sys.activePipeline")}: <b>{activeRuns}</b> · {t("sys.runsQueued")}: {sys?.runs.queued ?? 0}</>
            : <span className="muted">{t("sys.noActive")}</span>}
        </p>
        <div className="next-action">
          <span className="na-label">{t("sys.nextAction")}</span>
          <span className="na-text">{nextActionText(sys?.next_action, t)}</span>
        </div>
      </section>

      {/* Операционные риски (первыми) */}
      <section className="panel" aria-labelledby="risk-h">
        <h2 id="risk-h">{t("sys.risks")}</h2>
        {risks.length === 0 ? (
          <p className="ok-line"><span className="badge st-ok">● {t("sys.okAll")}</span></p>
        ) : (
          <ul className="risk-list">
            {risks.map((r, i) => (
              <li key={i} className={`risk ${r.cls}`}>
                <span className={`badge ${r.cls}`}><span aria-hidden="true">{r.sym}</span> {r.text}</span>
                {r.action && <span className="risk-action muted"> — {r.action}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Ресурсы и сервисы */}
      <section className="panel" aria-labelledby="res-h">
        <h2 id="res-h">{t("sys.resources")}</h2>
        {health && (
          <div className="svc-row">
            <span className="svc"><span className="kpi-label">{t("health.core")}</span>
              <StatusBadge status={health.status} label={t(statusKey(health.status))} /></span>
            <span className="svc"><span className="kpi-label">{t("health.runner")}</span>
              <StatusBadge status={runner} label={t(statusKey(runner))} /></span>
            <span className="svc"><span className="kpi-label">{t("health.db")}</span>
              <StatusBadge status={dbOk ? "READY" : "DEGRADED"}
                label={t(dbOk ? "status.READY" : "status.DEGRADED")} /></span>
            <span className="svc"><span className="kpi-label">{t("sys.web")}</span>
              <span className="badge st-ok" role="status"><span aria-hidden="true">●</span>{" "}
                {t("sys.webRendered")}</span></span>
          </div>
        )}
        {sys && (
          <div className="res-grid">
            <CpuGauge pct={sys.cpu.utilization_pct} window={sys.cpu.sample_window_s}
                      na={t("sys.cpuUnavailable")} label={t("sys.cpu")} />
            <Meter label={t("sys.memory")} used={sys.memory.used_bytes}
                   total={sys.memory.total_bytes} na={na} />
            <Meter label={t("sys.disk")} used={sys.disk.used_bytes}
                   total={sys.disk.total_bytes} na={na} />
          </div>
        )}
      </section>

      {/* Диагностика — раскрываемая (OS/kernel/arch/migration/machine/Web backend) */}
      {sys && (
        <details className="panel diag">
          <summary><h2 className="inline-h">{t("sys.diagnostics")}</h2>
            <span className="muted field-hint">{t("sys.diagnosticsHint")}</span></summary>
          <div className="kpi-grid kpi-grid-4">
            <Kpi label={t("sys.os")} value={`${sys.os.os_name ?? na} ${sys.os.os_version ?? ""}`.trim()} />
            <Kpi label={t("sys.kernel")} value={sys.os.kernel ?? na} mono />
            <Kpi label={t("sys.arch")} value={sys.os.arch ?? na} mono />
            <Kpi label={t("sys.machine")} value={sys.os.machine_id ?? na} mono />
            <Kpi label={t("sys.version")} value={sys.atlas_version} mono />
            <Kpi label={t("sys.migration")} value={sys.db_migration ?? na} mono />
            <Kpi label={t("sys.uptime")} value={fmtDuration(sys.host_uptime_s, na)} />
            <Kpi label={t("sys.backup")}
              value={sys.backup_age_s !== null ? fmtDuration(sys.backup_age_s, na) : t("sys.backupNone")} />
            <Kpi label={t("sys.writers")}
              value={sys.leases.worktree_writers !== null ? String(sys.leases.worktree_writers) : t("common.unknown")} />
            <Kpi label={t("sys.profileLeases")}
              value={sys.leases.profile_leases !== null ? String(sys.leases.profile_leases) : t("common.unknown")} />
            <div className="kpi">
              <span className="kpi-label">{t("sys.loadLabel")}</span>
              <span className="kpi-val mono">{sys.cpu.load_avg ? sys.cpu.load_avg.join(" / ") : na}</span>
              <span className="muted field-hint">{t("sys.loadNote")}
                {sys.cpu.logical_cores !== null && ` · ${sys.cpu.logical_cores} ${t("sys.cores")}`}</span>
            </div>
          </div>
          <p className="muted field-hint">{t("sys.web")}: {t("common.unknown")} — {t("sys.webBackend")}</p>
          <p className="muted field-hint">{t("sys.lastRefresh")}:{" "}
            <time dateTime={sys.collected_at}>{fmtLocal(sys.collected_at, locale)}</time></p>
        </details>
      )}

      {/* Audit — человекочитаемые метки + локальное время + фильтр (raw код сохранён) */}
      <section className="panel" aria-labelledby="a-h">
        <div className="hero-head">
          <h2 id="a-h">{t("audit.title")}</h2>
          {audit && <span className="muted">{t("audit.total")}: <span className="mono">{audit.total}</span></span>}
        </div>
        <label className="field inline">
          <span>{t("audit.filter")}</span>
          <input value={auditFilter} onChange={(e) => setAuditFilter(e.target.value)}
                 placeholder="review / profiles / runs…" />
        </label>
        {audit && auditEvents.length === 0 && <p className="muted">{t("audit.empty")}</p>}
        {audit && auditEvents.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr>
                <th scope="col">{t("audit.event")}</th>
                <th scope="col">{t("audit.actor")}</th>
                <th scope="col">{t("audit.time")}</th>
              </tr></thead>
              <tbody>
                {auditEvents.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <span className="badge st-info">{auditFamily(e.event_type, t)}</span>{" "}
                      <span className="mono muted audit-code">{e.event_type}</span>
                    </td>
                    <td>{e.actor}</td>
                    <td><time dateTime={e.created_at} title={`${e.created_at} UTC`}>
                      {fmtLocal(e.created_at, locale)}</time>{" "}
                      <span className="muted rel">· {fmtRelative(e.created_at, locale)}</span></td>
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
