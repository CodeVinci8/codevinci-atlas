# Core: FastAPI + SQLAlchemy, запуск НЕ от root (Master Spec §7.3, §34, §35).
FROM python:3.12-slim AS base

# git нужен для read-only baseline и безопасного worktree add (VP-2, §35).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# uv для воспроизводимой установки из uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

WORKDIR /app

# Установка зависимостей по локу (кэшируемый слой).
COPY pyproject.toml uv.lock ./
COPY apps/core/atlas_core/__init__.py apps/core/atlas_core/__init__.py
COPY apps/runner/atlas_runner/__init__.py apps/runner/atlas_runner/__init__.py
RUN uv sync --frozen --no-dev --no-install-project

# Исходники Core/Runner + миграции.
COPY apps/core /app/apps/core
COPY apps/runner /app/apps/runner
COPY alembic.ini /app/alembic.ini

# VP-4 runtime (§37 Phase H): изолированный fresh-session consumer и контракты
# схем. Копируем ТОЛЬКО нужные runtime-файлы, а не весь репозиторий — consumer
# чистый (stdlib), без доступа к БД/credentials; contracts/ содержит лишь JSON-схемы.
COPY scripts/vp4_fresh_consumer.py /app/scripts/vp4_fresh_consumer.py
COPY contracts /app/contracts

ENV PYTHONPATH=/app/apps/core:/app/apps/runner \
    PATH=/app/.venv/bin:$PATH \
    ATLAS_DATA_DIR=/var/lib/codevinci-atlas \
    ATLAS_RUNNER_SOCKET=/run/codevinci-atlas/runner.sock \
    ATLAS_RUNNER_TOKEN_FILE=/run/codevinci-atlas/runner.token

# Непривилегированный пользователь. UID/GID можно переопределить при сборке,
# чтобы совпасть с host atlas для доступа к смонтированным путям.
ARG ATLAS_UID=10001
ARG ATLAS_GID=10001
RUN groupadd --gid ${ATLAS_GID} atlas || true \
 && useradd --uid ${ATLAS_UID} --gid ${ATLAS_GID} --no-create-home --shell /usr/sbin/nologin atlas || true
USER ${ATLAS_UID}:${ATLAS_GID}

EXPOSE 8000
# Миграции применяются entrypoint'ом до старта (accepted path — только Alembic).
COPY --chmod=0755 infra/docker/core-entrypoint.sh /usr/local/bin/core-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/core-entrypoint.sh"]
CMD ["uvicorn", "atlas_core.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
