import asyncio

from oqtopus_engine_core.framework.engine import Engine
from oqtopus_engine_core.utils import (
    load_config,
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

    # Initialize the Engine
    engine = Engine(config=config)
    await engine.start()


if __name__ == "__main__":
    asyncio.run(main())
