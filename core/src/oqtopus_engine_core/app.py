import asyncio
import logging

from hydra.utils import instantiate

from oqtopus_engine_core.framework import (
    DeviceFetcher,
    GlobalContext,
    JobFetcher,
    PipelineExceptionHandler,
    PipelineExecutor,
)
from oqtopus_engine_core.utils import (
    load_config,
    mask_sensitive_info,
    parse_args,
    setup_logging,
)


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

    # Initialize the pipeline exception handler
    exception_handler: PipelineExceptionHandler = instantiate(
        gctx.config["pipeline_exception_handler"]
    )

    # Initialize the pipeline executor
    pipeline = PipelineExecutor(
        before_buffer_steps=[
            # instantiate(gctx.config["debug_step"]),  # noqa: ERA001
            instantiate(gctx.config["job_repository_update_step"]),
            instantiate(gctx.config["multi_manual_step"]),
            instantiate(gctx.config["tranqu_step"]),
            instantiate(gctx.config["estimator_step"]),
            instantiate(gctx.config["ro_error_mitigation_step"]),
        ],
        job_buffer=asyncio.Queue(),  # TODO Queueを直接見せずに抽象化すべき
        after_buffer_steps=[  # TODO after buffer step
            instantiate(gctx.config["sse_step"]), #TODO: pipeline locked during sse step
            instantiate(gctx.config["device_gateway_step"]),
        ],
        exception_handler=exception_handler,
    )

    # Initialize the job fetcher
    job_fetcher: JobFetcher = instantiate(gctx.config["job_fetcher"])
    job_fetcher.gctx = gctx
    job_fetcher.pipeline = pipeline

    # Initialize the job repository
    gctx.job_repository = instantiate(gctx.config["job_repository"])

    # Initialize the device fetcher
    device_fetcher: DeviceFetcher = instantiate(gctx.config["device_fetcher"])
    device_fetcher.gctx = gctx

    # Initialize the device repository
    gctx.device_repository = instantiate(gctx.config["device_repository"])

    # Start the executor, the job fetcher and the device fetcher
    pipeline_task = asyncio.create_task(pipeline.start())
    job_fetcher_task = asyncio.create_task(job_fetcher.start())
    device_fetcher_task = asyncio.create_task(device_fetcher.start())
    await asyncio.gather(pipeline_task, job_fetcher_task, device_fetcher_task)


if __name__ == "__main__":
    asyncio.run(main())
