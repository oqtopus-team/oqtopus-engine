import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from oqtopus_engine_core.framework import Device, DeviceRepository
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    ApiClient,
    Configuration,
    DevicesApi,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    DevicesDeviceInfoUpdate,
    DevicesDeviceStatusUpdate,
    DevicesUpdateDeviceRequest,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException

logger = logging.getLogger(__name__)


def service_status_label_to_cloud(status: str) -> str:
    """Convert a service status label to Oqtopus Cloud device status.

    Args:
        status: The service status label.

    Returns:
        "available" if status == "active", otherwise "unavailable".

    """
    if status == "active":
        return "available"
    return "unavailable"


class OqtopusCloudDeviceRepository(DeviceRepository):
    """Device repository implementation for Oqtopus Cloud."""

    T = TypeVar("T")

    def __init__(
        self,
        url: str = "http://localhost:8888",
        api_key: str = "",
        proxy: str | None = None,
        workers: int = 5,
    ) -> None:
        """Initialize the device repository with the API URL and interval.

        Args:
            url: The endpoint URL to fetch jobs from.
            api_key: The API key for authentication.
            proxy: The proxy URL for the API request.
            workers: The number of concurrent workers to use for API requests.

        """
        super().__init__()
        # Construct DevicesApi
        rest_config = Configuration()
        rest_config.host = url
        if proxy:
            rest_config.proxy = proxy
        api_client = ApiClient(
            configuration=rest_config,
            header_name="x-api-key",
            header_value=api_key,
        )
        self._devices_api = DevicesApi(api_client=api_client)
        self._sem = asyncio.Semaphore(workers)

        logger.info(
            "OqtopusCloudDeviceRepository was initialized",
            extra={
                "url": url,
                "proxy": proxy,
                "workers": workers,
            },
        )

    async def _request_with_error_logging(
        self,
        call: Callable[[], T],
        label: str,
        extra: dict[str, Any],
    ) -> T | None:
        """Call an API in a worker thread with logging and error handling.

        Args:
            call: Callable that performs the HTTP request and returns (data, status, headers).
            label: Log label like 'PATCH /devices/{device_id}'.
            extra: Extra fields to log on error.

        Returns:
            The data returned by the call, or None if an error occurred.

        Raises:
            ApiException: If an API error occurs.

        """
        async with self._sem:
            try:
                return await asyncio.to_thread(call)
            except ApiException as ex:
                # Note:
                # - Logged at INFO level because the caller performs the actual
                #   error handling at a higher layer
                # - This log is only a diagnostic breadcrumb, not a final failure record
                logger.info(
                    "%s: response",
                    label,
                    extra={
                        "status_code": ex.status,
                        "reason": ex.reason,
                        "body": str(ex.body),
                        **extra,
                    },
                )
                raise
            except Exception:
                # Same reasoning as above: avoid duplicate ERROR-level logs.
                logger.info(
                    "%s: unexpected error",
                    label,
                    extra=extra,
                )
                raise

    async def update_device(self, device: Device) -> None:
        """Update device in Oqtopus Cloud.

        Args:
            device: The device to update

        """
        body = DevicesUpdateDeviceRequest(
            n_qubits=device.n_qubits,
        )

        def _call() -> tuple[object, int, dict]:
            return self._devices_api.patch_device_with_http_info(
                device_id=device.device_id,
                body=body,
            )

        extra: dict[str, Any] = {"device_id": device.device_id}

        logger.info(
            "PATCH /devices/{device_id}: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "PATCH /devices/{device_id}",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "PATCH /devices/{device_id}: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "body": response,
            },
        )

    async def update_device_status(self, device: Device) -> None:
        """Update device status in Oqtopus Cloud.

        Args:
            device: The device to update

        """
        body = DevicesDeviceStatusUpdate(
            status=service_status_label_to_cloud(device.status),
        )

        extra: dict[str, Any] = {"device_id": device.device_id}

        def _call() -> tuple[object, int, dict]:
            return self._devices_api.patch_device_status_with_http_info(
                device_id=device.device_id,
                body=body,
            )

        logger.info(
            "PATCH /devices/{device_id}/status: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "PATCH /devices/{device_id}/status",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "PATCH /devices/{device_id}/status: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "body": response,
            },
        )

    async def update_device_info(self, device: Device) -> None:
        """Update device info in Oqtopus Cloud.

        Args:
            device: The device to update

        """
        body = DevicesDeviceInfoUpdate(
            device_info=device.device_info,
            calibrated_at=device.calibrated_at,
        )

        def _call() -> tuple[object, int, dict]:
            return self._devices_api.patch_device_info_with_http_info(
                device_id=device.device_id,
                body=body,
            )

        extra: dict[str, Any] = {"device_id": device.device_id}

        logger.info(
            "PATCH /devices/{device_id}/device_info: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _call,
            "PATCH /devices/{device_id}/device_info",
            extra,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "PATCH /devices/{device_id}/device_info: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "body": response,
            },
        )
