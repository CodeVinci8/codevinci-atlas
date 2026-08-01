# NEXT — активный VP и следующий шаг

## Завершено

**VP-0 — Profile Pool & Live Handoff Proof: ЗАВЕРШЁН, 11/11 PASS**
([`vp/VP-0.md`](vp/VP-0.md)). Реальные A→B доказаны для Codex и Claude;
изоляция через per-profile идентичности и исполняемые файлы; recovery до
успеха; секрет-скан чист; capacity UNKNOWN. Принимается через PR в `main`.

## Активный VP

**VP-1 — Foundation** ([Master Spec §34](MASTER_SPEC.md)). Compose Core/Web +
systemd Runner + health/migrations/Audit + CLI `doctor/backup/status` + RU/EN
shell + CI. Одна feature-ветка, WIP=1, один writer. Профильный пул и полный
Pulse — НЕ в VP-1 (позже: Agent Pipeline / Full Web Console).

## NEXT_ACTION

Реализовать VP-1 по §34 в ветке `atlas/vp-1-foundation`; на границе — полная
приёмка §34, негативные/recovery-проверки и секрет-скан; затем PR/merge.
VP-2 не начинать.
