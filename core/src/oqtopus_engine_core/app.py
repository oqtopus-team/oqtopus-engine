import asyncio
import logging

from oqtopus_engine_core.framework import (
    Buffer,
    DeviceFetcher,
    GlobalContext,
    JobFetcher,
    JobRepository,
    PipelineExceptionHandler,
    PipelineExecutor,
)
from oqtopus_engine_core.utils import (
    load_config,
    mask_sensitive_info,
    parse_args,
    setup_logging,
)
from oqtopus_engine_core.utils.di_container import DiContainer


async def main() -> None:
    """Run the OQTOPUS Engine application."""
    args = parse_args()

    # Load the configuration file
    config = load_config(args.config)
    # Setup logging
    logging_config = load_config(args.logging)
    setup_logging(logging_config)
    logger = logging.getLogger("oqtopus_engine_core")

    # Show the configuration
    logger.info("starting OQTOPUS Engine")
    gctx = GlobalContext(config=config)
    logger.info("gctx.config=%s", mask_sensitive_info(gctx.config))

    # Initialize the DI container
    dicon = DiContainer(gctx.config["di_container"]["registry"])

    # Initialize the pipeline exception handler
    exception_handler: PipelineExceptionHandler = dicon.get(
        "pipeline_exception_handler"
    )

    # Initialize the pipeline executor
    job_buffer: Buffer = dicon.get("buffer")
    pipeline = PipelineExecutor(
        pipeline=[
            # dicon.get("debug_step"),  # noqa: ERA001
            dicon.get("job_repository_update_step"),
            dicon.get("multi_manual_step"),
            dicon.get("tranqu_step"),
            dicon.get("estimator_step"),
            dicon.get("ro_error_mitigation_step"),
            job_buffer,
            dicon.get("sse_step"),
            dicon.get("device_gateway_step"),
        ],
        job_buffer=job_buffer,
        exception_handler=exception_handler,
    )

    # Initialize the job fetcher
    job_fetcher: JobFetcher = dicon.get("job_fetcher")
    job_fetcher.gctx = gctx
    job_fetcher.pipeline = pipeline

    # Initialize the job repository
    job_repository: JobRepository = dicon.get("job_repository")
    gctx.job_repository = job_repository

    # Initialize the device fetcher
    device_fetcher: DeviceFetcher = dicon.get("device_fetcher")
    device_fetcher.gctx = gctx

    # Initialize the device repository
    gctx.device_repository = dicon.get("device_repository")

    # Start the executor, the job fetcher and the device fetcher
    pipeline_task = asyncio.create_task(pipeline.start())
    job_fetcher_task = asyncio.create_task(job_fetcher.start())
    device_fetcher_task = asyncio.create_task(device_fetcher.start())
    await asyncio.gather(pipeline_task, job_fetcher_task, device_fetcher_task)


if __name__ == "__main__":
    asyncio.run(main())
