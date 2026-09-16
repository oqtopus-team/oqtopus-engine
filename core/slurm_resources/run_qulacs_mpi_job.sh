#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 WORKER_SCRIPT" >&2
  exit 2
fi

worker_script="$1"
python_executable="${OQTOPUS_WORKER_PYTHON:-python3}"
n_per_node="$($python_executable -c 'import json; print(json.load(open("request.json", encoding="utf-8"))["n_per_node"])')"

exec srun \
  --ntasks-per-node="$n_per_node" \
  "$python_executable" \
  "$worker_script" \
  request.json \
  result.json
