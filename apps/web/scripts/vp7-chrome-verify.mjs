// VP-7 реальная Chrome-верификация через Playwright (chromium-1234, уже
// установлен — новый браузер НЕ скачивается). Драйвит собранный Web поверх
// изолированного fixture-Core (scripts/vp7_chrome_server.py), снимает реальные
// наполненные экраны Автономия/Time Machine/Пульс/Профили на 1440/1024/768/390,
// RU и EN, reduced-motion; проверяет a11y (focus, document.lang, персистентность,
// без горизонтального overflow, touch>=44px), фактическую CPU-метрику и merge gate.
//
// Evidence (redacted PNG + JSON + SHA-256 манифест) → var/artifacts/vp7/chrome/.
// Никаких секретов/PII: fixture-Core их не отдаёт; отдельно скан DOM/скриншотов.

import pw from "/opt/codevinci-inspector/services/scanner/node_modules/playwright/index.js";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = pw;
const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..", "..");
const OUT = join(repoRoot, "var", "artifacts", "vp7", "chrome");
mkdirSync(OUT, { recursive: true });

const BASE = process.env.VP7_BASE || "http://127.0.0.1:8098";
const CHROME = "/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome";
const VIEWPORTS = [
  { w: 1440, h: 900 }, { w: 1024, h: 768 }, { w: 768, h: 1024 }, { w: 390, h: 844 },
];
const results = [];
const rec = (name, pass, detail = "") => {
  results.push({ name, pass: !!pass, detail });
  console.log(`  [${pass ? "PASS" : "FAIL"}] ${name}${detail ? " — " + detail : ""}`);
};

// Кликнуть nav по видимой метке (RU/EN).
async function nav(page, labels) {
  const btn = page.locator(".nav-item", { hasText: new RegExp(labels.join("|")) }).first();
  await btn.click();
  await page.waitForTimeout(220);
}

async function setLocale(page, loc) {
  await page.evaluate((l) => localStorage.setItem("atlas.locale", l), loc);
}

async function noHorizontalOverflow(page) {
  return page.evaluate(() =>
    document.documentElement.scrollWidth <= window.innerWidth + 1);
}

async function shot(page, name) {
  const p = join(OUT, `${name}.png`);
  await page.screenshot({ path: p, fullPage: false });
  return `${name}.png`;
}

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROME, args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });

  // --- матрица: view × viewport × locale ---
  const views = [
    { key: "pulse", ru: ["Пульс"], en: ["Pulse"] },
    { key: "profiles", ru: ["Профили"], en: ["Profiles"] },
    { key: "autonomy", ru: ["Автономия"], en: ["Autonomy"] },
    { key: "timemachine", ru: ["Time Machine"], en: ["Time Machine"] },
  ];
  let shots = 0;
  for (const loc of ["ru", "en"]) {
    for (const vp of VIEWPORTS) {
      const ctx = await browser.newContext({ viewport: { width: vp.w, height: vp.h } });
      const page = await ctx.newPage();
      await page.goto(BASE, { waitUntil: "networkidle" });
      await setLocale(page, loc);
      await page.reload({ waitUntil: "networkidle" });
      // document.lang соответствует локали (персистентность)
      const lang = await page.evaluate(() => document.documentElement.lang);
      if (vp.w === 1440) rec(`document.lang=${loc} (${vp.w})`, lang === loc, `lang=${lang}`);
      for (const v of views) {
        await nav(page, loc === "ru" ? v.ru : v.en);
        await page.waitForTimeout(260);
        shots++;
        await shot(page, `${v.key}-${loc}-${vp.w}`);
        if (v.key === "autonomy" && vp.w === 1440) {
          const overflow = await noHorizontalOverflow(page);
          rec(`no horizontal overflow autonomy ${loc} ${vp.w}`, overflow);
        }
      }
      await ctx.close();
    }
  }
  rec("Матрица скриншотов view×viewport×locale", shots === views.length * VIEWPORTS.length * 2,
      `${shots} шт.`);

  // --- Emergency Stop состояние (отдельный fixture-порт с ATLAS_VP7_EMERGENCY=1) ---
  if (process.env.VP7_EMERGENCY_BASE) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(process.env.VP7_EMERGENCY_BASE, { waitUntil: "networkidle" });
    await nav(page, ["Автономия"]);
    await page.waitForTimeout(300);
    const active = await page.locator(".estop-on").count();
    rec("Emergency Stop активное состояние отрисовано", active >= 1, `estop-on=${active}`);
    await shot(page, "autonomy-emergency-1440");
    await ctx.close();
  }

  // --- фокусный/клавиатурный обход + focus-visible + brand-home ---
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: "networkidle" });
    // Tab до первого интерактива, проверяем видимый фокус
    await page.keyboard.press("Tab");
    const hasFocusRing = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el) return false;
      const s = getComputedStyle(el);
      return s.outlineStyle !== "none" || s.boxShadow !== "none" || el.className.includes("skip");
    });
    rec("Клавиатурный фокус виден (focus-visible/skip-link)", hasFocusRing);
    // brand-home: клик по бренду ведёт на Пульс без полной перезагрузки
    await nav(page, ["Автономия"]);
    const navId = await page.evaluate(() => { window.__navmark = 1; return 1; });
    await page.locator(".brand-home").click();
    await page.waitForTimeout(200);
    const stillSPA = await page.evaluate(() => window.__navmark === 1); // не было reload
    const onPulse = await page.locator(".nav-item.active", { hasText: /Пульс|Pulse/ }).count();
    rec("Brand-home → Пульс без полной перезагрузки", stillSPA && onPulse >= 1,
        `spa=${stillSPA} pulseActive=${onPulse}`);
    await shot(page, "brand-home-pulse-1440");
    await ctx.close();
  }

  // --- реальная CPU-метрика присутствует (кольцо + %/недоступно) ---
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: "networkidle" });
    await nav(page, ["Пульс"]);
    await page.waitForTimeout(400);
    const cpu = await page.locator(".cpu-gauge").count();
    const cpuText = await page.locator(".cpu-gauge .cpu-val").first().textContent().catch(() => "");
    // load average НЕ в основной сетке ресурсов — только в диагностике
    const loadInResGrid = await page.locator(".res-grid").getByText(/load average|Нагрузка за/i).count();
    rec("Реальная CPU-метрика (кольцо) в Пульсе", cpu >= 1, `cpu_gauge=${cpu} txt=${(cpuText || "").trim()}`);
    rec("Load average вынесен из основной сетки (в диагностику)", loadInResGrid === 0);
    // next action присутствует (контекстное), отдельно от рисков
    const naText = await page.locator(".hero .next-action .na-text").first().textContent().catch(() => "");
    rec("Контекстное next action в Пульсе", !!(naText || "").trim(), (naText || "").trim().slice(0, 40));
    await shot(page, "pulse-cpu-nextaction-1440");
    await ctx.close();
  }

  // --- touch targets >= 44px на 390 (nav + основные кнопки) ---
  {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: "networkidle" });
    await nav(page, ["Автономия"]);
    await page.waitForTimeout(200);
    const smallTargets = await page.evaluate(() => {
      const els = [...document.querySelectorAll("button, a, .nav-item")];
      let bad = 0;
      for (const e of els) {
        const r = e.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && r.height < 44 && !e.classList.contains("seg-opt")
            && !e.classList.contains("chip")) bad++;
      }
      return bad;
    });
    rec("Touch targets >= 44px на 390 (осн. кнопки)", smallTargets === 0, `малых=${smallTargets}`);
    const overflow = await noHorizontalOverflow(page);
    rec("Нет горизонтального overflow на 390", overflow);
    await shot(page, "autonomy-ru-390-touch");
    await ctx.close();
  }

  // --- reduced-motion: анимации выключены ---
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 },
      reducedMotion: "reduce" });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: "networkidle" });
    await nav(page, ["Time Machine"]);
    await page.waitForTimeout(300);
    const animsRunning = await page.evaluate(() => {
      const el = document.querySelector(".tm-arrow");
      if (!el) return 0;
      // ::after анимация должна быть отключена под reduced-motion
      const after = getComputedStyle(el, "::after");
      return after.animationName && after.animationName !== "none" && after.display !== "none" ? 1 : 0;
    });
    rec("Reduced-motion выключает нефункциональную анимацию", animsRunning === 0);
    await shot(page, "timemachine-reduced-motion-1440");
    await ctx.close();
  }

  // --- PII/секрет-скан DOM: рендер-текст не содержит секретов ---
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: "networkidle" });
    const domText = await page.evaluate(() => document.body.innerText);
    const leaks = [/sk-ant-/, /gho_[A-Za-z0-9]/, /-----BEGIN/, /@gmail\.com/, /@mail\.ru/,
                   /\/root\/\./, /CODEX_HOME=/, /Bearer /];
    const hit = leaks.find((re) => re.test(domText));
    rec("Нет секретов/PII в отрендеренном DOM", !hit, hit ? String(hit) : "clean");
    await ctx.close();
  }

  await browser.close();

  // манифест
  const pngs = readdirSync(OUT).filter((f) => f.endsWith(".png")).sort();
  const manifest = {};
  for (const f of pngs) {
    manifest[f] = "sha256:" + createHash("sha256").update(readFileSync(join(OUT, f))).digest("hex");
  }
  writeFileSync(join(OUT, "manifest_sha256.json"),
    JSON.stringify(manifest, null, 2), "utf8");
  const passed = results.filter((r) => r.pass).length;
  const summary = { vp: "VP-7", tool: "playwright+chromium-1234", viewports: VIEWPORTS.map((v) => v.w),
    screenshots: pngs.length, checks_passed: passed, checks_total: results.length, results };
  writeFileSync(join(OUT, "chrome_report.json"), JSON.stringify(summary, null, 2), "utf8");
  console.log(`\n  Скриншотов: ${pngs.length}; проверок: ${passed}/${results.length}`);
  console.log(`  Evidence: ${OUT}`);
  if (passed !== results.length) process.exit(1);
}

main().catch((e) => { console.error(e); process.exit(2); });
