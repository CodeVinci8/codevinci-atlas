#!/usr/bin/env bash
# Регрессия упаковки Core-образа (Master Spec §16.4, §37 Phase H).
#
# Доказывает, что СОБРАННЫЙ образ Core содержит и МОЖЕТ ИСПОЛНИТЬ изолированный
# fresh-session consumer, а его результат валиден по контракту run-result.
# Ловит дефект, когда образ не содержал scripts/vp4_fresh_consumer.py и/или
# contracts/schemas/run-result.json (падение reconstruction внутри контейнера).
#
# Использование: scripts/check_core_image.sh [image_tag]
set -euo pipefail
IMG="${1:-codevinciatlas-core:latest}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1) Капсула с корректным content-hash (тем же каноническим способом).
PYTHONPATH="$ROOT/apps/core" python3 - "$TMP/capsule.json" <<'PY'
import hashlib, json, sys
def canon(o): return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
handoff = {
    "ids": {"project_id": "proj_img", "vp_key": "VP-X", "work_order_id": "wo_img"},
    "role": "builder", "goal": "reconstruct in-image", "finished_result": "partial",
    "immutable_constraints": ["one writer"],
    "acceptance_matrix": [{"id": "a1", "status": "completed"}, {"id": "a2", "status": "pending"}],
    "exact_next_action": "continue a2", "prohibited_actions": ["force push"],
    "baseline_head": "0" * 40, "current_head": "0" * 40,
    "changed_files": [], "commands": [], "failures": [],
}
cap = {"handoff": handoff,
       "content_hash": "sha256:" + hashlib.sha256(canon(handoff).encode()).hexdigest(),
       "run_id": "run_ci_img", "safe_refs": []}
json.dump(cap, open(sys.argv[1], "w"), ensure_ascii=False)
PY
chmod 0644 "$TMP/capsule.json"   # доступ для non-root пользователя образа

# 2) Исполнить consumer ВНУТРИ образа (реальный non-root пользователь, чистый cwd).
docker run --rm -v "$TMP/capsule.json:/tmp/capsule.json:ro" \
  --entrypoint python "$IMG" /app/scripts/vp4_fresh_consumer.py /tmp/capsule.json \
  > "$TMP/result.json"

# 3) Валидировать результат по контракту run-result.
PYTHONPATH="$ROOT/apps/core" python3 - "$TMP/result.json" "$ROOT/contracts/schemas/run-result.json" <<'PY'
import json, sys
from atlas_core.schema_validate import validate
res = json.load(open(sys.argv[1]))
schema = json.load(open(sys.argv[2]))
errs = validate(res, schema)
assert res.get("state") == "SUCCEEDED", res
assert not errs, errs
assert res["structured_output"]["reconstructed_from"] == "handoff_only", res
print("CORE IMAGE SELFTEST OK: consumer+run-result исполняются в образе", sys.argv[0])
PY
