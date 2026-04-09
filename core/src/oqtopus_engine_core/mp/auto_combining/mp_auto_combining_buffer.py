from __future__ import annotations

import asyncio
import json
import logging
import time

import grpc  # type: ignore[import-untyped]
import qiskit.qasm3  # type: ignore[import-untyped]
from qiskit.circuit import (  # type: ignore[import-untyped]
    ClassicalRegister,
    QuantumCircuit,
    QuantumRegister,
)
from qiskit.transpiler.layout import (  # type: ignore[import-untyped]
    Layout,
    TranspileLayout,
)
from uuid_extensions import uuid7

from oqtopus_engine_core.framework import Buffer, GlobalContext, JobContext
from oqtopus_engine_core.framework.model import Job, JobInfo, JobResult
from oqtopus_engine_core.interfaces.combiner_interface.v1 import (
    combiner_pb2,
    combiner_pb2_grpc,
)

logger = logging.getLogger(__name__)

# Only combine sampling jobs for now.
COMBINABLE_JOB_TYPES = ["sampling", "multi_manual"]


class MpAutoCombiningBuffer(Buffer):
    """Multi-programming auto combining buffer.

        This buffer automatically combines jobs in the input queue based on
        the combiner gRPC service and puts the combined jobs to the output queue.
        The combination process is done in the background task.
        The uncombined jobs are passed through without modification.

    Args:
        maxsize: Maximum number of elements allowed in the queue.
            A value of 0 indicates unlimited capacity.
        max_concurrency: Maximum number of worker tasks allowed to consume
            from this buffer concurrently. Defaults to 1.
        combiner_address: The address of the combiner gRPC service.
        monitor_interval_seconds: The interval in seconds for monitoring the input queue
            and performing combination.
        max_batch_size: The maximum number of jobs to drain from the input queue
            for each combination attempt.
        max_qsize_to_proceed: The maximum output queue size to proceed with combination.
            If the output queue size exceeds this value, the buffer will wait for more
            jobs to accumulate in the input queue before proceeding with combination.

    """

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        maxsize: int = 0,
        max_concurrency: int = 1,
        combiner_address: str = "localhost:52013",
        monitor_interval_seconds: float = 1,
        max_batch_size: int = 60,
        max_qsize_to_proceed: int = 5
    ) -> None:
        self._input_queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._output_queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._max_concurrency = max_concurrency
        self._interval_seconds = monitor_interval_seconds
        self._max_batch_size = max_batch_size
        self._max_qsize_to_proceed = max_qsize_to_proceed
        self._stopped = asyncio.Event()

        self._channel = grpc.aio.insecure_channel(combiner_address)
        self._stub = combiner_pb2_grpc.CombinerServiceStub(self._channel)

        task = asyncio.create_task(self.start())
        # Keep a reference to prevent the task from being garbage-collected,
        # and remove the reference when the task is completed
        self._background_tasks: set[asyncio.Task] = set()
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def put(self, gctx: GlobalContext, jctx: JobContext, job: Job) -> None:
        """Insert a tuple into the buffer.

        Args:
            gctx: Global execution context.
            jctx: Job-specific context.
            job: The raw job data.

        Note:
            This operation waits if the queue is full (only when `maxsize > 0`).

        """
        await self._input_queue.put((gctx, jctx, job))

    async def get(self) -> tuple[GlobalContext, JobContext, Job]:
        """Remove and return a tuple from the buffer.

        Returns:
            A tuple `(gctx, jctx, job)` retrieved from the queue.

        Note:
            This operation waits until an item becomes available.

        """
        return await self._output_queue.get()

    def size(self) -> int:
        """Return the current number of queued items.

        Returns:
            The size of the underlying queue.

        """
        return self._output_queue.qsize()

    @property
    def max_concurrency(self) -> int:
        """Return the maximum worker concurrency for this buffer."""
        return self._max_concurrency

    async def start(self) -> None:
        """Start the background processing loop."""
        logger.info(
            "starting multi-programming auto",
            extra={
                "interval_seconds": self._interval_seconds,
                "max_batch_size": self._max_batch_size
            },
        )
        await self._run()

    async def stop(self) -> None:
        """Signal the task to stop and wait for completion."""
        logger.info("stopping multi-programming auto")
        self._stopped.set()
        if self._task:
            await self._task

    async def _run(self) -> None:
        """Run main loop: drain input buffer, perform combination, enqueue results."""
        while not self._stopped.is_set():
            await asyncio.sleep(self._interval_seconds)

            drained = await self._drain_input()
            if not drained:
                continue

            try:
                # filter combinable jobs
                combinable_jobs, uncombinable_jobs = filter_combinable_jobs(drained)

                # combine the combinable jobs if the number of jobs is more than 1
                if len(combinable_jobs) > 1:
                    combined_jobs, unassigned_jobs = \
                        await self._combine_jobs(combinable_jobs)
                else:
                    logger.info(
                        "not enough combinable jobs; skipping combine",
                        extra={"num_combinable_jobs": len(combinable_jobs)}
                    )
                    combined_jobs = []
                    unassigned_jobs = combinable_jobs

            except Exception:
                logger.exception("error during job combination")
                for item in drained:
                    logger.debug(
                        "passing through a job unchanged",
                        extra={
                            "job_id": item[2].job_id,
                            "job_type": item[2].job_type,
                        },
                    )
                    await self._output_queue.put(item)
            else:
                logger.info(
                    "auto combining complete",
                    extra={
                        "num_drained": len(drained),
                        "num_jobs_before_combining":
                            len([job
                                for combined_job in combined_jobs
                                for job in combined_job[2].children
                            ]),
                        "num_jobs_after_combining": len(combined_jobs),
                        "num_jobs_unassigned": len(unassigned_jobs),
                        "num_jobs_uncombinable": len(uncombinable_jobs),
                        "combined_job_ids": [
                            job.job_id
                            for combined_job in combined_jobs
                            for job in combined_job[2].children
                        ],
                        "unassigned_job_ids": [
                            job[2].job_id for job in unassigned_jobs
                        ],
                        "uncombinable_job_ids": [
                            job[2].job_id for job in uncombinable_jobs
                        ],
                        "output_queue_size": self._output_queue.qsize(),
                    },
                )

                # enqueue combined jobs
                for item in combined_jobs:
                    await self._output_queue.put(item)
                # enqueue uncombined jobs
                for item in unassigned_jobs:
                    await self._output_queue.put(item)
                # enqueue uncombinable jobs
                for item in uncombinable_jobs:
                    await self._output_queue.put(item)

                logger.debug(
                    "multi-auto process complete",
                    extra={
                        "drained_jobs": len(drained),
                        "total_jobs_after_processing":
                            len(combined_jobs) + len(unassigned_jobs)
                    },
                )

    async def _drain_input(self) -> list[tuple[GlobalContext, JobContext, Job]]:
        """Drain jobs from input buffer up to max_batch_size.

        Drain jobs from the input buffer without blocking too long.

        Returns:
            A list of drained `(GlobalContext, JobContext, Job)` tuples.

        """
        items: list[tuple[GlobalContext, JobContext, Job]] = []
        # Always wait for at least one job to appear
        try:
            first = await asyncio.wait_for(self._input_queue.get(),
                                           timeout=self._interval_seconds
                                           )
        except TimeoutError:
            return items
        items.append(first)

        # Non-blocking draining up to max batch size
        while True:
            if self._max_batch_size and len(items) >= self._max_batch_size:
                break
            try:
                items.append(self._input_queue.get_nowait())
            except asyncio.QueueEmpty:  # type: ignore[attr-defined]
                if self._output_queue.qsize() <= self._max_qsize_to_proceed:
                    break

                logger.debug(
                    "output_queue is busy; waiting for more jobs to accumulate",
                    extra={
                        "output_queue_size": self._output_queue.qsize(),
                        "num_drained_jobs": len(items),
                    },
                )
                await asyncio.sleep(self._interval_seconds)

        self._input_queue.task_done()
        logger.info(
            "drained jobs from input queue",
            extra={
                "num_drained_jobs": len(items),
            },
        )
        return items

    async def _combine_jobs(
        self,
        jobs: list[tuple[GlobalContext, JobContext, Job]]
    ) -> tuple[
            list[tuple[GlobalContext, JobContext, Job]],
            list[tuple[GlobalContext, JobContext, Job]]
        ]:
        """Combine the given jobs using the combiner gRPC service.

        Args:
            jobs: List of `(GlobalContext, JobContext, Job)` tuples to be combined.

        Returns:
            A tuple containing:
            - List of combined jobs as `(GlobalContext, JobContext, Job)` tuples.
            - List of unassigned jobs that could not be combined.

        """
        # send gRPC request
        try:
            response = await self._request_combine(jobs)
        except Exception:
            # return empty combined jobs and all original jobs as uncombined
            logger.exception(
                "gRPC error during combine request",
                extra={
                    "jobs": jobs,
                },
            )
            return [], jobs

        # process response
        combine_result = json.loads(response.combine_result)
        combined_groups = combine_result["combined_groups"]
        # create combined jobs to be sent to after-buffer steps
        combined_jobs = []
        for combined_group in combined_groups:
            cmb_info = combined_group["combine_info"]

            # get original jobs that were combined
            original_jobs = {
                original_job.job_id: (gctx, jctx, original_job)
                for gctx, jctx, original_job in jobs
                if original_job.job_id in cmb_info["assigned_ids"]
            }

            # update transpile_result of original jobs according to the qubits assigned
            for assigned_job in cmb_info["assigned_group"]:
                job_id = assigned_job["job_id"]
                transpile_result = original_jobs[job_id][2].transpile_result
                transpile_result = \
                    await self._update_transpile_result(
                        transpile_result,
                        assigned_job["qubit_mapping"],
                    )
                original_jobs[job_id][2].transpile_result = transpile_result

            # use the max shots among original jobs for the combined job
            shots = max(job[2].shots for job in original_jobs.values())
            # create new job object for the combined circuit
            combined_job = create_combined_job(
                combined_group["combined_program"],
                shots=shots
            )
            # add context in JobContext to recover original jobs in post-process
            mp_auto_combining_ctx = {
                "n_total_qubits": cmb_info["n_total_qubits"],
                "combined_qubits_list": cmb_info["combined_qubits_list"],
            }

            # create new contexts for the combined job
            combined_jctx = JobContext()
            combined_jctx.mp_auto_combining = mp_auto_combining_ctx

            # Only link children here, not parent, to avoid overwriting the parent
            # of the original jobs before they are combined.
            combined_job.children = [job for _, _, job in original_jobs.values()]
            combined_jctx.children = [jctx for _, jctx, _ in original_jobs.values()]
            combined_jctx.has_actual_children = True

            # take gctx from one of the original jobs. gctx is common among jobs.
            gctx = next(iter(original_jobs.values()))[0]

            # store the combined job
            combined_jobs.append((gctx, combined_jctx, combined_job))

        assigned_ids = combine_result["assigned_ids"]
        uncombined_jobs = [job for job in jobs if job[2].job_id not in assigned_ids]
        for item in uncombined_jobs:
            logger.debug(
                "the job could not be combined; passing through unchanged",
                extra={
                    "job_id": item[2].job_id,
                    "job_type": item[2].job_type,
                },
            )

        return combined_jobs, uncombined_jobs

    @staticmethod
    async def _update_transpile_result(
        transpile_result: JobResult.TranspileResult,
        transpiled_combined_mapping: dict[int, int]
    ) -> JobResult.TranspileResult:
        """Update transpile_result according to the qubits assigned when combining.

        This method updates the `virtual_physical_mapping` and `transpiled_program`
        in the transpile_result. The qubits are re-mapped according to the qubits
        assigned during the combination process.
        For instance, if the original circuit's `virtual_physical_mapping` is
        `{0:1, 1:0}` and it is re-mapped as `{0:5, 1:4}` when combining, `q0` is first
        assigned to `q1` when per-circuit transpiling and then `q1` is assigned to `q4`
        when combining. So the end-to-end `virtual_physical_mapping` is `{0:4, 1:5}` and
        transpiled_program is updated by replacing `q0` with `q1` and `q1` with `q4`.

        Args:
            transpile_result: Original transpile result to be updated.
            transpiled_combined_mapping:
                Qubit mapping from original transpiled to combined.

        Returns:
            Updated transpile result with remapped qubits.

        """
        # convert the type of keys of qubit mapping from str to int
        transpiled_combined_mapping = {int(k): v
                                       for k, v in transpiled_combined_mapping.items()
                                       }
        qubit_mapping = transpile_result.virtual_physical_mapping["qubit_mapping"]
        new_virtual_physical_mapping = {k: transpiled_combined_mapping[v]
                                        for k, v in qubit_mapping.items()
                                        }
        transpile_result.virtual_physical_mapping["qubit_mapping"] = \
                                                new_virtual_physical_mapping

        # Update transpiled_program according to the qubits assigned when combining
        transpiled_program = transpile_result.transpiled_program
        transpiled_circuit = qiskit.qasm3.loads(transpiled_program)
        # construct new circuit with remapped qubits
        qr = QuantumRegister(max(transpiled_combined_mapping.values()) + 1, name="q")
        cr = ClassicalRegister(max(transpiled_combined_mapping.keys()) + 1, name="c")
        new_circuit = QuantumCircuit(qr, cr)
        for instr, qargs, cargs in transpiled_circuit.data:
            new_qargs = \
                [transpiled_combined_mapping[transpiled_circuit.find_bit(q).index]
                 for q in qargs
                 ]
            new_cargs = [transpiled_circuit.find_bit(c).index for c in cargs]
            new_circuit.append(instr, new_qargs, new_cargs)

        # if no layout info (it means no physical qubit assign was set), return as is
        if transpiled_circuit.layout is None:
            return transpile_result

        # if layout info exists, add physical qubit assign info to new circuit
        layout = dict(enumerate(new_circuit.qregs[0]))
        mapping = {qreg: i for i, qreg in enumerate(new_circuit.qregs[0])}
        new_circuit._layout = TranspileLayout(initial_layout=Layout(layout),  # noqa: SLF001
                                              input_qubit_mapping=mapping
                                              )

        transpile_result.transpiled_program = qiskit.qasm3.dumps(new_circuit)

        return transpile_result

    async def _request_combine(
        self,
        jobs: list[tuple[GlobalContext, JobContext, Job]]
    ) -> combiner_pb2.OptimalCombineResponse:
        """Send combine request to the combiner gRPC service.

        Args:
            jobs: List of `(GlobalContext, JobContext, Job)` tuples to be combined.

        Returns:
            The gRPC response from the combiner service.

        """
        programs_dict = []
        # request
        for _, _, job in jobs:
            program = extract_target_program(job)
            programs_dict.append({"job_id": job.job_id, "program": program})

        programs = json.dumps(programs_dict)
        request = combiner_pb2.OptimalCombineRequest(
                programs=programs,
                device_info=jobs[0][0].device.device_info
            )
        logger.info(
            "OptimalCombineRequest request",
            extra={"request": request},
        )

        start = time.perf_counter()
        response = await self._stub.OptimalCombine(request)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "OptimalCombineRequest response",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                "response": response,
            },
        )
        return response


def filter_combinable_jobs(
    jobs: list[tuple[GlobalContext, JobContext, Job]]
) -> tuple[
    list[tuple[GlobalContext, JobContext, Job]],
    list[tuple[GlobalContext, JobContext, Job]]
]:
    """Filter jobs that are eligible for combination.

    Args:
        jobs: List of `(GlobalContext, JobContext, Job)` tuples to filter.

    Returns:
        A tuple containing:
        - List of combinable jobs as `(GlobalContext, JobContext, Job)` tuples.
        - List of uncombinable jobs as `(GlobalContext, JobContext, Job)` tuples.

    """
    combinable_jobs = []
    uncombinable_jobs = []
    for gctx, jctx, job in jobs:
        # TODO: Add evaluation if the job accepts combination or not  # noqa: TD002,TD003,FIX002,E501
        if (
            job.job_type in COMBINABLE_JOB_TYPES and
            # Do not combine jobs that specify no transpiling
            # since the jobs may assign qubits manually.
            # MP auto does not support manual qubit assignment currently.
            job.transpiler_info.get("transpiler_lib", None) is not None
            ):
            combinable_jobs.append((gctx, jctx, job))

        else:
            uncombinable_jobs.append((gctx, jctx, job))

    return combinable_jobs, uncombinable_jobs


def create_combined_job(combined_program: str, shots: int) -> Job:
    """Create a combined Job object from the combined QASM.

    Args:
        combined_program: The combined program QASM string.
        shots: Number of shots for the combined job.

    Returns:
        A Job object containing the combined program.

    """
    return Job(
        job_id=f"mpa-comb-{uuid7(as_type='str')}",
        device_id="",
        shots=shots,
        job_type="sampling",
        input="",
        program=[combined_program],
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="ready",
    )


def extract_target_program(job: Job) -> str:
    """Extract the target program QASM from the Job object.

    Args:
        job: Job object from which to extract the program.

    Returns:
        The program QASM string.

    """
    # For now, we assume jobs with no transpiler are filtered out in advance.
    # Thus, we always extract transpiled_program
    return job.transpile_result.transpiled_program
