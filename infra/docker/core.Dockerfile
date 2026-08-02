# Core: FastAPI + SQLAlchemy, запуск НЕ от root (Master Spec §7.3, §34).
FROM python:3.12-slim AS base

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
