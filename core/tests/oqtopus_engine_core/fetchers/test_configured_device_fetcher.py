import pytest

from oqtopus_engine_core.fetchers import ConfiguredDeviceFetcher
from oqtopus_engine_core.framework import Device, DeviceRepository, GlobalContext


class RecordingDeviceRepository(DeviceRepository):
    def __init__(self):
        self.updated_devices: list[Device] = []
        self.updated_statuses: list[Device] = []
        self.updated_device_info: list[Device] = []

    async def update_device(self, device: Device) -> None:
        self.updated_devices.append(device)

    async def update_device_status(self, device: Device) -> None:
        self.updated_statuses.append(device)

    async def update_device_info(self, device: Device) -> None:
        self.updated_device_info.append(device)


class StubSlurmClient:
    def __init__(self, error: Exception | None = None, healthy: bool = True):
        self.error = error
        self.healthy = healthy

    async def healthcheck(self) -> bool:
        if self.error is not None:
            raise self.error
        return self.healthy


class StubProcessLock:
    def acquire(self) -> None:
        pass

    def release(self) -> None:
        pass


@pytest.mark.asyncio
async def test_start_initializes_configured_simulator_device():
    device_repository = RecordingDeviceRepository()
    gctx = GlobalContext(config={}, device_repository=device_repository)
    fetcher = ConfiguredDeviceFetcher(
        device_id="large-simulator",
        n_qubits=40,
        basis_gates=["x", "cx", "rz"],
        instructions=["measure"],
        device_info={"qubits": [0, 1], "couplings": [[0, 1]]},
        slurm_client=StubSlurmClient(),
        process_lock=StubProcessLock(),
        description="Qulacs MPI simulator",
    )
    fetcher.gctx = gctx

    await fetcher.initialize()
    await fetcher.check_health()

    assert gctx.device is not None
    assert gctx.device.device_id == "large-simulator"
    assert gctx.device.device_type == "simulator"
    assert gctx.device.status == "active"
    assert gctx.device.is_connected is True
    assert gctx.device.n_qubits == 40
    assert gctx.device.device_info == '{"couplings":[[0,1]],"qubits":[0,1]}'
    assert device_repository.updated_devices == [gctx.device]
    assert device_repository.updated_statuses == [gctx.device]
    assert device_repository.updated_device_info == []


@pytest.mark.asyncio
async def test_healthcheck_marks_device_inactive_on_slurm_failure():
    device_repository = RecordingDeviceRepository()
    gctx = GlobalContext(config={}, device_repository=device_repository)
    fetcher = ConfiguredDeviceFetcher(
        device_id="large-simulator",
        n_qubits=40,
        basis_gates=[],
        instructions=[],
        device_info={},
        slurm_client=StubSlurmClient(RuntimeError("controller unavailable")),
        process_lock=StubProcessLock(),
    )
    fetcher.gctx = gctx

    await fetcher.initialize()
    await fetcher.check_health()

    assert gctx.device is not None
    assert gctx.device.status == "inactive"
    assert gctx.device.is_connected is False
    assert device_repository.updated_statuses == [gctx.device]


@pytest.mark.asyncio
async def test_healthcheck_marks_device_inactive_when_partition_is_missing():
    device_repository = RecordingDeviceRepository()
    gctx = GlobalContext(config={}, device_repository=device_repository)
    fetcher = ConfiguredDeviceFetcher(
        device_id="large-simulator",
        n_qubits=40,
        basis_gates=[],
        instructions=[],
        device_info={},
        slurm_client=StubSlurmClient(healthy=False),
        process_lock=StubProcessLock(),
    )
    fetcher.gctx = gctx

    await fetcher.initialize()
    await fetcher.check_health()

    assert gctx.device is not None
    assert gctx.device.status == "inactive"
    assert gctx.device.is_connected is False


@pytest.mark.asyncio
async def test_start_requires_device_repository():
    fetcher = ConfiguredDeviceFetcher(
        device_id="large-simulator",
        n_qubits=40,
        basis_gates=[],
        instructions=[],
        device_info={},
        slurm_client=StubSlurmClient(),
        process_lock=StubProcessLock(),
    )
    fetcher.gctx = GlobalContext(config={})

    with pytest.raises(RuntimeError, match="Device repository"):
        await fetcher.start()