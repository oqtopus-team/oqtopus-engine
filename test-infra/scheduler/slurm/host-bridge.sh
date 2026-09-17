#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
engine_root="$(cd -- "${script_dir}/../../.." && pwd)"
compose=(
  docker compose
  -f "${script_dir}/compose.yaml"
  -f "${script_dir}/compose.host.yaml"
)

usage() {
  printf 'usage: %s {up|down|config|exec COMMAND [ARGS...]}\n' "${BASH_SOURCE[0]}" >&2
  exit 2
}

if [[ $# -lt 1 ]]; then
  usage
fi

case "$1" in
  up)
    mkdir -p "${engine_root}/.cache/oqtopus-slurm-work"
    OQTOPUS_ENGINE_ROOT="${engine_root}" "${compose[@]}" up -d --wait
    ;;
  down)
    OQTOPUS_ENGINE_ROOT="${engine_root}" "${compose[@]}" down
    ;;
  config)
    OQTOPUS_ENGINE_ROOT="${engine_root}" "${compose[@]}" config
    ;;
  exec)
    if [[ $# -lt 2 ]]; then
      usage
    fi
    slurm_command="$2"
    case "${slurm_command}" in
      sbatch|squeue|sacct|sinfo|scontrol|scancel)
        ;;
      *)
        printf 'unsupported SLURM command: %s\n' "${slurm_command}" >&2
        exit 2
        ;;
    esac
    OQTOPUS_ENGINE_ROOT="${engine_root}" "${compose[@]}" exec -T slurmctld "${slurm_command}" "${@:3}"
    ;;
  *)
    usage
    ;;
esac
