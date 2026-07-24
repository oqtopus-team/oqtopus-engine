import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from oqtopus_engine_core.framework import Device, DeviceRepository
from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    ApiClient,
    Configuration,
    DevicesApi,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    DevicesDeviceInfoUpdate,
    DevicesDeviceInfoUploadResponse,
    DevicesDeviceStatusUpdate,
    DevicesUpdateDeviceRequest,
)
from oqtopus_engine_core.interfaces.oqtopus_cloud.rest import ApiException
from oqtopus_engine_core.utils.storage_util import OqtopusStorage, OqtopusStorageError

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
        file_op_timeout_seconds: int = 60,
    ) -> None:
        """Initialize the device repository with the API URL and interval.

        Args:
            url: The endpoint URL to fetch jobs from.
            api_key: The API key for authentication.
            proxy: The proxy URL for the API request.
            workers: The number of concurrent workers to use for API requests.
            file_op_timeout_seconds: Timeout for file upload requests.

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
        self._proxy = proxy
        self._file_op_timeout_seconds = file_op_timeout_seconds

        logger.info(
            "OqtopusCloudDeviceRepository was initialized",
            extra={
                "url": url,
                "proxy": proxy,
                "workers": workers,
                "file_op_timeout_seconds": file_op_timeout_seconds,
            },
        )

    async def _request_with_error_logging(
        self,
        call: Callable[[], T],
        label: str,
        extra: dict[str, Any],
    ) -> T:
        """Call an API in a worker thread with logging and error handling.

        Args:
            call: Callable that performs the HTTP request and returns
                (data, status, headers).
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

        Raises:
            ValueError: If `device.device_info` is missing.
            OqtopusStorageError: If uploading device_info fails.

        """
        if device.device_info is None:
            msg = "device.device_info is required"
            raise ValueError(msg)

        extra: dict[str, Any] = {"device_id": device.device_id}

        def _get_upload_url_call() -> tuple[object, int, dict]:
            return self._devices_api.get_device_info_upload_url_with_http_info(
                device_id=device.device_id,
            )

        logger.info(
            "GET /devices/{device_id}/device_info/upload: request",
            extra=extra,
        )

        start = time.perf_counter()
        upload_response_obj, status_code, _ = await self._request_with_error_logging(
            _get_upload_url_call,
            "GET /devices/{device_id}/device_info/upload",
            extra,
        )
        upload_response = cast("DevicesDeviceInfoUploadResponse", upload_response_obj)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "GET /devices/{device_id}/device_info/upload: response",
            extra={
                "status_code": status_code,
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "body": upload_response,
            },
        )

        presigned_url = upload_response.presigned_url

        def _upload_call() -> None:
            proxies = (
                {"http": self._proxy, "https": self._proxy} if self._proxy else None
            )
            return OqtopusStorage.upload(
                presigned_url=presigned_url,
                data=device.device_info or "",
                arcname="device_info.json",
                proxies=proxies,
                timeout_s=self._file_op_timeout_seconds,
            )

        logger.info(
            "device_info upload started",
            extra={
                **extra,
                "url": presigned_url.url,
                "key": presigned_url.fields.get("key")
                if isinstance(presigned_url.fields, dict)
                else None,
            },
        )

        start = time.perf_counter()
        async with self._sem:
            try:
                await asyncio.to_thread(_upload_call)
            except OqtopusStorageError as ex:
                logger.info(
                    "device_info upload: storage error",
                    extra={
                        "error": str(ex),
                        **extra,
                    },
                )
                raise
            except Exception:
                logger.info(
                    "device_info upload: unexpected error",
                    extra=extra,
                )
                raise
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "device_info upload completed",
            extra={
                "elapsed_ms": round(elapsed_ms, 3),
                **extra,
                "url": presigned_url.url,
                "key": presigned_url.fields.get("key")
                if isinstance(presigned_url.fields, dict)
                else None,
            },
        )

        body = DevicesDeviceInfoUpdate(calibrated_at=device.calibrated_at)

        def _patch_call() -> tuple[object, int, dict]:
            return self._devices_api.patch_device_info_with_http_info(
                device_id=device.device_id,
                body=body,
            )

        logger.info(
            "PATCH /devices/{device_id}/device_info: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        response, status_code, _ = await self._request_with_error_logging(
            _patch_call,
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
