// CI-гейт локалей (Master Spec §29.1): отсутствующий/лишний ключ → провал.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src");

function keysOf(file, exportName) {
  const text = readFileSync(join(src, file), "utf8");
  // грубый, но детерминированный разбор ключей "x.y": из объекта локали
  const keys = new Set();
  const re = /"([a-zA-Z0-9_.]+)"\s*:/g;
  let m;
  while ((m = re.exec(text))) keys.add(m[1]);
  return keys;
}

// эталон — LOCALE_KEYS в i18n.ts
const i18n = readFileSync(join(src, "i18n.ts"), "utf8");
const canonical = new Set();
{
  const block = i18n.slice(i18n.indexOf("LOCALE_KEYS"), i18n.indexOf("] as const"));
  const re = /"([a-zA-Z0-9_.]+)"/g;
  let m;
  while ((m = re.exec(block))) canonical.add(m[1]);
}

let failed = false;
for (const [file, name] of [["locales/ru.ts", "ru"], ["locales/en.ts", "en"]]) {
  const keys = keysOf(file, name);
  const missing = [...canonical].filter((k) => !keys.has(k));
  const extra = [...keys].filter((k) => !canonical.has(k));
  if (missing.length || extra.length) {
    failed = true;
    console.error(`[i18n] ${file}: missing=${JSON.stringify(missing)} extra=${JSON.stringify(extra)}`);
  } else {
    console.log(`[i18n] ${file}: OK (${keys.size} keys)`);
  }
}
if (canonical.size === 0) { console.error("[i18n] canonical key set empty"); failed = true; }
process.exit(failed ? 1 : 0);
