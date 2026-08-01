# infra/

Инфраструктура развёртывания.

- `docker/` — Dockerfile'ы Core/Web и `compose.yaml`.
- `systemd/` — unit Runner с `User=atlas` (нативный процесс, не root).

**Область VP-1** (Master Spec §34). В VP-0 не реализуется, чтобы не расширять
scope. VP-0 доказывает риск на стандартной библиотеке без Docker/systemd.
