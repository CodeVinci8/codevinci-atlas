import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type AuditPage, type Health, type HealthStatus } from "./api";
import { catalogs, detectInitialLocale, type Locale, type LocaleKey } from "./i18n";

function useT(locale: Locale) {
  return useMemo(() => {
    const cat = catalogs[locale];
    return (key: LocaleKey) => cat[key];
  }, [locale]);
}

function statusKey(s: HealthStatus): LocaleKey {
  switch (s) {
    case "READY": return "status.READY";
    case "DEGRADED": return "status.DEGRADED";
    case "OFFLINE": return "status.OFFLINE";
    case "UNAUTHORIZED": return "status.UNAUTHORIZED";
    default: return "status.UNKNOWN";
  }
}

// Статус никогда не только цветом (§29.3): цвет + текст + иконка-символ.
function StatusBadge({ status, label }: { status: HealthStatus; label: string }) {
  const symbol = status === "READY" ? "●" : status === "DEGRADED" ? "▲" : "■";
  return (
    <span className={`badge badge-${status}`} role="status">
      <span aria-hidden="true">{symbol}</span> {label}
    </span>
  );
}

export function App() {
  const [locale, setLocale] = useState<Locale>(detectInitialLocale());
  const [health, setHealth] = useState<Health | null>(null);
  const [audit, setAudit] = useState<AuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const t = useT(locale);

  useEffect(() => {
    document.documentElement.lang = locale; // активный язык (§29.3)
    localStorage.setItem("atlas.locale", locale);
    document.title = catalogs[locale]["app.title"];
  }, [locale]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [h, a] = await Promise.all([api.health(), api.audit()]);
      setHealth(h);
      setAudit(a);
      setError(null);
    } catch {
      setError(t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <>
      <a href="#main" className="skip-link">{t("skip.toContent")}</a>
      <header className="topbar">
        <div className="brand">
          <strong>{t("app.title")}</strong>
          <span className="muted">{t("app.subtitle")}</span>
        </div>
        <div className="lang" role="group" aria-label={t("lang.switch")}>
          <button aria-pressed={locale === "ru"} onClick={() => setLocale("ru")}>{t("lang.ru")}</button>
          <button aria-pressed={locale === "en"} onClick={() => setLocale("en")}>{t("lang.en")}</button>
        </div>
      </header>

      <main id="main" className="content">
        <section aria-labelledby="health-h" className="card">
          <div className="card-head">
            <h1 id="health-h">{t("health.title")}</h1>
            <button className="refresh" onClick={refresh}>{t("common.refresh")}</button>
          </div>

          {loading && !health && <p className="muted">{t("common.loading")}</p>}
          {error && <p className="error" role="alert">{error}</p>}

          {health && (
            <>
              <ul className="health-grid">
                <li>
                  <span className="label">{t("health.core")}</span>
                  <StatusBadge status={health.status} label={t(statusKey(health.status))} />
                </li>
                <li>
                  <span className="label">{t("health.runner")}</span>
                  <StatusBadge status={health.runner.status} label={t(statusKey(health.runner.status))} />
                </li>
                <li>
                  <span className="label">{t("health.db")}</span>
                  <StatusBadge
                    status={health.core.db.ok ? "READY" : "DEGRADED"}
                    label={t(health.core.db.ok ? "status.READY" : "status.DEGRADED")}
                  />
                </li>
                <li>
                  <span className="label">{t("health.version")}</span>
                  <span className="mono">{health.version}</span>
                </li>
              </ul>
              {health.runner.status !== "READY" && (
                <p className="hint" role="note">{t("runner.offlineHint")}</p>
              )}
            </>
          )}
        </section>

        <section aria-labelledby="audit-h" className="card">
          <h2 id="audit-h">{t("audit.title")}</h2>
          {audit && (
            <p className="muted">{t("audit.total")}: <span className="mono">{audit.total}</span></p>
          )}
          {audit && audit.events.length === 0 && <p className="muted">{t("audit.empty")}</p>}
          {audit && audit.events.length > 0 && (
            <table className="audit">
              <thead>
                <tr>
                  <th scope="col">{t("audit.title")}</th>
                  <th scope="col">actor</th>
                  <th scope="col">time (UTC)</th>
                </tr>
              </thead>
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
          )}
        </section>
      </main>
    </>
  );
}
