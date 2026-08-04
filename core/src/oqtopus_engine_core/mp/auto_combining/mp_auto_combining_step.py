"""Background task for automatic multi-programming.

This task consumes incoming jobs from `source_buffer`, performs combination logic
(reduction of job count by merging circuits), and enqueues the processed jobs
into `processed_buffer` for the pipeline's after-buffer steps.
"""

import logging

import numpy as np

from oqtopus_engine_core.framework import (
    GlobalContext,
    JobContext,
    PipelineDirective,
    StepResult,
)
from oqtopus_engine_core.framework.model import Job, JobResult, SamplingResult
from oqtopus_engine_core.framework.step import Step
from oqtopus_engine_core.steps.multi_manual_step import (
    divide_result,
)

logger = logging.getLogger(__name__)


class MpAutoCombiningStep(Step):
    """Multi-programming auto combining worker.

    This step divides the results of auto-combined job back to original jobs.
    The automatic combining process is done in `MpAutoCombiningBuffer` class.
    """

    async def pre_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,  # noqa: ARG002
        job: Job,  # noqa: ARG002
    ) -> StepResult:
        """Pre-process the job.

        Do nothing.
        The automatic combining process is done in `MpAutoCombiningBuffer` class.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Returns:
            StepResult: NONE directive — the pipeline continues normally.

        """
        return StepResult()

    async def post_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> StepResult:
        """Post-process the job by dividing results back to original jobs.

        This method divides the counts of the combined job back to the original
        jobs and returns SPLIT_WITHOUT_JOIN so each child continues through the
        remaining post-process steps independently.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            RuntimeError: If failed to divide the results back to original jobs.

        Returns:
            StepResult: SPLIT_WITHOUT_JOIN directive when auto-combined; NONE otherwise.

        """
        if "mp_auto_combining" not in jctx:
            logger.info(
                "jctx does not have mp_auto_combining info, skipping post_process",
                extra={
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                },
            )
            return StepResult()

        # get used qubits info
        combined_qubits_list = jctx.mp_auto_combining["combined_qubits_list"]
        n_total_qubits = len(next(iter(job.result.sampling.counts.keys())))  # type: ignore[union-attr]
        if n_total_qubits > sum(combined_qubits_list):
            # add the number of unused qubits to combined_qubits_list
            # for the convenience of division
            combined_qubits_list = [
                *combined_qubits_list,
                n_total_qubits - sum(combined_qubits_list),
            ]

        try:
            # divide the result
            divided_counts_list = divide_result(
                job,
                combined_qubits_list,
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
                child_job.result = JobResult(
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

        return StepResult(
            directive=PipelineDirective.SPLIT_WITHOUT_JOIN,
            child_jobs=list(job.children),
            child_contexts=list(jctx.children),
        )


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
    if total_counts == 0:
        logger.warning(
            "total counts is zero, cannot resample counts",
            extra={
                "counts": counts,
                "shots": shots,
            },
        )
        return counts
    probs = [v / total_counts for v in values]

    # resampling
    rng = np.random.default_rng()
    sampled_keys = rng.choice(keys, size=shots, p=probs)
    unique, counts_result = np.unique(sampled_keys, return_counts=True)
    return {str(k): int(v) for k, v in zip(unique, counts_result, strict=False)}
