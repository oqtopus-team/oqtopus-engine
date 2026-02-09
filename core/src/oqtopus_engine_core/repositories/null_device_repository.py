import logging

from oqtopus_engine_core.framework import Device, DeviceRepository

logger = logging.getLogger(__name__)


class NullDeviceRepository(DeviceRepository):
    """Null-object implementation of DeviceRepository.

    This repository intentionally performs no operations and does not
    interact with any external system. It is safe to use in environments
    where device persistence is disabled.
    """

    def __init__(self) -> None:
        """Initialize the device repository."""
        super().__init__()

        logger.info(
            "NullDeviceRepository was initialized",
        )

    async def update_device(self, device: Device) -> None:
        """No-op implementation."""

    async def update_device_status(self, device: Device) -> None:
        """No-op implementation."""

    async def update_device_info(self, device: Device) -> None:
        """No-op implementation."""
