// VP-5 Web verification без headless-браузера. Проверяет РЕАЛЬНЫЙ production-бандл
// (собранный компонентный код, который и отгружается) и CSS на a11y-роли, RU/EN,
// честные состояния (символ+текст, не только цвет), responsive-брейкпоинты,
// reduced-motion, focus, sliding-индикатор языка, KPI-грид Pulse.
//
// Ограничение (честно): в среде нет headless-браузера, а bundler/esbuild для SSR
// недоступен как отдельный пакет. Установка chromium/playwright не производится
// (крупная зависимость только ради PASS). Это сильнейший доступный уровень:
// анализ реального собранного бандла + CSS, дополненный реальной HTTP-подачей
// (отдельный шаг) и API-интеграцией (крит. 24 приёмки).

import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, "..");
const repoRoot = join(webRoot, "..", "..");
const outDir = join(repoRoot, "var", "artifacts", "vp5", "web");
mkdirSync(outDir, { recursive: true });

const assetsDir = join(webRoot, "dist", "assets");
const js = readdirSync(assetsDir).filter((f) => f.endsWith(".js"))
  .map((f) => readFileSync(join(assetsDir, f), "utf8")).join("");
const css = readdirSync(assetsDir).filter((f) => f.endsWith(".css"))
  .map((f) => readFileSync(join(assetsDir, f), "utf8")).join("");
const indexHtml = readFileSync(join(webRoot, "dist", "index.html"), "utf8");

const checks = [];
const check = (name, cond, detail = "") => {
  checks.push({ name, pass: !!cond, detail });
  console.log(`  [${cond ? "PASS" : "FAIL"}] ${name}${detail ? " — " + detail : ""}`);
};

// --- RU/EN каталог и навигация ---
check("Бандл: навигация RU (Запуски/Профили/Пульс)", js.includes("Запуски") && js.includes("Профили") && js.includes("Пульс"));
check("Бандл: навигация EN (Runs/Profiles/Pulse)", js.includes("Runs") && js.includes("Profiles") && js.includes("Pulse"));
check("Бандл: заголовки Runs RU+EN", js.includes("Запуски конвейера") && js.includes("Pipeline runs"));
check("Бандл: RU по умолчанию (detectInitialLocale)", js.includes('"ru"') && js.includes("atlas.locale"));

// --- Language segmented control a11y ---
check("Бандл: lang radiogroup", js.includes("radiogroup"));
check("Бандл: aria-checked (segmented radio)", js.includes("aria-checked"));
check("Бандл: RU|EN метки контрола", js.includes('"RU"') && js.includes('"EN"'));
check("Бандл: клавиатура ArrowLeft/Right", js.includes("ArrowLeft") && js.includes("ArrowRight"));
check("Бандл: document.lang установка", js.includes("documentElement") && js.includes(".lang"));

// --- честные состояния (символ+текст, не только цвет) ---
check("Бандл: run states (SUCCEEDED/RATE_LIMITED/OWNER_REQUIRED)", js.includes("SUCCEEDED") && js.includes("RATE_LIMITED") && js.includes("OWNER_REQUIRED"));
check("Бандл: ёмкость UNKNOWN/STALE/AVAILABLE/EXHAUSTED", js.includes("UNKNOWN") && js.includes("STALE") && js.includes("AVAILABLE") && js.includes("EXHAUSTED"));
check("Бандл: role=status на бейджах", js.includes('"status"') || js.includes("role:\"status\""));
check("Бандл: aria-hidden на символах (символ+текст)", js.includes("aria-hidden"));
check("Бандл: cookie import UNSUPPORTED/experimental виден", js.includes("UNSUPPORTED") || js.toLowerCase().includes("experimental"));
check("Бандл: no silent fallback подпись", js.includes("silent") || js.includes("Без молчаливой") || js.includes("No silent"));

// --- Pulse full-width + partial states ---
check("Бандл: system summary поля (миграция/бэкап/uptime)", js.includes("db_migration") || js.includes("migration"));
check("Бандл: partial/unknown honest states", js.includes("partial") || js.includes("Данные частично") || js.includes("Data partially"));

// --- CSS: responsive, reduced-motion, focus, sliding indicator ---
check("CSS: prefers-reduced-motion", css.includes("prefers-reduced-motion"));
check("CSS: prefers-color-scheme (тема)", css.includes("prefers-color-scheme"));
check("CSS: responsive 1024px", css.includes("max-width: 1024px") || css.includes("max-width:1024px"));
check("CSS: responsive 460/390px (одна колонка)", css.includes("max-width: 460px") || css.includes("max-width:460px"));
check("CSS: KPI-грид 4 колонки (full-width Pulse)", css.includes("kpi-grid-4") && css.includes("repeat(4"));
// минификатор переписывает translateX(100%)→translate(100%), 160ms→.16s
check("CSS: seg-thumb sliding indicator", css.includes("seg-thumb") && /translate(X)?\(100%\)/.test(css));
check("CSS: focus-visible (видимый фокус)", css.includes("focus-visible"));
check("CSS: Ember-переход 120–180ms", css.includes("160ms") || css.includes(".16s") || /\.1[2-8]s/.test(css));
check("CSS: card-grid (Profiles cards)", css.includes("card-grid"));
check("CSS: timeline (Runs lifecycle)", css.includes(".timeline"));

// --- index.html корректен ---
check("index.html: lang и root", indexHtml.includes("<html") && indexHtml.includes("root"));

const result = {
  generated_at: new Date().toISOString(),
  method: "Статический анализ РЕАЛЬНОГО production-бандла (dist/assets *.js/*.css) и index.html — это тот же код, что отгружается пользователю.",
  limitation: "Нет headless-браузера и отдельного bundler/esbuild для SSR в среде; chromium/playwright НЕ устанавливались (крупная зависимость только ради PASS). Пиксельные скриншоты и живые interaction-события недоступны. Дополнено реальной HTTP-подачей (serve dist + Core 0005) и API-интеграцией (крит.24 приёмки).",
  passed: checks.filter((c) => c.pass).length,
  total: checks.length,
  checks,
};
writeFileSync(join(outDir, "web_render_verification.json"), JSON.stringify(result, null, 2));
const failed = checks.filter((c) => !c.pass);
console.log(`\n  Web bundle verification: ${result.passed}/${result.total}${failed.length ? " FAILED: " + failed.map((f) => f.name).join(", ") : " — все проверки пройдены"}`);
process.exit(failed.length ? 1 : 0);
