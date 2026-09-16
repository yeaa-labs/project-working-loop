#!/usr/bin/env bash
# Local structural checks only. This never launches a model or network call.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$SKILL_DIR/config/delegation.toml"

usage() {
  echo "usage: selfcheck.sh [--profiles-dir /absolute/project/.codex/agents]" >&2
}

if [[ $# -eq 0 ]]; then
  TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hi-dele-to-low-selfcheck.XXXXXX")"
  trap 'rm -rf "$TEMP_DIR"' EXIT
  PROFILES_DIR="$TEMP_DIR/profiles"
  CHECK_EXISTING_PROFILES=false
elif [[ $# -eq 2 && "$1" == "--profiles-dir" ]]; then
  PROFILES_DIR="$2"
  CHECK_EXISTING_PROFILES=true
else
  usage
  exit 2
fi

# Compile source in memory so this check does not leave __pycache__ files in
# either an installed skill or an editable candidate.
python3 -B - "$SKILL_DIR/scripts/delegation_core.py" "$SKILL_DIR/scripts/dispatch_worker.py" \
  "$SKILL_DIR/scripts/review_worker.py" "$SKILL_DIR/scripts/generate_profiles.py" \
  "$SKILL_DIR/scripts/validate_receipt.py" <<'PY'
from pathlib import Path
import sys

for filename in sys.argv[1:]:
    source = Path(filename).read_text(encoding="utf-8")
    compile(source, filename, "exec")
PY

PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s "$SKILL_DIR/tests" -v

if [[ "$CHECK_EXISTING_PROFILES" == true ]]; then
  python3 -B "$SKILL_DIR/scripts/generate_profiles.py" --config "$CONFIG" \
    --output-dir "$PROFILES_DIR" --check
else
  python3 -B "$SKILL_DIR/scripts/generate_profiles.py" --config "$CONFIG" \
    --output-dir "$PROFILES_DIR"
  python3 -B "$SKILL_DIR/scripts/generate_profiles.py" --config "$CONFIG" \
    --output-dir "$PROFILES_DIR" --check
fi
