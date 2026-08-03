import { useCallback, useEffect, useState } from "react";
import { api, type Checkpoint, type CompareResult } from "./api";
import type { Locale, LocaleKey } from "./i18n";
import { fmtLocal } from "./fmt";

type T = (key: LocaleKey) => string;

export function TimeMachineView({ t, locale }: { t: T; locale: Locale }) {
  const [rows, setRows] = useState<Checkpoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [cmp, setCmp] = useState<CompareResult | null>(null);
  const [preview, setPreview] = useState<{ id: string; data: Record<string, unknown> } | null>(null);

  const refresh = useCallback(async () => {
    try { setRows((await api.listAtlasCheckpoints()).checkpoints); setError(null); }
    catch { setError(t("common.error")); }
  }, [t]);
  useEffect(() => { refresh(); }, [refresh]);

  const toggle = (id: string) => setSel((s) => {
    if (s.includes(id)) return s.filter((x) => x !== id);
    return s.length >= 2 ? [s[1], id] : [...s, id];
  });

  useEffect(() => {
    if (sel.length === 2) {
      api.compareCheckpoints(sel[0], sel[1]).then((r) => setCmp(r.compare)).catch(() => setCmp(null));
    } else { setCmp(null); }
  }, [sel]);

  const doRestorePreview = async (id: string) => {
    try {
      const r = await api.rollbackPreview(id, "");
      setPreview({ id, data: r.preview });
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  const current = rows && rows.length > 0 ? rows[0].id : null;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t("tm.title")}</h1>
          <p className="muted page-desc">{t("tm.subtitle")}</p>
        </div>
        <button className="btn" onClick={refresh}>{t("common.refresh")}</button>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {rows === null && !error && <p className="muted">{t("common.loading")}</p>}

      <p className="muted field-hint">
        <span className="badge st-ok"><span aria-hidden="true">●</span> {t("tm.readOnly")}</span>{" "}
        {t("tm.replayNote")} {t("tm.noCredentials")}
      </p>

      {/* Хронология чекпоинтов */}
      <section className="panel" aria-labelledby="tl-h">
        <h2 id="tl-h">{t("tm.timeline")}</h2>
        {rows && rows.length === 0 && <p className="muted">{t("tm.noCheckpoints")}</p>}
        {rows && rows.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr>
                <th scope="col">{t("tm.compare")}</th>
                <th scope="col">{t("tm.col.cause")}</th>
                <th scope="col">{t("tm.col.branch")}</th>
                <th scope="col">{t("tm.col.head")}</th>
                <th scope="col">{t("tm.col.profile")}</th>
                <th scope="col">{t("tm.col.created")}</th>
                <th scope="col"></th>
              </tr></thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} className={c.id === current ? "tm-current" : ""}>
                    <td>
                      <label className="chip toggle">
                        <input type="checkbox" checked={sel.includes(c.id)}
                          onChange={() => toggle(c.id)} aria-label={`compare ${c.id}`} />
                        {c.id === current && <span className="badge st-info"><span aria-hidden="true">◆</span> {t("tm.current")}</span>}
                      </label>
                    </td>
                    <td>{c.cause || "—"}</td>
                    <td className="mono small">{c.branch}</td>
                    <td className="mono">{c.head_sha.slice(0, 10) || "—"}</td>
                    <td className="mono small">{c.profile_alias}<br /><span className="muted">{c.model}{c.effort ? `/${c.effort}` : ""}</span></td>
                    <td className="mono small"><time dateTime={c.created_at} title={`${c.created_at} UTC`}>{fmtLocal(c.created_at, locale)}</time></td>
                    <td>
                      <button className="btn btn-sm" onClick={() => doRestorePreview(c.id)}>{t("tm.rollbackPreview")}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Сравнение двух чекпоинтов */}
      <section className="panel" aria-labelledby="cmp-h">
        <h2 id="cmp-h">{t("tm.compare")}</h2>
        {sel.length < 2 ? <p className="muted">{t("tm.selectTwo")}</p> : cmp && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th scope="col">—</th><th scope="col">A</th><th scope="col">B</th><th scope="col"></th></tr></thead>
              <tbody>
                {[["tm.diff.head", "head_sha"], ["tm.diff.grant", "grant"], ["tm.diff.profile", "profile"],
                  ["tm.diff.model", "model"], ["tm.diff.artifacts", "artifacts"],
                  ["tm.diff.tests", "tests_evidence"], ["tm.diff.outcome", "outcome"]].map(([label, key]) => {
                  const d = cmp.diffs[key];
                  return (
                    <tr key={key}>
                      <td>{t(label as LocaleKey)}</td>
                      <td className="mono small">{d?.a ?? "—"}</td>
                      <td className="mono small">{d?.b ?? "—"}</td>
                      <td>
                        <span className={`badge ${d?.changed ? "st-warn" : "st-ok"}`}>
                          <span aria-hidden="true">{d?.changed ? "▲" : "●"}</span> {t(d?.changed ? "tm.changed" : "tm.unchanged")}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Превью rollback (read-only, destructive недоступен без grant) */}
      {preview && (
        <section className="panel" aria-labelledby="pv-h">
          <div className="hero-head">
            <h2 id="pv-h">{t("tm.rollbackPreview")}</h2>
            <button className="btn btn-sm" onClick={() => setPreview(null)}>✕</button>
          </div>
          <p><span className="badge st-ok"><span aria-hidden="true">●</span> {t("tm.readOnly")}</span></p>
          {!(preview.data as { available?: boolean }).available && (
            <p className="warn" role="note">
              <span className="badge st-warn"><span aria-hidden="true">▲</span> {t("tm.destructiveUnavailable")}</span>
            </p>
          )}
          <dl className="kv">
            <dt>{t("auto.reason")}</dt><dd className="mono">{String((preview.data as { reason?: string }).reason ?? "")}</dd>
            <dt>{t("overview.nextAction")}</dt><dd>{String((preview.data as { next_action?: string }).next_action ?? "")}</dd>
          </dl>
        </section>
      )}
    </>
  );
}
