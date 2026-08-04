import { useCallback, useEffect, useState } from "react";
import { api, type AuthHealthRow, type CapacityView, type CapacityWindow,
         type ClaudePoolSummary, type ProfileView } from "./api";
import type { Locale, LocaleKey } from "./i18n";
import { fmtLocal, fmtRelative } from "./fmt";

type T = (key: LocaleKey) => string;

// Значение auth-статуса берётся из read-only CLI-пробы и НЕ зависит от ёмкости.
const AUTH_CLS: Record<string, { sym: string; cls: string }> = {
  READY: { sym: "●", cls: "st-ok" },
  AUTH_REQUIRED: { sym: "▲", cls: "st-warn" },
  AUTH_EXPIRED: { sym: "◆", cls: "st-warn" },
  STALE: { sym: "◇", cls: "st-muted" },
  UNKNOWN: { sym: "○", cls: "st-muted" },
};
function authLabel(status: string, t: T): string {
  return t((`auth.${status}`) as LocaleKey) ?? status;
}
function AuthBadge({ status, t }: { status: string; t: T }) {
  const m = AUTH_CLS[status] ?? { sym: "○", cls: "st-muted" };
  return (
    <span className={`badge ${m.cls}`} role="status">
      <span aria-hidden="true">{m.sym}</span> {authLabel(status, t)}
    </span>
  );
}

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

// Читаемая метка плана: "Codex Plus" / "Claude Pro".
function planLabel(provider: string, plan: string | undefined, t: T): string {
  const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  if (!plan) return t("profiles.noPlan");
  return `${cap(provider)} ${cap(plan)}`;
}

function winLabel(w: CapacityWindow, t: T): string {
  return t((`cap.win.${w.id}`) as LocaleKey) ?? w.label;
}

// Одно окно: числовое (Codex used/remaining %+бар) ИЛИ статус-окно (Claude
// allowed/warning/rejected без фикции %). Плюс подпись, сброс и обратный отсчёт.
function WindowRow({ w, t, locale }: { w: CapacityWindow; t: T; locale: Locale }) {
  const used = w.used_pct;
  const status = (w.status ?? "").toLowerCase();
  const hasPct = used !== null;
  // Уровень: из % (Codex) или из статуса (Claude).
  const level = hasPct
    ? (used >= 100 ? "crit" : used >= 80 ? "warn" : "ok")
    : (status === "rejected" ? "crit" : status === "warning" ? "warn"
       : status === "allowed" ? "ok" : "muted");
  const sym = level === "crit" ? "■" : level === "warn" ? "▲" : level === "ok" ? "●" : "○";
  const stCls = level === "ok" ? "ok" : level === "warn" ? "warn" : level === "crit" ? "danger" : "muted";
  // Текст значения: % для Codex; локализованный статус для Claude.
  const valText = hasPct
    ? `${used}% ${t("profiles.used")}`
    : status ? t((`cap.status.${status}`) as LocaleKey) : "—";
  // Ширина бара: из % (Codex); для статус-окна — полный при исчерпании, иначе пустой.
  const fillPct = hasPct ? Math.max(0, Math.min(100, used)) : (status === "rejected" ? 100 : 0);
  return (
    <div className="cap-win">
      <div className="cap-win-top">
        <span className="cap-win-label">{winLabel(w, t)}</span>
        <span className={`cap-win-pct mono st-${stCls}`}>
          <span aria-hidden="true">{sym}</span> {valText}
          {hasPct && w.remaining_pct !== null && <> · {w.remaining_pct}% {t("profiles.remaining")}</>}
        </span>
      </div>
      <div className={`cap-bar level-${level}`} role="img"
           aria-label={`${winLabel(w, t)}: ${valText}`}>
        <span className="cap-fill" style={{ width: `${fillPct}%` }} />
      </div>
      {(w.reset_at || w.reset_text) && (
        <p className="muted cap-reset">
          {t("profiles.resets")}:{" "}
          {w.reset_at
            ? <time dateTime={w.reset_at} title={`${w.reset_at} UTC`}>
                {fmtLocal(w.reset_at, locale)} <span className="rel">· {fmtRelative(w.reset_at, locale)}</span>
              </time>
            : <span>{w.reset_text}</span>}
        </p>
      )}
    </div>
  );
}

// Точное объяснение, когда числовых окон нет (не немой UNKNOWN).
function capErrExplain(code: string | undefined, provider: string, t: T): string {
  if (!code) return t("profiles.noNumeric");
  const key = (`caperr.${code}`) as LocaleKey;
  const specific = t(key);
  if (specific && specific !== key) return specific;
  if (provider === "claude") return t("caperr.CLAUDE_GENERIC");
  return t("profiles.noNumeric");
}

type RefreshState = { kind: "idle" | "busy" | "ok" | "cooldown" | "inprogress" | "error";
                      detail?: string };

function CapacityBlock({ p, cap, refresh, startWindow, rstate, t, locale }: {
  p: ProfileView; cap: CapacityView; refresh: () => void; startWindow: () => void;
  rstate: RefreshState; t: T; locale: Locale;
}) {
  const windows = cap.windows ?? [];
  const stale = cap.stale || cap.status === "STALE";
  const checked = cap.observed_at;
  const dataAt = cap.data_observed_at ?? cap.observed_at;
  const isClaude = p.provider === "claude";
  const busy = rstate.kind === "busy";
  return (
    <div className="p-cap">
      <div className="p-cap-head">
        <span className="label">{t("profiles.capacity")}</span>
        <div className="cap-actions">
          <button className="btn btn-sm" onClick={refresh} disabled={busy}
                  aria-label={t("profiles.refreshCap")}>
            {busy ? t("profiles.refreshing") : t("profiles.refreshCap")}
          </button>
          {isClaude && (
            <button className="btn btn-sm" onClick={startWindow} disabled={busy}
                    title={t("profiles.startWindowHint")} aria-label={t("profiles.startWindow")}>
              {t("profiles.startWindow")}
            </button>
          )}
        </div>
      </div>
      {isClaude && <p className="muted small cap-note">{t("profiles.startWindowHint")}</p>}

      {stale && (
        <p className="cap-stale" role="note">
          <span aria-hidden="true">◇</span> {t("profiles.staleData")}
          {cap.error_code && <span className="mono small"> · {cap.error_code}</span>}
        </p>
      )}

      {windows.length > 0
        ? windows.map((w) => <WindowRow key={w.id} w={w} t={t} locale={locale} />)
        : (
          <div className="cap-unavail">
            <p><span aria-hidden="true">○</span> {capErrExplain(cap.error_code, p.provider, t)}</p>
            <p className="muted small mono">
              {cap.source && <>{t("profiles.source")}: {cap.source}</>}
              {cap.error_code && <> · {cap.error_code}</>}
            </p>
          </div>
        )}

      <p className="muted small cap-meta">
        {cap.source && <>{t("profiles.source")}: <span className="mono">{cap.source}</span> · </>}
        {checked
          ? <>{t("profiles.checked")}:{" "}
              <time dateTime={checked} title={`${checked} UTC`}>{fmtRelative(checked, locale)}</time></>
          : t("profiles.noCheck")}
        {stale && dataAt && dataAt !== checked && (
          <> · {t("profiles.dataAge")}:{" "}
            <time dateTime={dataAt} title={`${dataAt} UTC`}>{fmtRelative(dataAt, locale)}</time></>)}
      </p>

      {rstate.kind === "cooldown" && (
        <p className="muted small" role="status">{t("profiles.cooldownWait")}
          {rstate.detail && <> ({rstate.detail}s)</>}</p>)}
      {rstate.kind === "inprogress" && (
        <p className="muted small" role="status">{t("profiles.refreshInProgress")}</p>)}
      {rstate.kind === "error" && (
        <p className="warn small" role="alert">{t("common.error")}</p>)}
    </div>
  );
}

function ProfileCard({ p, auth, refresh, startWindow, rstate, t, locale }: {
  p: ProfileView; auth: string; refresh: () => void; startWindow: () => void;
  rstate: RefreshState; t: T; locale: Locale;
}) {
  // Отключённый профиль (истёкшая подписка): приглушён, без ёмкости/действий,
  // с явной причиной — но исторически присутствует (soft removal, §2).
  const disabled = p.enabled === false || p.state === "DISABLED";
  return (
    <article className={`p-card${disabled ? " p-card-disabled" : ""}`}>
      <header className="p-card-h">
        <span className="mono p-alias">{p.alias}</span>
        <span className="muted p-prov">{p.provider}</span>
      </header>
      {disabled ? (
        <>
          <div className="p-row"><StateBadge state="DISABLED" t={t} /></div>
          <p className="muted small p-disabled-note">{t("profiles.disabledNote")}</p>
        </>
      ) : (
        <>
          {/* auth truth — независимо от ёмкости */}
          <div className="p-row"><AuthBadge status={auth} t={t} />
            <StateBadge state={p.state} t={t} /></div>
          <div className="p-row">
            <span className="label">{t("profiles.plan")}</span>
            <span>{planLabel(p.provider, p.capacity.plan || p.health?.plan_label, t)}</span>
          </div>
          {p.active_lease && (
            <div className="p-row"><span className="label">{t("profiles.currentRun")}</span>
              <span className="mono">{p.active_lease.role}</span></div>
          )}
          <CapacityBlock p={p} cap={p.capacity} refresh={refresh} startWindow={startWindow}
                         rstate={rstate} t={t} locale={locale} />
        </>
      )}
    </article>
  );
}

// Компактная сводка Claude Builder-пула (без фиктивного объединённого %).
function ClaudePoolCard({ pool, t, locale }: { pool: ClaudePoolSummary; t: T; locale: Locale }) {
  return (
    <section className="panel pool-summary" aria-labelledby="pool-h">
      <h2 id="pool-h">{t("pool.title")}</h2>
      <div className="pool-grid">
        <div className="pool-kpi"><span className="kpi-label">{t("pool.authorized")}</span>
          <span className="mono">{pool.authorized_count}/{pool.members.length}</span></div>
        <div className="pool-kpi"><span className="kpi-label">{t("pool.eligible")}</span>
          <span className="mono">{pool.eligible_count}</span></div>
        <div className="pool-kpi"><span className="kpi-label">{t("pool.active")}</span>
          <span className="mono">{pool.active_alias || t("common.none")}</span></div>
        <div className="pool-kpi"><span className="kpi-label">{t("pool.nextReset")}</span>
          <span className="mono">{pool.next_reset
            ? <time dateTime={pool.next_reset}>{fmtRelative(pool.next_reset, locale)}</time>
            : "—"}</span></div>
      </div>
      {pool.last_reason && <p className="muted small">{t("pool.lastReason")}: <span className="mono">{pool.last_reason}</span></p>}
      {pool.conservative_fallback && <p className="muted small">{t("pool.fallback")}</p>}
      <p className="muted small">{t("pool.note")}</p>
    </section>
  );
}

export function ProfilesView({ t, locale }: { t: T; locale: Locale }) {
  const [rows, setRows] = useState<ProfileView[] | null>(null);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"cards" | "table">("cards");
  const [filter, setFilter] = useState<string>("");
  const [authHealth, setAuthHealth] = useState<AuthHealthRow[]>([]);
  const [rstates, setRstates] = useState<Record<string, RefreshState>>({});
  const [pool, setPool] = useState<ClaudePoolSummary | null>(null);

  const refresh = useCallback(async () => {
    try { const r = await api.listProfiles(); setRows(r.profiles); setSummary(r.summary); setError(null); }
    catch { setError(t("common.error")); }
    try { setAuthHealth((await api.authHealthReport()).auth_health); } catch { /* optional */ }
    try { setPool((await api.claudePoolSummary()).claude_pool); } catch { /* optional */ }
  }, [t]);
  useEffect(() => { refresh(); }, [refresh]);

  const authByAlias: Record<string, string> = {};
  for (const a of authHealth) authByAlias[a.alias] = a.auth_status;

  const refreshCap = useCallback(async (alias: string) => {
    setRstates((s) => ({ ...s, [alias]: { kind: "busy" } }));
    try {
      const res = await api.refreshCapacity(alias);
      const row = res.refreshed.find((r) => r.alias === alias) ?? res.refreshed[0];
      const st = row?.state;
      if (st === "COOLDOWN")
        setRstates((s) => ({ ...s, [alias]: { kind: "cooldown",
          detail: row?.cooldown_remaining_s ? String(Math.ceil(row.cooldown_remaining_s)) : undefined } }));
      else if (st === "REFRESH_IN_PROGRESS")
        setRstates((s) => ({ ...s, [alias]: { kind: "inprogress" } }));
      else { setRstates((s) => ({ ...s, [alias]: { kind: "ok" } })); await refresh(); }
    } catch {
      setRstates((s) => ({ ...s, [alias]: { kind: "error" } }));
    }
  }, [refresh]);

  // «Начать окно и обновить» — owner-действие для Claude (тратит немного подписки).
  const startWindow = useCallback(async (alias: string) => {
    setRstates((s) => ({ ...s, [alias]: { kind: "busy" } }));
    try {
      const res = await api.startWindow(alias);
      const row = res.started.find((r) => r.alias === alias) ?? res.started[0];
      const st = row?.state;
      if (st === "COOLDOWN")
        setRstates((s) => ({ ...s, [alias]: { kind: "cooldown",
          detail: row?.cooldown_remaining_s ? String(Math.ceil(row.cooldown_remaining_s)) : undefined } }));
      else if (st === "REFRESH_IN_PROGRESS")
        setRstates((s) => ({ ...s, [alias]: { kind: "inprogress" } }));
      else { setRstates((s) => ({ ...s, [alias]: { kind: "ok" } })); await refresh(); }
    } catch {
      setRstates((s) => ({ ...s, [alias]: { kind: "error" } }));
    }
  }, [refresh]);

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

      {pool && pool.members.length > 0 && <ClaudePoolCard pool={pool} t={t} locale={locale} />}

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
        <div className="empty-state compact">
          <p>{t("profiles.empty")}</p>
          <p className="muted">{t("profiles.emptyHint")}</p>
          <a className="btn-primary" href="#onb-h">{t("profiles.onboarding")}</a>
        </div>
      )}

      {rows && rows.length > 0 && mode === "cards" && (
        <div className="card-grid">
          {shown.map((p) => (
            <ProfileCard key={p.id} p={p} auth={authByAlias[p.alias] ?? "UNKNOWN"}
              refresh={() => refreshCap(p.alias)} startWindow={() => startWindow(p.alias)}
              rstate={rstates[p.alias] ?? { kind: "idle" }} t={t} locale={locale} />
          ))}
        </div>
      )}

      {rows && rows.length > 0 && mode === "table" && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col">{t("profiles.col.alias")}</th>
              <th scope="col">{t("profiles.col.provider")}</th>
              <th scope="col">{t("ah.title")}</th>
              <th scope="col">{t("profiles.col.plan")}</th>
              <th scope="col">{t("profiles.col.capacity")}</th>
              <th scope="col">{t("ah.observed")}</th>
            </tr></thead>
            <tbody>
              {shown.map((p) => {
                const cap = p.capacity;
                const win = (cap.windows ?? [])[0];
                return (
                  <tr key={p.id}>
                    <td className="mono">{p.alias}</td>
                    <td>{p.provider}</td>
                    <td><AuthBadge status={authByAlias[p.alias] ?? "UNKNOWN"} t={t} /></td>
                    <td>{planLabel(p.provider, cap.plan || p.health?.plan_label, t)}</td>
                    <td>{win
                      ? <span className="mono">{winLabel(win, t)} {win.used_pct}%/{win.remaining_pct}%</span>
                      : <span className="muted small">{capErrExplain(cap.error_code, p.provider, t)}</span>}
                    </td>
                    <td className="small">{cap.observed_at
                      ? <time dateTime={cap.observed_at}>{fmtRelative(cap.observed_at, locale)}</time>
                      : "—"}</td>
                  </tr>
                );
              })}
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
