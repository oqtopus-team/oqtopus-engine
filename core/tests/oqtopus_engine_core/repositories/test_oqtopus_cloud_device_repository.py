from datetime import UTC, datetime
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from oqtopus_engine_core.framework import Device
from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    DevicesDeviceInfoUploadPresignedURL,
    DevicesDeviceInfoUploadResponse,
)
from oqtopus_engine_core.repositories.oqtopus_cloud_device_repository import (
    OqtopusCloudDeviceRepository,
)


def make_device() -> Device:
    return Device(
        device_id="qulacs",
        device_type="simulator",
        status="active",
        n_qubits=4,
        basis_gates=["x"],
        instructions=["measure"],
        device_info='{"device_id":"qulacs","qubits":[]}',
        calibrated_at=datetime(2026, 7, 6, tzinfo=UTC),
        description="simulator",
    )


def make_repo() -> OqtopusCloudDeviceRepository:
    with (
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
        ),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
        ),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
        ),
    ):
        return OqtopusCloudDeviceRepository(workers=2)


def make_test_device(device_id: str = "device-1") -> Device:
    """Minimal Device instance for request-timeout tests.

    Returns:
        A device suitable for repository request tests.

    """
    return Device(
        device_id=device_id,
        device_type="qpu",
        status="active",
        n_qubits=4,
        basis_gates=[],
        instructions=[],
        device_info="{}",
        calibrated_at=datetime(2026, 7, 6, tzinfo=UTC),
        description="",
    )


@pytest.mark.asyncio
async def test_update_device_passes_api_request_timeout_seconds():
    """update_device forwards the API request timeout to the generated client."""
    with (
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
        ) as mock_devices_api_cls,
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
        ),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
        ),
    ):
        mock_devices_api = mock_devices_api_cls.return_value
        mock_devices_api.patch_device_with_http_info.return_value = ({}, 200, {})

        repo = OqtopusCloudDeviceRepository(workers=1, api_request_timeout_seconds=15)
        await repo.update_device(make_test_device())

        _, kwargs = mock_devices_api.patch_device_with_http_info.call_args
        assert kwargs["_request_timeout"] == 15


@pytest.mark.asyncio
async def test_update_device_status_passes_api_request_timeout_seconds():
    """update_device_status forwards the API request timeout to the generated client."""
    with (
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
        ) as mock_devices_api_cls,
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
        ),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
        ),
    ):
        mock_devices_api = mock_devices_api_cls.return_value
        mock_devices_api.patch_device_status_with_http_info.return_value = ({}, 200, {})

        repo = OqtopusCloudDeviceRepository(workers=1, api_request_timeout_seconds=15)
        await repo.update_device_status(make_test_device())

        _, kwargs = mock_devices_api.patch_device_status_with_http_info.call_args
        assert kwargs["_request_timeout"] == 15


@pytest.mark.asyncio
async def test_update_device_info_passes_api_request_timeout_seconds():
    """update_device_info forwards the API request timeout to the generated client."""
    with (
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
        ) as mock_devices_api_cls,
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
        ),
        patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
        ),
    ):
        mock_devices_api = mock_devices_api_cls.return_value
        mock_devices_api.get_device_info_upload_url_with_http_info.return_value = (
            DevicesDeviceInfoUploadResponse(
                presigned_url=DevicesDeviceInfoUploadPresignedURL(
                    url="https://example.test/",
                    fields={"key": "devices/device-1/device_info.zip"},
                )
            ),
            200,
            {},
        )
        mock_devices_api.patch_device_info_with_http_info.return_value = ({}, 200, {})

        with patch(
            "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.OqtopusStorage.upload"
        ):
            repo = OqtopusCloudDeviceRepository(
                workers=1, api_request_timeout_seconds=15
            )
            await repo.update_device_info(make_test_device())

        _, kwargs = mock_devices_api.patch_device_info_with_http_info.call_args
        assert kwargs["_request_timeout"] == 15


@pytest.mark.asyncio
async def test_update_device_info_uploads_payload_before_patch():
    repo = make_repo()
    device = make_device()
    devices_api = repo._devices_api  # noqa: SLF001
    upload_response = DevicesDeviceInfoUploadResponse(
        presigned_url=DevicesDeviceInfoUploadPresignedURL(
            url="https://example.test/",
            fields={"key": "devices/qulacs/device_info.zip"},
        )
    )
    devices_api.get_device_info_upload_url_with_http_info.return_value = (
        upload_response,
        200,
        {},
    )
    devices_api.patch_device_info_with_http_info.return_value = (
        object(),
        200,
        {},
    )

    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.OqtopusStorage.upload"
    ) as upload:
        await repo.update_device_info(device)

    devices_api.get_device_info_upload_url_with_http_info.assert_called_once_with(
        device_id="qulacs",
        _request_timeout=10,
    )
    upload.assert_called_once_with(
        presigned_url=upload_response.presigned_url,
        data=device.device_info,
        arcname="device_info.json",
        proxies=None,
        timeout_s=60,
    )
    devices_api.patch_device_info_with_http_info.assert_called_once()
    patch_body = devices_api.patch_device_info_with_http_info.call_args.kwargs["body"]
    assert patch_body.calibrated_at == device.calibrated_at


@pytest.mark.asyncio
async def test_update_device_info_rejects_missing_payload():
    repo = make_repo()
    device = make_device()
    device.device_info = None

    with pytest.raises(ValueError, match=r"device\.device_info is required"):
        await repo.update_device_info(device)
