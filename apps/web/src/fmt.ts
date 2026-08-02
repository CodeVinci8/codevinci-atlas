// Locale-aware форматирование времени (Master Spec §29.1, VP6-D3).
// API отдаёт UTC (ISO с Z). UI показывает локальное человекочитаемое время через
// Intl.DateTimeFormat + относительную форму («2 мин назад»), сохраняя точный UTC
// в <time datetime> / tooltip. Никакой ручной подстройки часовых поясов.

import type { Locale } from "./i18n";

const _fmtCache = new Map<string, Intl.DateTimeFormat>();

function fmt(locale: Locale): Intl.DateTimeFormat {
  const key = locale;
  let f = _fmtCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-US", {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    });
    _fmtCache.set(key, f);
  }
  return f;
}

/** Абсолютное локальное время, например `3 авг., 05:12`. Пустой вход → «—». */
export function fmtLocal(iso: string | null | undefined, locale: Locale): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return fmt(locale).format(d);
}

/** Относительное время, например `2 мин назад` / `2 min ago`. */
export function fmtRelative(iso: string | null | undefined, locale: Locale): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const rtf = new Intl.RelativeTimeFormat(locale === "ru" ? "ru-RU" : "en-US",
    { numeric: "auto" });
  const diffS = Math.round((d.getTime() - Date.now()) / 1000);
  const abs = Math.abs(diffS);
  if (abs < 60) return rtf.format(Math.round(diffS), "second");
  if (abs < 3600) return rtf.format(Math.round(diffS / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diffS / 3600), "hour");
  return rtf.format(Math.round(diffS / 86400), "day");
}

/** Компактная длительность в секундах → `2d 3h` / `4h 5m` / `6m`. */
export function fmtDuration(sec: number | null | undefined, na: string): string {
  if (sec === null || sec === undefined) return na;
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600),
    m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

/** Человекочитаемый размер: used/total → доля 0..1 (для баров) + строка. */
export function fmtBytes(n: number | null | undefined, na: string): string {
  if (n === null || n === undefined) return na;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let v = n, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}
