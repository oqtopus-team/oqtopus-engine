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
    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
    ):
        return OqtopusCloudDeviceRepository(workers=2)


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
