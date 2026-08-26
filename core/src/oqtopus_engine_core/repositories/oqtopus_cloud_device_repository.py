import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any, TypeVar

from oqtopus_engine_core.auth import (
    BearerAuthApiClient,
    ClientCredentialsTokenProvider,
)
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


def _build_api_client(  # noqa: PLR0913
    rest_config: Configuration,
    *,
    api_key: str,
    auth_mode: str,
    oidc_token_url: str,
    oidc_client_id: str,
    oidc_client_secret: str,
    oidc_scope: str,
    oidc_expiry_skew_seconds: int,
) -> ApiClient:
    """Build the generated ApiClient for the configured auth mode.

    Args:
        rest_config: The generated-client configuration (host, proxy, ...).
        api_key: Static API key for ``auth_mode="api_key"``.
        auth_mode: ``"api_key"`` or ``"oidc"``. Empty falls back to the
            ``AUTH_MODE`` env var, then to ``"api_key"``.
        oidc_token_url: IdP token endpoint for ``auth_mode="oidc"``.
        oidc_client_id: Confidential client id for ``auth_mode="oidc"``.
        oidc_client_secret: Confidential client secret for ``auth_mode="oidc"``.
        oidc_scope: Space-delimited scopes to request.
        oidc_expiry_skew_seconds: Refresh the token this many seconds early.

    Returns:
        A ``BearerAuthApiClient`` for ``oidc`` mode, else a plain ``ApiClient``
        carrying the static ``x-api-key`` header.

    """
    # Precedence: explicit per-repository auth_mode > AUTH_MODE env > "api_key".
    mode = (auth_mode or os.getenv("AUTH_MODE", "")).strip().lower() or "api_key"
    if mode == "oidc":
        token_provider = ClientCredentialsTokenProvider(
            token_url=oidc_token_url,
            client_id=oidc_client_id,
            client_secret=oidc_client_secret,
            scope=oidc_scope,
            expiry_skew_seconds=oidc_expiry_skew_seconds,
        )
        return BearerAuthApiClient(
            configuration=rest_config,
            token_provider=token_provider,
        )
    return ApiClient(
        configuration=rest_config,
        header_name="x-api-key",
        header_value=api_key,
    )


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

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        url: str = "http://localhost:8888",
        api_key: str = "",
        proxy: str | None = None,
        workers: int = 5,
        api_request_timeout_seconds: int = 10,
        auth_mode: str = "",
        oidc_token_url: str = "",
        oidc_client_id: str = "",
        oidc_client_secret: str = "",
        oidc_scope: str = "",
        oidc_expiry_skew_seconds: int = 60,
    ) -> None:
        """Initialize the device repository with the API URL and interval.

        Args:
            url: The endpoint URL to fetch jobs from.
            api_key: The API key for authentication (``auth_mode="api_key"``).
            proxy: The proxy URL for the API request.
            workers: The number of concurrent workers to use for API requests.
            api_request_timeout_seconds: Timeout for Devices API HTTP requests.
            auth_mode: ``"api_key"`` (static ``x-api-key`` header) or ``"oidc"``
                (OAuth2 client-credentials bearer token). Empty (default) falls
                back to the ``AUTH_MODE`` env var, then to ``"api_key"``.
            oidc_token_url: IdP token endpoint (``auth_mode="oidc"``).
            oidc_client_id: Confidential client id (``auth_mode="oidc"``).
            oidc_client_secret: Confidential client secret (``auth_mode="oidc"``).
            oidc_scope: Space-delimited scopes to request, e.g. ``provider.write``.
            oidc_expiry_skew_seconds: Refresh the token this many seconds before
                it expires.

        """
        super().__init__()
        # Construct DevicesApi
        rest_config = Configuration()
        rest_config.host = url
        if proxy:
            rest_config.proxy = proxy
        api_client = _build_api_client(
            rest_config,
            api_key=api_key,
            auth_mode=auth_mode,
            oidc_token_url=oidc_token_url,
            oidc_client_id=oidc_client_id,
            oidc_client_secret=oidc_client_secret,
            oidc_scope=oidc_scope,
            oidc_expiry_skew_seconds=oidc_expiry_skew_seconds,
        )
        self._devices_api = DevicesApi(api_client=api_client)
        self._sem = asyncio.Semaphore(workers)
        self._api_request_timeout_seconds = api_request_timeout_seconds

        logger.info(
            "OqtopusCloudDeviceRepository was initialized",
            extra={
                "url": url,
                "proxy": proxy,
                "workers": workers,
                "api_request_timeout_seconds": api_request_timeout_seconds,
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
            The data returned by the call.

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
                _request_timeout=self._api_request_timeout_seconds,
            )

        extra: dict[str, Any] = {"device_id": device.device_id}

        logger.info(
            "PATCH /devices/{device_id}: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        result = await self._request_with_error_logging(
            _call,
            "PATCH /devices/{device_id}",
            extra,
        )
        response, status_code, _ = result
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
                _request_timeout=self._api_request_timeout_seconds,
            )

        logger.info(
            "PATCH /devices/{device_id}/status: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        result = await self._request_with_error_logging(
            _call,
            "PATCH /devices/{device_id}/status",
            extra,
        )
        response, status_code, _ = result
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
                _request_timeout=self._api_request_timeout_seconds,
            )

        extra: dict[str, Any] = {"device_id": device.device_id}

        logger.info(
            "PATCH /devices/{device_id}/device_info: request",
            extra={**extra, "body": body},
        )

        start = time.perf_counter()
        result = await self._request_with_error_logging(
            _call,
            "PATCH /devices/{device_id}/device_info",
            extra,
        )
        response, status_code, _ = result
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
