// Типизированный каталог локалей (Master Spec §29.1).
// RU по умолчанию, EN переключением. Отсутствующий ключ ловит CI
// (scripts/check-i18n.mjs) и tsc: обе локали обязаны реализовать LocaleKey.

export const LOCALE_KEYS = [
  "app.title",
  "app.subtitle",
  "nav.projects",
  "nav.pulse",
  "lang.ru",
  "lang.en",
  "lang.switch",
  "skip.toContent",
  // health / pulse
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
  // common
  "common.loading",
  "common.error",
  "common.refresh",
  "common.yes",
  "common.no",
  "common.none",
  "common.offline",
  "common.offlineHint",
  // projects list
  "projects.title",
  "projects.subtitle",
  "projects.connect",
  "projects.empty",
  "projects.emptyHint",
  "projects.col.name",
  "projects.col.source",
  "projects.col.branch",
  "projects.col.state",
  // source kinds
  "source.local_git",
  "source.github",
  "source.archive",
  "source.empty",
  // project states
  "state.clean",
  "state.dirty",
  "state.stale",
  "state.empty",
  "state.pending",
  "state.error",
  "state.disconnected",
  // connect form
  "connect.title",
  "connect.name",
  "connect.kind",
  "connect.path",
  "connect.github",
  "connect.archive",
  "connect.submit",
  "connect.cancel",
  "connect.hint.local_git",
  "connect.hint.github",
  "connect.hint.archive",
  "connect.hint.empty",
  // overview
  "overview.back",
  "overview.source",
  "overview.location",
  "overview.remotes",
  "overview.baseline",
  "overview.branch",
  "overview.head",
  "overview.tracked",
  "overview.changes",
  "overview.untracked",
  "overview.dirtyWarn",
  "overview.instructions",
  "overview.instr.path",
  "overview.instr.scope",
  "overview.instr.precedence",
  "overview.instr.read",
  "overview.instr.summary",
  "overview.packageManagers",
  "overview.commands",
  "overview.commandsNote",
  "overview.col.name",
  "overview.col.command",
  "overview.col.source",
  "overview.worktrees",
  "overview.wt.branch",
  "overview.wt.path",
  "overview.wt.status",
  "overview.lease",
  "overview.leaseActive",
  "overview.leaseNone",
  "overview.nextAction",
  "overview.createWorktree",
  "overview.refresh",
  "overview.disconnect",
  "overview.observedAt",
  "overview.contentHash",
  "overview.secretScan",
  "overview.noBaseline",
  "overview.branchPrompt",
  "overview.notFound",
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
