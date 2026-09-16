# SLURM MPI-Qulacs Deployment Guide

This guide explains how to deploy the direct SLURM MPI-Qulacs path from an
independent `oqtopus-engine` checkout in two reference environments:

- the Engine-owned Docker fixture under `test-infra/scheduler/slurm/`
- the qsim Batch environment, where login nodes are x86_64 and compute nodes
  are AArch64

The execution contract is the same in both environments. The scheduler
partition, shared work directory, batch adapter, worker script, Python runtime,
MPI launcher, and site-specific CPU settings are selected by deployment
configuration. Core does not assume a particular CPU architecture, MPI
implementation, or launcher. All commands in this guide are relative to the
root of the `oqtopus-engine` repository unless stated otherwise. For the
lifecycle and recovery contract, see [SLURM MPI-Qulacs Simulator](overview.md).

## 1. Execution Model

The runtime has four configuration layers:

| Layer | Owns | Examples |
| --- | --- | --- |
| Engine configuration | Cloud device identity, scheduler resources, work paths, polling, and safety limits | `SLURM_PARTITION`, `SLURM_WORK_ROOT`, `SLURM_QUBITS_PER_NODE` |
| Execution adapter | The command used inside an already reserved allocation | `SLURM_BATCH_SCRIPT`, `srun`, `mpirun`, `OQTOPUS_WORKER_PYTHON` |
| Runtime and MPI installation | Python, `mpi4py`, MPI-enabled Qulacs, shared libraries, and architecture-specific binaries | Docker image or qsim AArch64 venv |
| Scheduler environment | Allocation-provided topology and rank information | `SLURM_NTASKS`, `SLURM_NTASKS_PER_NODE`, `SLURM_LOCALID` |

The execution path is:

```text
Core on a SLURM login node
  -> sbatch reserves nodes and MPI tasks
  -> configured batch adapter runs inside the allocation
  -> srun or mpirun starts the requested ranks
  -> every rank runs the configured worker
  -> rank 0 atomically writes result.json
```

The adapter receives the worker script path as its only argument. It runs from
the per-job work directory and must start the worker with exactly these
positional arguments:

```text
request.json result.json
```

The adapter must use the allocation already created by Core. It must not call
`sbatch` again. It must start `n_per_node` ranks per node, matching the topology
reserved by Core.

## 2. Common Prerequisites

Every deployment needs the following:

1. Core can execute `sinfo`, `sbatch`, `squeue`, `sacct`, and `scancel` from the
   login node.
2. Core can reach the OQTOPUS Cloud Job and Device APIs.
3. `SLURM_WORK_ROOT`, the batch adapter, and the worker script are visible at
   the same absolute paths from the login node, the controller, and every
   compute node.
4. The compute-node runtime contains `mpi4py` and an MPI-enabled Qulacs build.
5. `mpi4py`, Qulacs, and the launcher use a compatible MPI implementation.
6. The per-node memory is sufficient for the distributed state-vector layout.
7. Only one Core process owns a configured simulator device.

A Python interpreter being present is not enough. The selected interpreter must
be able to import both `mpi4py` and the Qulacs package used by the worker.

## 3. Engine Configuration

The source of truth is
[`core/config/slurm_simulator_config.yaml`](../../../core/config/slurm_simulator_config.yaml).
The values are supplied through environment substitution when Core starts.

### 3.1 Required deployment values

These values must be set for a production deployment unless the deployment
launcher supplies equivalent defaults outside the Engine configuration.

| Variable | Default | Meaning | Change it when |
| --- | --- | --- | --- |
| `SLURM_PARTITION` | none | SLURM partition used for health checks and submissions | The site uses a partition other than the deployment default |
| `SLURM_DEVICE_N_QUBITS` | none | Maximum logical qubit count advertised by the configured device | The configured simulator capacity changes |
| `SLURM_DEVICE_INFO` | none | Complete device information JSON | The site has a device topology or calibration description |
| `SLURM_WORK_ROOT` | none | Durable per-job work root | The default path is not shared or durable |
| `SLURM_BATCH_SCRIPT` | none | Absolute path to the deployment-owned execution adapter | The launcher, runtime, or site wrapper changes |
| `SLURM_WORKER_SCRIPT` | none | Absolute path to `run_qulacs_mpi.py` | The worker is installed at another shared path |

`SLURM_DEVICE_ID` defaults to `large-simulator`. Set it explicitly when the
Cloud device row has another ID. The Cloud device row must already exist; Core
publishes the configured device information but does not create the row.

Example common configuration:

```bash
export SLURM_PARTITION=Batch
export SLURM_DEVICE_ID=mpi-qulacs
export SLURM_DEVICE_N_QUBITS=32
export SLURM_DEVICE_INFO="$(cat /path/to/device-info.json)"
export SLURM_WORK_ROOT=/shared/oqtopus/slurm-work
export SLURM_BATCH_SCRIPT=/shared/oqtopus/slurm/run_qulacs_mpi_job.sh
export SLURM_WORKER_SCRIPT=/shared/oqtopus/slurm/run_qulacs_mpi.py
```

### 3.2 Resource and safety defaults

These settings have portable Engine defaults. A deployment should change them
only after checking the scheduler and node capacity.

| Variable | Default | Purpose |
| --- | ---: | --- |
| `SLURM_QUBITS_PER_NODE` | `30` | State-vector qubit capacity used for automatic node sizing |
| `SLURM_MAX_NODES` | `1024` | Maximum requested node count |
| `SLURM_MAX_TASKS_PER_NODE` | `48` | Maximum MPI ranks per node |
| `SLURM_MAX_TIMEOUT_SECONDS` | `432000` | Maximum allocation timeout |
| `SLURM_MAX_SHOTS` | `100000` | Maximum sampling shots |
| `SLURM_POLL_INTERVAL_SECONDS` | `5` | Scheduler polling interval |
| `SLURM_ACCOUNT` | `null` | Optional scheduler account passed to `sbatch` |
| `SLURM_QOS` | `null` | Optional scheduler QoS passed to `sbatch` |
| `SLURM_PROCESS_LOCK_PATH` | `~/.local/state/oqtopus-engine/slurm/engine.lock` | Core process lock |
| `SLURM_ARTIFACT_TTL_SECONDS` | `604800` | Retention period for terminal work artifacts |
| `SLURM_FINALIZE_RETRY_COUNT` | `2` | Cloud result-finalization retries |
| `SLURM_FINALIZE_RETRY_INTERVAL_SECONDS` | `1` | Delay between finalization retries |

When `n_nodes` is omitted from `simulator_info`, Core derives the minimum node
count as:

```text
2 ** max(n_qubits - SLURM_QUBITS_PER_NODE, 0)
```

This is a scheduling estimate, not a memory guarantee. Set
`SLURM_QUBITS_PER_NODE` from measured per-node memory usage for the selected
MPI-Qulacs build. A non-MPI Qulacs build can still require a full state vector
per rank and will not be fixed by increasing the MPI rank count.

### 3.3 Configuration that is deliberately outside the Engine config

The Engine config does not contain a Python path, MPI launcher, CPU
architecture, NUMA policy, or Qulacs thread setting. Those values belong to the
adapter and runtime because they differ between sites.

The reference adapters use this optional environment variable:

```bash
export OQTOPUS_WORKER_PYTHON=/shared/oqtopus/venv/bin/python
```

The adapter falls back to `python3` when it is unset. The value may be a Python
executable or an executable wrapper that accepts Python's command-line
arguments. The wrapper must preserve the worker's two positional arguments.

Typical site-local variables are also adapter-owned:

```text
PATH
LD_LIBRARY_PATH
LD_PRELOAD
OMP_PROC_BIND
OMP_NUM_THREADS
QULACS_NUM_THREADS
UCX_IB_MLX5_DEVX
```

Do not add these to the Engine YAML merely to support one cluster. Put them in
the deployment adapter or its runtime wrapper.

## 4. Adapter Requirements and Launcher Choice

The repository's reference adapter
[`deployment/slurm/run_qulacs_mpi_job.sh`](../../../deployment/slurm/run_qulacs_mpi_job.sh)
is a portable `srun` reference. It reads `n_per_node` from `request.json` and
executes:

```bash
srun \
  --ntasks-per-node="$n_per_node" \
  "$OQTOPUS_WORKER_PYTHON" \
  "$worker_script" \
  request.json \
  result.json
```

A deployment may replace it with an `mpirun` adapter. The Engine-owned Docker
fixture uses the following equivalent shape:

```bash
mpirun \
  -np "$SLURM_NTASKS" \
  --npernode "$n_per_node" \
  "$OQTOPUS_WORKER_PYTHON" \
  "$worker_script" \
  request.json \
  result.json
```

Use `srun` when the site's SLURM and MPI integration supports launching the
selected MPI implementation through SLURM. Use `mpirun` when the MPI
implementation owns process startup or when the SLURM PMI/PMIx combination
cannot initialize the selected MPI library. The launcher choice does not
change the worker protocol, but the runtime must be rebuilt or selected for the
same MPI implementation used by the launcher.

The adapter is deployment-owned when it contains site paths, NUMA policy,
module loading, `mpirun` flags, or architecture-specific environment setup.
Keep it outside the Engine source tree and set `SLURM_BATCH_SCRIPT` to its
absolute path.

## 5. Engine-Owned Docker Environment

### 5.1 Intended topology

The Engine-owned fixture is under `test-infra/scheduler/slurm/`. It contains:

- one SLURM controller and accounting database
- two Docker compute containers registered as `c1` and `c2`
- a `cpu` partition
- shared Docker volumes for configuration, jobs, logs, and work data
- an amd64-oriented Linux container runtime
- OpenMPI 4.1.1, `mpi4py` 4.1.2, and MPI-enabled Qulacs 0.6.14

The two containers simulate two scheduler nodes on one physical Docker host.
They do not validate a physical network interconnect or production placement
policy.

The image builds Python 3.12 and installs the runtime at:

```text
/opt/oqtopus-venv/bin/python
```

The image sets:

```text
OQTOPUS_WORKER_PYTHON=/opt/oqtopus-venv/bin/python
LD_LIBRARY_PATH=/usr/lib64/openmpi/lib
```

Qulacs is built with `mpicc`, `mpic++`, and `USE_MPI=Yes`. Installing
`mpi4py` alone does not make Qulacs MPI-enabled.

The Docker adapter uses `mpirun`. The fixture's SLURM image exposes PMI-2 while
the Rocky Linux OpenMPI package expects PMIx, so the production `srun` adapter
is not used in this image.

### 5.2 Start and validate the fixture

Run these commands from the `oqtopus-engine` repository root:

```bash
make -C test-infra/scheduler/slurm build
make -C test-infra/scheduler/slurm up
make -C test-infra/scheduler/slurm smoke
make -C test-infra/scheduler/slurm down
```

For a host Core process connected to the Docker cluster:

```bash
make -C test-infra/scheduler/slurm up-host
source test-infra/scheduler/slurm/host-engine.env
export SLURM_DEVICE_INFO="$(cat /tmp/qulacs-device-info.json)"
make -C core run-slurm-simulator
```

`host-engine.env` is Bash syntax. It supplies the local defaults:

| Variable | Engine fixture default |
| --- | --- |
| `SLURM_PARTITION` | `cpu` |
| `SLURM_DEVICE_ID` | `qulacs` |
| `SLURM_DEVICE_N_QUBITS` | `16` |
| `SLURM_WORK_ROOT` | `<engine-repository>/.cache/oqtopus-slurm-work` |
| `SLURM_BATCH_SCRIPT` | `<engine-repository>/test-infra/scheduler/slurm/run_qulacs_mpi_job.sh` |
| `SLURM_WORKER_SCRIPT` | `<engine-repository>/deployment/slurm/run_qulacs_mpi.py` |
| `SLURM_QUBITS_PER_NODE` | `30` |
| `SLURM_POLL_INTERVAL_SECONDS` | `5` |

### 5.3 Customizing the Docker defaults

The fixture's environment file and its local Makefile are the customization
interface:

```bash
export SLURM_PARTITION=cpu
export SLURM_DEVICE_ID=qulacs
export SLURM_DEVICE_N_QUBITS=16
export SLURM_QUBITS_PER_NODE=30
source test-infra/scheduler/slurm/host-engine.env
make -C test-infra/scheduler/slurm up-host
```

Change these variables when:

- the partition or scheduler account differs
- the Cloud device ID or advertised qubit count differs
- the work root is moved
- the adapter or worker is mounted at another path
- the node memory changes enough to alter `SLURM_QUBITS_PER_NODE`

Changing the launcher or CPU architecture is not only a Makefile change. Build
an image containing a compatible Python, MPI, `mpi4py`, and MPI-enabled Qulacs
runtime, then point `SLURM_BATCH_SCRIPT` and `SLURM_WORKER_SCRIPT` at paths
visible in every container. If the new MPI setup supports SLURM PMI/PMIx,
replace the fixture adapter with an `srun` adapter and validate the complete
multi-node smoke test. The image can be selected with the fixture's
`SLURM_RUNTIME_IMAGE` Make variable.

See [`test-infra/scheduler/slurm/README.md`](../../../test-infra/scheduler/slurm/README.md)
for the fixture topology, host bridge, image build, and scope limitations.

## 6. qsim Batch Environment

### 6.1 Site assumptions

The qsim configuration used for the successful Batch validation has these
properties:

- login node architecture: `x86_64`
- compute node architecture: `aarch64`
- login and compute nodes share the relevant home filesystem
- Batch nodes provide 48 CPUs and approximately 32000 MB of memory per node
- `Batch` is the production partition
- the qsim MPI runtime is installed in a user-owned directory visible to the
  compute nodes
- the validated Engine path uses `srun` inside the allocation

The system `/usr/bin/python3` on a compute node is an ARM interpreter, but it
is not the job runtime: it does not provide the required `mpi4py` and Qulacs
packages. Do not use an x86_64 login-node virtualenv on the AArch64 compute
nodes. An x86_64 executable will fail with `Exec format error`.

### 6.2 Build the compute-node runtime

The qsim gateway documentation builds Python on a compute node with pyenv and
then creates a job venv. The documented pattern is:

```bash
salloc -N 1 -p Interactive --time=1:00:00
pyenv install 3.11.9
pyenv local 3.11.9
python -m venv /home/<user>/gateway/jobs/venv_exec
source /home/<user>/gateway/jobs/venv_exec/bin/activate
pip install -U pip wheel
pip install mpi4py mpiQulacs
deactivate
exit
```

The gateway repository automates the same setup with:

```bash
salloc -N 1 -p Interactive --time=1:00:00
/home/<user>/gateway/repo/a64fx/setup_job_env.sh /home/<user>/gateway
exit
```

The validated qsim runtime contained:

- AArch64 Python 3.11.9
- `mpi4py` 3.1.4
- `mpiQulacs` 1.3.1rc0
- an ARM-native Qulacs extension

The exact package versions can change with the site image and compiler. The
requirements that must remain true are that the packages are built for the
compute-node architecture and that `mpi4py` and Qulacs use the MPI library
provided by the qsim runtime.

Verify the runtime from a compute allocation before starting Core:

```bash
srun --immediate=60 \
  --partition=Batch \
  --nodes=1 \
  --ntasks=1 \
  --cpus-per-task=1 \
  --time=00:02:00 \
  /home/<user>/gateway/jobs/venv_exec/bin/python \
  -c 'import platform, sys, mpi4py, qulacs; print(sys.executable); print(platform.machine()); print(mpi4py.__file__); print(qulacs.__file__)'
```

The output must show the AArch64 compute node and successful imports. A
successful import check does not replace a multi-rank MPI smoke test.

### 6.3 Configure Core and the qsim adapter

A qsim deployment using the repository `srun` adapter needs the following
values. Paths must be shared absolute paths.

```bash
export SLURM_PARTITION=Batch
export SLURM_DEVICE_ID=mpi-qulacs
export SLURM_DEVICE_N_QUBITS=32
export SLURM_DEVICE_INFO="$(cat /shared/oqtopus/qsim-device-info.json)"
export SLURM_WORK_ROOT=/shared/oqtopus/slurm-work
export SLURM_BATCH_SCRIPT=/shared/oqtopus/slurm/run_qulacs_mpi_job.sh
export SLURM_WORKER_SCRIPT=/shared/oqtopus/oqtopus-engine/deployment/slurm/run_qulacs_mpi.py
export SLURM_QUBITS_PER_NODE=30
export OQTOPUS_WORKER_PYTHON=/home/<user>/gateway/jobs/venv_exec/bin/python
```

Start Core with the SLURM simulator configuration:

```bash
cd /shared/oqtopus/oqtopus-engine/core
make run-slurm-simulator
```

If the site wrapper applies NUMA binding or library setup, set
`OQTOPUS_WORKER_PYTHON` to an executable wrapper that accepts the same Python
arguments, or replace `SLURM_BATCH_SCRIPT` with a qsim-specific adapter. The
wrapper must eventually execute the AArch64 Python runtime for every MPI rank.

Existing qsim startup scripts that still export
`OQTOPUS_QULACS_PYTHON` must be updated to `OQTOPUS_WORKER_PYTHON`. There is no
backward-compatible alias in the current adapter.

### 6.4 qsim CPU and MPI tuning

The qsim gateway's A64FX job wrapper documents the following site settings:

| Setting | qsim gateway value | Ownership |
| --- | --- | --- |
| `UCX_IB_MLX5_DEVX` | `no` | qsim MPI/network adapter |
| `OMP_PROC_BIND` | `TRUE` | runtime wrapper |
| `OMP_NUM_THREADS` | `1` | runtime wrapper |
| `QULACS_NUM_THREADS` | `48` | Qulacs runtime wrapper |
| `LD_PRELOAD` | `/lib64/libgomp.so.1` | site library workaround |
| NUMA binding | `numactl`, based on local rank | runtime wrapper |

These are not portable Engine defaults. Apply them only when the qsim MPI and
Qulacs build require them, and keep them in the qsim adapter or wrapper. The
successful Engine Batch validation confirms the ARM MPI-Qulacs runtime and the
`srun` allocation path; each additional tuning variable should be validated
independently on the target qsim partition.

The qsim gateway also documents an `mpirun` invocation around its own
`a64fx/job.sh` wrapper. qsim can therefore use `mpirun` when the selected MPI
installation and site policy support it. To use that path from Engine, provide
an Engine-compatible batch adapter that starts the worker directly with
`mpirun`; the gateway wrapper's argument contract is not automatically the
same as the Engine adapter contract. The qsim `srun` adapter is the path used
for the verified 32-qubit Batch execution.

### 6.5 qsim resource sizing

The verified qsim Batch environment reports roughly 32 GB per node and uses
`SLURM_QUBITS_PER_NODE=30`. With 32 logical qubits and no explicit
`n_nodes`, the Engine derives four nodes, which produced one MPI rank per node
in the successful validation.

These values are qsim-specific measurements, not universal defaults. Recheck
all of the following when the partition or Qulacs build changes:

- node memory available to the job
- number of CPUs and OpenMP threads per node
- MPI ranks per node
- NUMA domain count and rank binding
- Qulacs state-vector memory per rank
- scheduler limits and account/QoS policy

## 7. Changing the Default Deployment

Use this order when adapting the deployment to a new cluster:

1. Confirm the compute-node architecture and choose a native Python runtime.
2. Install or build `mpi4py` and MPI-enabled Qulacs against the site's MPI.
3. Run a one-node import check and a multi-rank MPI probe on the target
   partition.
4. Choose `srun`, `mpirun`, or a site launcher and implement the Engine adapter
   contract.
5. Set `SLURM_BATCH_SCRIPT`, `SLURM_WORKER_SCRIPT`, and `SLURM_WORK_ROOT` to
   shared absolute paths.
6. Set the partition, account, QoS, device metadata, and Cloud endpoints.
7. Measure memory and set `SLURM_QUBITS_PER_NODE` conservatively.
8. Add NUMA, OpenMP, UCX, and library variables only in the adapter/runtime
   layer.
9. Run sampling and direct estimation, then restart and cancellation tests.
10. Run the real-cluster smoke matrix before production rollout.

### What changes for common customizations?

| Customization | Engine config change | Adapter/runtime change |
| --- | --- | --- |
| Different partition | `SLURM_PARTITION` | Usually none |
| Scheduler account or QoS | `SLURM_ACCOUNT`, `SLURM_QOS` | Usually none |
| Different device capacity | `SLURM_DEVICE_ID`, `SLURM_DEVICE_N_QUBITS`, `SLURM_DEVICE_INFO` | Recheck memory sizing |
| Different shared filesystem | `SLURM_WORK_ROOT`, script paths | Ensure identical absolute visibility and permissions |
| Different Python version | None | Build/select runtime and set `OQTOPUS_WORKER_PYTHON` |
| Different CPU architecture | None | Native Python, `mpi4py`, Qulacs, and shared libraries |
| Different MPI implementation | None | Rebuild `mpi4py` and Qulacs; select matching launcher |
| `srun` to `mpirun` | `SLURM_BATCH_SCRIPT` | Replace adapter and validate topology |
| NUMA or OpenMP policy | None | Runtime wrapper or adapter variables |
| No shared filesystem | Not supported by the current direct contract | Add a staging mechanism before using this path |
| More or less memory per node | `SLURM_QUBITS_PER_NODE` | Re-measure worker memory and rank placement |

## 8. Validation and Troubleshooting

### Recommended validation sequence

1. Import check on one compute node using `OQTOPUS_WORKER_PYTHON`.
2. MPI rank and hostname probe across the intended number of nodes.
3. Two-node sampling and direct estimation.
4. Controller or Core restart recovery.
5. Cancellation while the allocation is pending and running.
6. A representative large-qubit circuit, including the target 32-qubit case.

For the Engine-owned Docker fixture, the complete smoke command is:

```bash
make -C test-infra/scheduler/slurm smoke
```

### Common failures

| Symptom | Likely cause | Check |
| --- | --- | --- |
| `ModuleNotFoundError` for `mpi4py` or `qulacs` | System Python or the wrong venv is selected | Run the import check through `OQTOPUS_WORKER_PYTHON` on a compute node |
| `Exec format error` | x86_64 runtime is being executed on an AArch64 compute node, or vice versa | Check `file` and `platform.machine()` inside the allocation |
| 32q job OOMs on every node | Qulacs is not MPI-enabled, or rank/node memory sizing is too aggressive | Verify the Qulacs build and reduce `SLURM_QUBITS_PER_NODE` |
| `srun` MPI initialization fails | SLURM PMI/PMIx and MPI implementation do not match | Try the site's supported `mpirun` adapter and verify MPI linkage |
| `mpirun` starts the wrong number of ranks | `-np` or `--npernode` does not match the reserved allocation | Compare `SLURM_NTASKS`, `SLURM_NTASKS_PER_NODE`, and `request.json` |
| Worker cannot find `request.json` | Adapter changed its working directory | Start the worker from the per-job work directory |
| Result is missing after a completed allocation | Work root is not shared or the adapter did not preserve paths | Check identical absolute mounts and `result.json` visibility |
| qsim falls back to system Python | `OQTOPUS_WORKER_PYTHON` is unset or an old variable name is still exported | Update the startup script and verify the resolved executable |
