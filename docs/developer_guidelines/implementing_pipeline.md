# Implementing Pipeline

## Step

This section describes the specifications of a Step and important considerations for implementation.
An overview of a Step is as follows:

- Inherit from `oqtopus_engine_core.framework.Step`
- Implement pre-process logic in the `pre_process` function
- Implement post-process logic in the `post_process` function

The arguments of `pre_process` and `post_process` are as follows.
For details on the meaning of each argument, see the [concept section](../design/concept.md).

- `gctx` (GlobalContext)
- `jctx` (JobContext)
- `job` (Job)

A single Step instance processes multiple jobs.
Therefore, pay attention to the following points:

- Steps must be **thread-safe**
- Do **not** update the internal state (instance variables) of the Step inside `pre_process` or `post_process`
- SIf the Step requires instance variables, they must hold information **not specific to individual jobs** (e.g., connection endpoints). Job-specific information must be stored in `jctx` or `job`

Below is an example Step implementation.
This simple Step only logs the Job and JobContext.

```python
import logging

from oqtopus_engine_core.framework import GlobalContext, Job, JobContext, Step

logger = logging.getLogger(__name__)


class DebugStep(Step):
    """Debug step that logs job and context info."""

    async def pre_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Pre-process the job by logging job and context information.

        This method logs the job and the job context.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
        logger.debug(
            "debug dump",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job": job,
                "jctx": jctx,
            },
        )

    async def post_process(  # noqa: PLR6301
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Post-process the job by logging job and context information.

        This method logs the job and the job context.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """
        logger.debug(
            "debug dump",
            extra={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job": job,
                "jctx": jctx,
            },
        )
```

Implementation Notes

- `pre_process` and `post_process` **must be `async` functions** so that external operations can be awaited and CPU usage can be optimized
- When calling external services (e.g., via gRPC), use `await`. Log request and response contents at the **INFO** level. If logging the full content risks excessive log size, you may omit it. When logging a response, include the **elapsed time**.
- If `pre_process` or `post_process` raises an exception, the Engine framework catches it and updates the job repository, marking the job as `failed`
- OQTOPUS Engine uses **structured logging**. To improve log analysis using external log-search tools, keep the `message` field a fixed string and put variable information into `extra`
- For better debugging, always include `job_id` and `job_type` in logs whenever possible
- The start and end of `pre_process` and `post_process` are logged at INFO level by the framework, so Steps do **not** need to log these themselves
