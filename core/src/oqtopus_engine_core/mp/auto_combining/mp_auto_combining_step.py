"""Background task for automatic multi-programming.

This task consumes incoming jobs from `source_buffer`, performs combination logic
(reduction of job count by merging circuits), and enqueues the processed jobs
into `processed_buffer` for the pipeline's after-buffer steps.
"""
import copy
import logging

import numpy as np
from uuid_extensions import uuid7

from oqtopus_engine_core.framework import GlobalContext, JobContext
from oqtopus_engine_core.framework.model import Job, JobResult, SamplingResult
from oqtopus_engine_core.framework.step import SplitOnPostprocess, Step
from oqtopus_engine_core.steps.multi_manual_step import (
    divide_result,
)

logger = logging.getLogger(__name__)


class MpAutoCombiningStep(Step, SplitOnPostprocess):
    """Multi-programming auto combining worker.

    This step divides the results of auto-combined job back to original jobs.
    The automatic combining process is done in MpAutoCombiningBuffer class.
    """

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job.

        Do nothing.
        The automatic combining process is done in MpAutoCombiningBuffer class.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """

    async def post_process(  # noqa: PLR6301
            self,
            gctx: GlobalContext,  # noqa: ARG002
            jctx: JobContext,
            job: Job,
    ) -> None:
        """Post-process the job by dividing results back to original jobs.

            This method divides the counts of the combined job back to the original jobs

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            RuntimeError: If failed to divide the results back to original jobs.

        """
        if not hasattr(jctx, "mp_auto_combining"):
            logger.info(
                "jctx does not have mp_auto_combining info, skipping post_process",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
            # If the job is not combined one, just pass it through. But to pass it to
            # the next step, we need to treat the job same as the case of splitting.
            # Therefore set a copy of the job to job.children.
            job.children = [copy.copy(job)]
            jctx.children = [copy.copy(jctx)]
            # replace the parent's job_id to avoid confusion of parent and child jobs
            job.job_id = f"mpa-uncomb-{uuid7(as_type='str')}"
            return

        n_total_qubits = jctx.mp_auto_combining["n_total_qubits"]
        virtual_physical_mapping: dict[str, dict[int, int]] = {"qubit_mapping": {}}

        # padding for unused qubits
        idx = 0
        for i in range(n_total_qubits):
            if i in virtual_physical_mapping["qubit_mapping"]:
                continue
            while idx in virtual_physical_mapping["qubit_mapping"].values():
                idx += 1
                if idx >= n_total_qubits:
                    idx = 0
            virtual_physical_mapping["qubit_mapping"][i] = idx

        # get used qubits info
        combined_qubits_list = jctx.mp_auto_combining["combined_qubits_list"]
        n_total_qubits = len(next(iter(job.job_info.result.sampling.counts.keys())))
        if n_total_qubits > sum(combined_qubits_list):
            # add the number of unused qubits to the end of combined_qubits_list
            # for the convenience of the measurement and division
            combined_qubits_list.insert(0, n_total_qubits - sum(combined_qubits_list))

        try:
            # divide the result
            divided_counts_list = divide_result(
                job,
                combined_qubits_list
            )

            # set counts to all child jobs
            for i, child_job in enumerate(job.children):
                divided_counts = divided_counts_list.get(i, {})
                # resample counts to match the original shots
                resampled_counts = resample_counts(
                    counts=divided_counts,
                    shots=child_job.shots,
                )
                # set the result to the child job
                child_job.job_info.result = JobResult(
                    sampling=SamplingResult(
                        counts=resampled_counts,
                    )
                )
                child_job.execution_time = job.execution_time

        except Exception as e:
            logger.exception(
                "failed to divide result",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
            msg = "failed to extract result from auto-combined job result"
            raise RuntimeError(msg) from e


def resample_counts(
    counts: dict[str, int],
    shots: int,
) -> dict[str, int]:
    """Resample counts to match the original shots.

    Args:
        counts: Original counts from the combined job.
        shots: Number of shots for the original job.

    Returns:
        Resampled counts dictionary.

    """
    keys = list(counts.keys())
    values = list(counts.values())
    total_counts = sum(values)
    probs = [v / total_counts for v in values]

    # resampling
    rng = np.random.default_rng()
    sampled_keys = rng.choice(keys, size=shots, p=probs)
    unique, counts_result = np.unique(sampled_keys, return_counts=True)
    return {str(k): int(v) for k, v in zip(unique, counts_result, strict=False)}
