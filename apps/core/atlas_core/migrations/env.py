"""Alembic env — Core Atlas. URL и metadata берём из приложения."""

from __future__ import annotations

from alembic import context
from atlas_core.migration_guard import assert_live_migration_allowed
from atlas_core.orm import Base
from atlas_core.settings import load_settings
from sqlalchemy import engine_from_config, pool

config = context.config

# Fail-closed до любой миграции: живой production data_dir/DB мигрируется ТОЛЬКО при
# ATLAS_ALLOW_LIVE_MIGRATION=1 (guarded deploy §8). Изолированные/CI/dev-цели — свободно.
assert_live_migration_allowed(purpose="alembic env")

_settings = load_settings()
config.set_main_option("sqlalchemy.url", _settings.db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=_settings.db_url, target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
