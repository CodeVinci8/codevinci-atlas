#!/usr/bin/env bash
# login-gate — печатает ТОЧНЫЕ команды официального логина (Master Spec §11.4,
# §47). Скрипт НИЧЕГО не логинит сам и не трогает credentials — только выводит
# команды. Логин выполняется ПОД идентичностью профиля (runuser), поэтому
# auth-файлы принадлежат этой идентичности (0700) и недоступны другим профилям
# и сервисному пользователю atlas.
#
# ВАЖНО: не вставляйте в чат токены, коды, cookie, email или account-ID.
# Для aliases 01 и 02 используйте РАЗНЫЕ браузерные профили/аккаунты.
set -euo pipefail

D="${ATLAS_DATA_DIR:-/var/lib/codevinci-atlas}"

row() { # alias, provider-var, identity
  local alias="$1" var="$2" ident="$3" provider="$4"
  local root="$D/profiles/$provider/$alias"
  echo "  # --- $alias  (идентичность $ident) ---"
  if [[ "$provider" == "codex" ]]; then
    echo "  runuser -u $ident -- env $var=\"$root\" HOME=\"$root\" codex login --device-auth"
    echo "  runuser -u $ident -- env $var=\"$root\" HOME=\"$root\" codex login status"
  else
    echo "  runuser -u $ident -- env $var=\"$root\" HOME=\"$root\" claude auth login --claudeai"
    echo "  runuser -u $ident -- env $var=\"$root\" HOME=\"$root\" claude auth status --json"
  fi
  echo
}

cat <<EOF
=================================================================
VP-0 OWNER GATE — официальный логин реальных профилей
=================================================================
Codex CLI установлен ($(codex --version 2>/dev/null || echo '?')).
Claude Code установлен ($(claude --version 2>/dev/null || echo '?')).
Выполняйте по ОДНОМУ профилю за раз. Аккаунты 01 и 02 — РАЗНЫЕ.
Устройство headless: codex использует device-auth (URL+код в браузере),
claude выведет ссылку для входа. Ничего не вставляйте в чат.

EOF
echo "--- 1) Codex A -------------------------------------------------"
row codex-plus-01 CODEX_HOME atlas-cx01 codex
echo "--- 2) Codex B (другой аккаунт) --------------------------------"
row codex-plus-02 CODEX_HOME atlas-cx02 codex
echo "--- 3) Claude A ------------------------------------------------"
row claude-pro-01 CLAUDE_CONFIG_DIR atlas-cl01 claude
echo "--- 4) Claude B (другой аккаунт) -------------------------------"
row claude-pro-02 CLAUDE_CONFIG_DIR atlas-cl02 claude
cat <<EOF
=================================================================
Когда все четыре покажут авторизацию, продолжите ТОТ ЖЕ VP-0:
  PYTHONPATH=apps/core:apps/runner python3 scripts/manual_real_probe.py
  PYTHONPATH=apps/core:apps/runner python3 scripts/run_acceptance.py
=================================================================
EOF
