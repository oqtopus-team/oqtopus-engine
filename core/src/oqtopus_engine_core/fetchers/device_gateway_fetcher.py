import asyncio
import logging

import grpc

from oqtopus_engine_core.framework import Device, DeviceFetcher
from oqtopus_engine_core.interfaces.qpu_interface.v1 import qpu_pb2, qpu_pb2_grpc
from oqtopus_engine_core.interfaces.qpu_interface.v1.qpu_pb2 import ServiceStatus

logger = logging.getLogger(__name__)


def service_status_to_label(status: int) -> str:
    """Convert a ServiceStatus enum value (gRPC) to a human-friendly string.

    Args:
        status: The ServiceStatus value.

    Returns:
        "active" if status == SERVICE_STATUS_ACTIVE,
        "inactive" if status is SERVICE_STATUS_INACTIVE or SERVICE_STATUS_MAINTENANCE,
        otherwise "inactive" by default.

    """
    if status == ServiceStatus.SERVICE_STATUS_ACTIVE:
        return "active"
    # Covers INACTIVE (1), MAINTENANCE (2), and any unknown values
    return "inactive"


class DeviceGatewayFetcher(DeviceFetcher):
    """Periodically fetch device info and service status from the device gateway."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        gateway_address: str,
        initial_interval_seconds: float = 10.0,
        initial_backoff_max_seconds: float = 60.0,
        loop_interval_seconds: float = 60.0,
        loop_backoff_max_seconds: float = 300.0,
        enable_device_info_update: bool = True,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize the DeviceGatewayFetcher.

        Args:
            gateway_address: The address of the device gateway
                (e.g., "localhost:50051").
            initial_interval_seconds: Initial fetch interval in seconds.
            initial_backoff_max_seconds: Maximum backoff time for initial fetch.
            loop_interval_seconds: Fetch interval in seconds after initialization.
            loop_backoff_max_seconds: Maximum backoff time for loop fetch in seconds.
            enable_device_info_update: Whether to update device info from gateway.

        """
        super().__init__()

        # Construct gRPC channel and stub
        self._channel = grpc.aio.insecure_channel(gateway_address)
        self._stub = qpu_pb2_grpc.QpuServiceStub(self._channel)
        self._initial_interval_seconds = initial_interval_seconds
        self._initial_backoff_max_seconds = initial_backoff_max_seconds
        self._loop_interval_seconds = loop_interval_seconds
        self._loop_backoff_max_seconds = loop_backoff_max_seconds
        self._enable_device_info_update = enable_device_info_update

        logger.info(
            "DeviceGatewayFetcher was initialized",
            extra={
                "gateway_address": gateway_address,
                "initial_interval_seconds": initial_interval_seconds,
                "initial_backoff_max_seconds": initial_backoff_max_seconds,
                "loop_interval_seconds": loop_interval_seconds,
                "loop_backoff_max_seconds": loop_backoff_max_seconds,
                "enable_device_info_update": enable_device_info_update,
            },
        )

    async def _fetch_device(self) -> Device:
        device_info_resp = await self._stub.GetDeviceInfo(
            qpu_pb2.GetDeviceInfoRequest()
        )

        service_status_resp = await self._stub.GetServiceStatus(
            qpu_pb2.GetServiceStatusRequest()
        )
        logger.debug(
            "GetServiceStatus response: %d",
            service_status_resp.service_status,
        )

        return Device(
            device_id=device_info_resp.body.device_id,
            device_type=device_info_resp.body.type,
            status=service_status_to_label(service_status_resp.service_status),
            # provider_id=device_info_resp.body.provider_id,  # noqa: ERA001
            n_qubits=device_info_resp.body.max_qubits,
            basis_gates=[],
            instructions=[],
            # max_shots=device_info_resp.body.max_shots,  # noqa: ERA001
            device_info=device_info_resp.body.device_info,
            calibrated_at=device_info_resp.body.calibrated_at,
            description="",
            is_connected=True,
        )

    async def start(self) -> None:
        """Periodically fetch device info and service status from the device gateway.

        This method will run indefinitely, fetching the device gateway for
        device info and service status at the specified interval.
        """
        logger.info("DeviceGatewayFetcher was started")

        # 1. Initial fetch device info once at startup
        await self._run_initial_fetch_loop()

        # 2. Main periodic fetch loop
        logger.info("starting device fetch loop")
        current_backoff = self._loop_interval_seconds
        consecutive_errors = 0

        while True:
            try:
                device = await self._fetch_device()

                # Reset backoff and error count on success
                consecutive_errors = 0
                current_backoff = self._loop_interval_seconds

                # Compare and update specific fields if changed
                await self._update_if_changed(device)

                await asyncio.sleep(current_backoff)

            except Exception:
                self.gctx.device.is_connected = False
                consecutive_errors += 1
                logger.exception(
                    "failed to fetch device, will retry with backoff",
                    extra={
                        "sleep_seconds": current_backoff,
                        "consecutive_errors": consecutive_errors,
                    },
                )
                await asyncio.sleep(current_backoff)
                current_backoff = min(
                    current_backoff * 2, self._loop_backoff_max_seconds
                )

    async def _run_initial_fetch_loop(self) -> None:
        """Fetch device info once at startup with exponential backoff."""
        # fetch device info once at startup
        current_backoff = self._initial_interval_seconds
        while True:
            try:
                # update device in global context
                device = await self._fetch_device()
                logger.info(
                    "initial device fetched",
                    extra={"device_id": device.device_id, "device": device},
                )
                self.gctx.device = device

                # update device info and status in cloud
                await self.gctx.device_repository.update_device(device)
                if self._enable_device_info_update:
                    await self.gctx.device_repository.update_device_info(device)
                await self.gctx.device_repository.update_device_status(device)
                break

            except Exception:
                logger.exception(
                    "failed to fetch device, will retry with backoff",
                    extra={"sleep_seconds": current_backoff},
                )
                await asyncio.sleep(current_backoff)
                current_backoff = min(
                    current_backoff * 2, self._initial_backoff_max_seconds
                )

    async def _update_if_changed(self, device: Device) -> None:
        """Compare current device state with global context and update if necessary."""
        # Check is_connected
        if device.is_connected != self.gctx.device.is_connected:
            logger.info(
                "device is_connected changed",
                extra={
                    "device_id": device.device_id,
                    "prev_is_connected": self.gctx.device.is_connected,
                    "curr_is_connected": device.is_connected,
                },
            )
            self.gctx.device.is_connected = device.is_connected

        # Check n_qubits
        if device.n_qubits != self.gctx.device.n_qubits:
            logger.info(
                "device n_qubits changed",
                extra={
                    "device_id": device.device_id,
                    "prev_n_qubits": self.gctx.device.n_qubits,
                    "curr_n_qubits": device.n_qubits,
                },
            )
            self.gctx.device.n_qubits = device.n_qubits
            await self.gctx.device_repository.update_device(device)

        # Check device_info and calibrated_at
        if (device.device_info != self.gctx.device.device_info) or (
            device.calibrated_at != self.gctx.device.calibrated_at
        ):
            device_info_changed = (device.device_info != self.gctx.device.device_info)
            logger.info(
                "device info/calibrated_at changed",
                extra={
                    "device_id": device.device_id,
                    "device_info_changed": device_info_changed,
                    "prev_calibrated_at": self.gctx.device.calibrated_at,
                    "curr_calibrated_at": device.calibrated_at,
                },
            )
            self.gctx.device.device_info = device.device_info
            self.gctx.device.calibrated_at = device.calibrated_at
            if self._enable_device_info_update:
                await self.gctx.device_repository.update_device_info(device)

        # Check status
        if device.status != self.gctx.device.status:
            logger.info(
                "device status changed",
                extra={
                    "device_id": device.device_id,
                    "prev_status": self.gctx.device.status,
                    "curr_status": device.status,
                },
            )
            self.gctx.device.status = device.status
            await self.gctx.device_repository.update_device_status(device)
