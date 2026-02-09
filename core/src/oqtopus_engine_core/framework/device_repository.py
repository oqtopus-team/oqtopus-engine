from abc import ABC, abstractmethod

from .model import Device


class DeviceRepository(ABC):
    """Abstract base class for device repository implementations."""

    @abstractmethod
    async def update_device(self, device: Device) -> None:
        """Update device in Oqtopus Cloud.

        Args:
            device: The device to update

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = (
            "`update_device` must be implemented in subclasses of DeviceRepository."
        )
        raise NotImplementedError(message)

    @abstractmethod
    async def update_device_status(self, device: Device) -> None:
        """Update device status in Oqtopus Cloud.

        Args:
            device: The device to update

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`update_device_status` must be implemented in subclasses of DeviceRepository."
        raise NotImplementedError(message)

    @abstractmethod
    async def update_device_info(self, device: Device) -> None:
        """Update device info in Oqtopus Cloud.

        Args:
            device: The device to update

        Raises:
            NotImplementedError: If not implemented in subclass.

        """
        message = "`update_device_info` must be implemented in subclasses of DeviceRepository."
        raise NotImplementedError(message)
