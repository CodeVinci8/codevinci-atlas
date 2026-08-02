#!/usr/bin/env python3
"""runner_host — отдельный процесс Runner для доказательства жёсткого краха.

Слушает UDS до SIGKILL. Конфигурация через окружение. Используется
``recovery_demo`` для честной имитации краха Runner: процесс убивается
SIGKILL, поэтому запись ``finished`` в journal не появляется, и следующий
инстанс Runner обязан обнаружить незавершённый job.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for pkg in ("apps/core", "apps/runner"):
    sys.path.insert(0, str(_ROOT / pkg))

from atlas_runner.protocol import generate_token, write_token_file  # noqa: E402
from atlas_runner.server import RunnerConfig, RunnerServer  # noqa: E402


async def main() -> None:
    # Токен: из окружения (тесты) или сгенерировать и записать в token-файл (прод),
    # чтобы Core (иной пользователь) мог аутентифицироваться, читая файл 0600.
    token = os.environ.get("ATLAS_RUNNER_TOKEN") or generate_token()
    bridge_group = os.environ.get("ATLAS_BRIDGE_GROUP") or None
    token_file = os.environ.get("ATLAS_RUNNER_TOKEN_FILE")
    if token_file:
        write_token_file(token_file, token, group=bridge_group)
    cfg = RunnerConfig(
        socket_path=os.environ["ATLAS_RUNNER_SOCK"],
        token=token,
        journal_path=os.environ["ATLAS_RUNNER_JOURNAL"],
        allowed_dirs=[d for d in os.environ.get("ATLAS_RUNNER_ALLOWED", "").split(":") if d],
        allow_root=os.environ.get("ATLAS_RUNNER_ALLOW_ROOT") == "1",
        bridge_group=bridge_group,
        grace_s=1.0,
    )
    srv = RunnerServer(cfg)
    await srv.start()
    await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
