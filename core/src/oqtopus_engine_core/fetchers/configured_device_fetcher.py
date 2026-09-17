import asyncio
import json
import logging
from typing import Any

# ruff: noqa: DOC501
from oqtopus_engine_core.framework import Device, DeviceFetcher
from oqtopus_engine_core.simulator.execution import SingleProcessLock
from oqtopus_engine_core.simulator.scheduler.slurm import SlurmClient

logger = logging.getLogger(__name__)


class ConfiguredDeviceFetcher(DeviceFetcher):
    """Initialize a simulator device from trusted engine configuration."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        device_id: str,
        n_qubits: int,
        basis_gates: list[str],
        instructions: list[str],
        device_info: dict[str, Any] | str,
        slurm_client: SlurmClient,
        process_lock: SingleProcessLock,
        description: str = "",
        healthcheck_interval_seconds: float = 30.0,
    ) -> None:
        super().__init__()
        if isinstance(device_info, str):
            parsed_device_info = json.loads(device_info)
        else:
            parsed_device_info = device_info
        if not isinstance(parsed_device_info, dict):
            message = "configured device_info must be a JSON object"
            raise TypeError(message)
        self._slurm_client = slurm_client
        self._process_lock = process_lock
        self._healthcheck_interval_seconds = healthcheck_interval_seconds
        self._device = Device(
            device_id=device_id,
            device_type="simulator",
            status="active",
            n_qubits=n_qubits,
            basis_gates=basis_gates,
            instructions=instructions,
            device_info=json.dumps(
                parsed_device_info,
                sort_keys=True,
                separators=(",", ":"),
            ),
            description=description,
            is_connected=False,
        )

    async def start(self) -> None:
        """Publish the configured device to the engine and Cloud repository."""
        self._process_lock.acquire()
        try:
            await self.initialize()
            while True:
                await self.check_health()
                await asyncio.sleep(self._healthcheck_interval_seconds)
        finally:
            self._process_lock.release()

    async def initialize(self) -> None:
        """Publish the configured device before health monitoring starts."""
        gctx = self.gctx
        if gctx is None:
            message = "Global context must be set before starting the fetcher."
            raise RuntimeError(message)
        if gctx.device_repository is None:
            message = "Device repository must be set before starting the fetcher."
            raise RuntimeError(message)

        device = self._device.model_copy(deep=True)
        gctx.device = device
        await gctx.device_repository.update_device(device)
        logger.info(
            "configured device initialized",
            extra={"device_id": device.device_id},
        )

    async def check_health(self) -> None:
        """Probe the SLURM controller and publish the resulting device status."""
        gctx = self.gctx
        if gctx is None or gctx.device is None or gctx.device_repository is None:
            message = "ConfiguredDeviceFetcher is not ready for a health check."
            raise RuntimeError(message)

        try:
            healthy = await self._slurm_client.healthcheck()
        except Exception:
            logger.exception(
                "SLURM health check failed",
                extra={"device_id": gctx.device.device_id},
            )
            healthy = False
        if healthy:
            gctx.device.is_connected = True
            gctx.device.status = "active"
        else:
            gctx.device.is_connected = False
            gctx.device.status = "inactive"
        await gctx.device_repository.update_device_status(gctx.device)
