#!/usr/bin/env bash
# atlas-runtime-setup — создать минимальные runtime-идентичности и каталоги
# для VP-0 (Master Spec §7.2/§7.3, §30). Идемпотентно. Запускать от root.
#
# Модель изоляции: сервисный пользователь `atlas` (Core/Runner, не root) плюс
# ОТДЕЛЬНАЯ Unix-идентичность на каждый профиль. Root профиля принадлежит его
# идентичности с правами 0700 — ни другой профиль, ни даже сервисный `atlas`
# не читают чужие credentials. Runner дропает привилегии в идентичность
# профиля перед запуском CLI. Credentials НЕ копируются ниоткуда.
set -euo pipefail

DATA_DIR="${ATLAS_DATA_DIR:-/var/lib/codevinci-atlas}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Нужен root для создания идентичностей/каталогов." >&2; exit 1
fi

mk_sysuser() {  # $1 user, $2 home
  local u="$1" home="$2"
  if ! getent group "$u" >/dev/null; then groupadd --system "$u"; fi
  if ! getent passwd "$u" >/dev/null; then
    useradd --system --no-create-home --home-dir "$home" \
            --shell /usr/sbin/nologin --gid "$u" "$u"
  fi
}

# Сервисная группа/пользователь
if ! getent group atlas >/dev/null; then groupadd --system atlas; fi
if ! getent passwd atlas >/dev/null; then
  useradd --system --no-create-home --home-dir "$DATA_DIR" \
          --shell /usr/sbin/nologin --gid atlas atlas
fi

# Per-profile идентичности (в т.ч. в группе atlas для traverse)
declare -A PROFILE_USER=(
  [codex-plus-01]=atlas-cx01 [codex-plus-02]=atlas-cx02
  [claude-pro-01]=atlas-cl01 [claude-pro-02]=atlas-cl02
)
for alias in "${!PROFILE_USER[@]}"; do
  u="${PROFILE_USER[$alias]}"
  provider="codex"; [[ "$alias" == claude* ]] && provider="claude"
  home="${DATA_DIR}/profiles/${provider}/${alias}"
  mk_sysuser "$u" "$home"
  usermod -a -G atlas "$u" || true
done

# Каталоги: база owned atlas:atlas, traversable (0751), но не listable другими
install -d -o atlas -g atlas -m 0751 "$DATA_DIR"
install -d -o atlas -g atlas -m 0751 "$DATA_DIR/profiles"
install -d -o atlas -g atlas -m 0751 "$DATA_DIR/profiles/codex"
install -d -o atlas -g atlas -m 0751 "$DATA_DIR/profiles/claude"
install -d -o atlas -g atlas -m 0750 "$DATA_DIR/runner"
install -d -o atlas -g atlas -m 0750 "$DATA_DIR/artifacts"
install -d -o atlas -g atlas -m 0750 "$DATA_DIR/logs"

echo "Готово. Идентичности:"
getent passwd atlas atlas-cx01 atlas-cx02 atlas-cl01 atlas-cl02 | cut -d: -f1,3,6
echo "Каталог: $DATA_DIR"
echo "Следующий шаг: PYTHONPATH=apps/core python3 scripts/profile-init.py"
