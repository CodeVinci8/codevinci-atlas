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

from atlas_runner.server import RunnerConfig, RunnerServer  # noqa: E402


async def main() -> None:
    cfg = RunnerConfig(
        socket_path=os.environ["ATLAS_RUNNER_SOCK"],
        token=os.environ["ATLAS_RUNNER_TOKEN"],
        journal_path=os.environ["ATLAS_RUNNER_JOURNAL"],
        allowed_dirs=os.environ.get("ATLAS_RUNNER_ALLOWED", "").split(":"),
        allow_root=True,
        grace_s=1.0,
    )
    srv = RunnerServer(cfg)
    await srv.start()
    await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
