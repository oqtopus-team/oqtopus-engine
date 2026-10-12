#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
compose=(docker compose -f "$script_dir/compose.yaml")
controller=("${compose[@]}" exec -T slurmctld)
work_root=/data/oqtopus-smoke
recovery_job_id=

cleanup() {
  if [[ -n "$recovery_job_id" ]]; then
    "${controller[@]}" scancel "$recovery_job_id" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

allocation_id() {
  local submission=$1
  printf '%s\n' "${submission%%;*}"
}

assert_accounting() {
  local job_id=$1
  local expected_state=$2
  local expected_comment=$3
  local record
  local recorded_job_id
  local state
  local exit_code
  local comment

  record="$(${controller[@]} sacct \
    --noheader \
    --allocations \
    --jobs="$job_id" \
    --format=JobIDRaw,State,ExitCode,Comment \
    --parsable2)"
  IFS='|' read -r recorded_job_id state exit_code comment <<<"$record"
  [[ "$recorded_job_id" == "$job_id" ]]
  [[ "$state" == "$expected_state"* ]]
  [[ "$comment" == "$expected_comment" ]]
  printf 'job=%s state=%s exit=%s comment=%s\n' \
    "$recorded_job_id" "$state" "$exit_code" "$comment"
}

assert_job_topology() {
  local job_id=$1
  local expected_nodes=$2
  local expected_tasks=$3
  local expected_tasks_per_node=$4
  local job_record

  job_record="$(${controller[@]} scontrol show job "$job_id")"
  [[ "$job_record" == *"NodeList=c[1-2]"* ]]
  [[ "$job_record" == *"NumNodes=$expected_nodes"* ]]
  [[ "$job_record" == *"NumTasks=$expected_tasks"* ]]
  [[ "$job_record" == *"NtasksPerN:B:S:C=$expected_tasks_per_node:"* ]]
  printf 'topology job=%s nodes=%s tasks=%s tasks_per_node=%s\n' \
    "$job_id" "$expected_nodes" "$expected_tasks" "$expected_tasks_per_node"
}

"${controller[@]}" rm -rf "$work_root"
"${controller[@]}" mkdir -p "$work_root/sampling" "$work_root/estimation" "$work_root/topology"

"${controller[@]}" sh -c "cat > '$work_root/topology/probe.py'" <<'PY'
import socket

from mpi4py import MPI


communicator = MPI.COMM_WORLD
print(
    f"rank={communicator.Get_rank()} size={communicator.Get_size()} host={socket.gethostname()}",
    flush=True,
)
PY

topology_job_id="$(allocation_id "$(${controller[@]} sbatch \
  --parsable \
  --wait \
  --partition=cpu \
  --nodes=2 \
  --ntasks=4 \
  --ntasks-per-node=2 \
  --cpus-per-task=1 \
  --chdir="$work_root/topology" \
  --output=stdout.log \
  --error=stderr.log \
  --comment=oqtopus-topology-smoke \
  --wrap='mpirun -np "$SLURM_NTASKS" --npernode 2 /opt/oqtopus-venv/bin/python probe.py')")"

"${controller[@]}" python - <<'PY'
import re
from collections import Counter
from pathlib import Path

rows = re.findall(
    r"rank=(\d+) size=(\d+) host=([^\s]+)",
    Path("/data/oqtopus-smoke/topology/stdout.log").read_text(),
)
assert len(rows) == 4, rows
assert {int(size) for _, size, _ in rows} == {4}, rows
assert {int(rank) for rank, _, _ in rows} == set(range(4)), rows
host_counts = Counter(host for _, _, host in rows)
assert set(host_counts) == {"c1", "c2"}, host_counts
assert sorted(host_counts.values()) == [2, 2], host_counts
print(f"mpi ranks={len(rows)} hosts={dict(sorted(host_counts.items()))}")
PY
"${controller[@]}" test ! -s "$work_root/topology/stderr.log"
assert_job_topology "$topology_job_id" 2 4 2
assert_accounting "$topology_job_id" COMPLETED oqtopus-topology-smoke

"${controller[@]}" sh -c "cat > '$work_root/sampling/request.json'" <<'JSON'
{"schema_version":1,"job_type":"sampling","n_qubits":4,"shots":256,"n_per_node":2,"seed_simulation":7,"gates":[{"name":"h","qubits":[0],"params":[]},{"name":"cx","qubits":[0,1],"params":[]}],"measurement_mapping":{"0":0,"1":1}}
JSON

sampling_submission="$("${controller[@]}" sbatch \
  --parsable \
  --wait \
  --partition=cpu \
  --nodes=2 \
  --ntasks=4 \
  --ntasks-per-node=2 \
  --cpus-per-task=1 \
  --chdir="$work_root/sampling" \
  --output=stdout.log \
  --error=stderr.log \
  --comment=oqtopus-sampling-smoke \
  /opt/oqtopus/test-infra/scheduler/slurm/run_qulacs_mpi_job.sh \
  /opt/oqtopus/deployment/slurm/run_qulacs_mpi.py)"
sampling_job_id="$(allocation_id "$sampling_submission")"

"${controller[@]}" python - <<'PY'
import json
from pathlib import Path

result = json.loads(Path("/data/oqtopus-smoke/sampling/result.json").read_text())
assert result["status"] == "succeeded"
assert result["job_type"] == "sampling"
assert sum(result["counts"].values()) == 256
assert set(result["counts"]) <= {"00", "11"}
print(f"sampling counts={result['counts']}")
PY
"${controller[@]}" test ! -s "$work_root/sampling/stderr.log"
assert_accounting "$sampling_job_id" COMPLETED oqtopus-sampling-smoke
assert_job_topology "$sampling_job_id" 2 4 2

"${controller[@]}" sh -c "cat > '$work_root/estimation/request.json'" <<'JSON'
{"schema_version":1,"job_type":"estimation","n_qubits":4,"n_per_node":2,"seed_simulation":7,"gates":[{"name":"h","qubits":[0],"params":[]}],"operators":[{"coeff":1.0,"pauli":"X 0"}]}
JSON

estimation_submission="$("${controller[@]}" sbatch \
  --parsable \
  --wait \
  --partition=cpu \
  --nodes=2 \
  --ntasks=4 \
  --ntasks-per-node=2 \
  --cpus-per-task=1 \
  --chdir="$work_root/estimation" \
  --output=stdout.log \
  --error=stderr.log \
  --comment=oqtopus-estimation-smoke \
  /opt/oqtopus/test-infra/scheduler/slurm/run_qulacs_mpi_job.sh \
  /opt/oqtopus/deployment/slurm/run_qulacs_mpi.py)"
estimation_job_id="$(allocation_id "$estimation_submission")"

"${controller[@]}" python - <<'PY'
import json
import math
from pathlib import Path

result = json.loads(Path("/data/oqtopus-smoke/estimation/result.json").read_text())
assert result["status"] == "succeeded"
assert result["job_type"] == "estimation"
assert math.isclose(result["exp_value"][0], 1.0, abs_tol=1e-12)
assert math.isclose(result["exp_value"][1], 0.0, abs_tol=1e-12)
print(f"estimation exp_value={result['exp_value']}")
PY
"${controller[@]}" test ! -s "$work_root/estimation/stderr.log"
assert_accounting "$estimation_job_id" COMPLETED oqtopus-estimation-smoke
assert_job_topology "$estimation_job_id" 2 4 2

recovery_submission="$("${controller[@]}" sbatch \
  --parsable \
  --partition=cpu \
  --nodes=1 \
  --ntasks=1 \
  --comment=oqtopus-recovery-smoke \
  --wrap='sleep 120')"
recovery_job_id="$(allocation_id "$recovery_submission")"
"${controller[@]}" squeue --noheader --jobs="$recovery_job_id" \
  --format='%i|%T|%k'

"${compose[@]}" restart slurmctld
"${compose[@]}" up -d --wait slurmctld

recovered="$(${controller[@]} squeue --noheader --jobs="$recovery_job_id" \
  --format='%i|%k')"
[[ "$recovered" == "$recovery_job_id|oqtopus-recovery-smoke" ]]
printf 'recovered=%s\n' "$recovered"

"${controller[@]}" scancel "$recovery_job_id"
assert_accounting "$recovery_job_id" CANCELLED oqtopus-recovery-smoke
recovery_job_id=

printf 'Docker SLURM smoke test passed.\n'
