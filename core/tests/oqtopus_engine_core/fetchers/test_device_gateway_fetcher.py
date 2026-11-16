from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oqtopus_engine_core.fetchers.device_gateway_fetcher import DeviceGatewayFetcher
from oqtopus_engine_core.framework import Device
from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2


def make_device(max_shots: int | None) -> Device:
    return Device(
        device_id="qulacs",
        device_type="simulator",
        status="active",
        n_qubits=4,
        max_shots=max_shots,
        basis_gates=[],
        instructions=[],
        device_info="{}",
        calibrated_at=datetime(2026, 1, 1, tzinfo=UTC),
        description="",
        is_connected=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("gateway_limit", "expected"), [(1000, 1000), (0, None)])
async def test_fetch_device_reads_gateway_max_shots(
    gateway_limit: int, expected: int | None
) -> None:
    fetcher = DeviceGatewayFetcher("localhost:51021")
    fetcher._stub = MagicMock()
    fetcher._stub.GetDeviceInfo = AsyncMock(
        return_value=SimpleNamespace(
            body=SimpleNamespace(
                device_id="qulacs",
                type="simulator",
                max_qubits=4,
                max_shots=gateway_limit,
                device_info="{}",
                calibrated_at="2026-01-01T00:00:00Z",
            )
        )
    )
    fetcher._stub.GetServiceStatus = AsyncMock(
        return_value=SimpleNamespace(
            service_status=qpu_pb2.ServiceStatus.SERVICE_STATUS_ACTIVE
        )
    )

    device = await fetcher._fetch_device()

    assert device.max_shots == expected


@pytest.mark.asyncio
async def test_limit_update_is_retried_after_cloud_failure() -> None:
    fetcher = DeviceGatewayFetcher("localhost:51021")
    repository = MagicMock()
    repository.update_device = AsyncMock(
        side_effect=[RuntimeError("unavailable"), None, None]
    )
    fetcher.gctx = SimpleNamespace(
        device=make_device(2000), device_repository=repository
    )
    updated = make_device(1000)

    with pytest.raises(RuntimeError, match="unavailable"):
        await fetcher._update_if_changed(updated)

    assert fetcher.gctx.device.max_shots == 1000
    await fetcher._update_if_changed(updated)
    assert repository.update_device.await_count == 2

    await fetcher._update_if_changed(make_device(None))
    assert fetcher.gctx.device.max_shots is None
    assert repository.update_device.await_count == 3
