from unittest.mock import patch

import pytest

from oqtopus_engine_core.framework.model import Device
from oqtopus_engine_core.repositories.oqtopus_cloud_device_repository import (
    OqtopusCloudDeviceRepository,
)


def make_test_device(device_id: str = "device-1") -> Device:
    """Minimal Device instance for use in repository unit tests."""
    return Device(
        device_id=device_id,
        device_type="qpu",
        status="active",
        n_qubits=4,
        basis_gates=[],
        instructions=[],
        device_info="{}",
        description="",
    )


# ---------------------------------------------------------------------------
# update_device / update_device_status / update_device_info –
# api_request_timeout_seconds is passed to the generated client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_device_passes_api_request_timeout_seconds():
    """update_device must forward api_request_timeout_seconds as _request_timeout."""
    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
    ) as mock_devices_api_cls, patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
    ):
        mock_devices_api = mock_devices_api_cls.return_value
        mock_devices_api.patch_device_with_http_info.return_value = ({}, 200, {})

        repo = OqtopusCloudDeviceRepository(workers=1, api_request_timeout_seconds=15)
        await repo.update_device(make_test_device())

        _, kwargs = mock_devices_api.patch_device_with_http_info.call_args
        assert kwargs["_request_timeout"] == 15


@pytest.mark.asyncio
async def test_update_device_status_passes_api_request_timeout_seconds():
    """update_device_status must forward api_request_timeout_seconds as _request_timeout."""
    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
    ) as mock_devices_api_cls, patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
    ):
        mock_devices_api = mock_devices_api_cls.return_value
        mock_devices_api.patch_device_status_with_http_info.return_value = ({}, 200, {})

        repo = OqtopusCloudDeviceRepository(workers=1, api_request_timeout_seconds=15)
        await repo.update_device_status(make_test_device())

        _, kwargs = mock_devices_api.patch_device_status_with_http_info.call_args
        assert kwargs["_request_timeout"] == 15


@pytest.mark.asyncio
async def test_update_device_info_passes_api_request_timeout_seconds():
    """update_device_info must forward api_request_timeout_seconds as _request_timeout."""
    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
    ) as mock_devices_api_cls, patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
    ):
        mock_devices_api = mock_devices_api_cls.return_value
        mock_devices_api.patch_device_info_with_http_info.return_value = ({}, 200, {})

        repo = OqtopusCloudDeviceRepository(workers=1, api_request_timeout_seconds=15)
        await repo.update_device_info(make_test_device())

        _, kwargs = mock_devices_api.patch_device_info_with_http_info.call_args
        assert kwargs["_request_timeout"] == 15


# ---------------------------------------------------------------------------
# auth_mode resolution precedence:
#   explicit auth_mode  >  AUTH_MODE env  >  "api_key"
# ---------------------------------------------------------------------------


def test_auth_mode_falls_back_to_env(monkeypatch):
    """With no per-repo auth_mode, the AUTH_MODE env var selects the mode."""
    monkeypatch.setenv("AUTH_MODE", "oidc")
    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
    ) as api_client_cls, patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.BearerAuthApiClient"
    ) as bearer_cls, patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ClientCredentialsTokenProvider"
    ):
        OqtopusCloudDeviceRepository(workers=1)

    bearer_cls.assert_called_once()
    api_client_cls.assert_not_called()


def test_explicit_auth_mode_beats_env(monkeypatch):
    """An explicit auth_mode overrides the AUTH_MODE env var."""
    monkeypatch.setenv("AUTH_MODE", "oidc")
    with patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.DevicesApi"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.Configuration"
    ), patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.ApiClient"
    ) as api_client_cls, patch(
        "oqtopus_engine_core.repositories.oqtopus_cloud_device_repository.BearerAuthApiClient"
    ) as bearer_cls:
        OqtopusCloudDeviceRepository(workers=1, auth_mode="api_key")

    api_client_cls.assert_called_once()
    bearer_cls.assert_not_called()
