// Типизированный каталог локалей (Master Spec §29.1).
// RU по умолчанию, EN переключением. Отсутствующий ключ ловит CI
// (scripts/check-i18n.mjs) и tsc: обе локали обязаны реализовать LocaleKey.

export const LOCALE_KEYS = [
  "app.title",
  "app.subtitle",
  "nav.pulse",
  "lang.ru",
  "lang.en",
  "lang.switch",
  "skip.toContent",
  "health.title",
  "health.core",
  "health.runner",
  "health.db",
  "health.version",
  "status.READY",
  "status.DEGRADED",
  "status.OFFLINE",
  "status.UNAUTHORIZED",
  "status.UNKNOWN",
  "runner.offlineHint",
  "audit.title",
  "audit.empty",
  "audit.total",
  "common.loading",
  "common.error",
  "common.refresh",
] as const;

export type LocaleKey = (typeof LOCALE_KEYS)[number];
export type Catalog = Record<LocaleKey, string>;
export type Locale = "ru" | "en";

import { ru } from "./locales/ru";
import { en } from "./locales/en";

export const catalogs: Record<Locale, Catalog> = { ru, en };

export function detectInitialLocale(): Locale {
  const saved = localStorage.getItem("atlas.locale");
  if (saved === "ru" || saved === "en") return saved;
  return "ru";
}
