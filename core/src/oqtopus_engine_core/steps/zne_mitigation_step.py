import json
import logging
from typing import Any

import grpc
from omegaconf import OmegaConf

from oqtopus_engine_core.framework import (
    EstimationResult,
    GlobalContext,
    Job,
    JobContext,
    JobResult,
    Step,
)
from oqtopus_engine_core.interfaces.mitigator_interface.v1 import (
    mitigator_pb2,
    mitigator_pb2_grpc,
)

logger = logging.getLogger(__name__)


class ZneMitigationStep(Step):
    """Prepare ZNE request in pre_process and apply response in post_process."""

    def __init__(
        self,
        mitigator_address: str = "localhost:52011",
        mitigator_timeout_seconds: float = 120.0,
        zne_default_config: dict[str, Any] | None = None,
    ) -> None:
        self._channel = grpc.aio.insecure_channel(mitigator_address)
        self._stub = mitigator_pb2_grpc.MitigatorServiceStub(self._channel)
        self._mitigator_timeout_seconds = mitigator_timeout_seconds
        self._zne_default_config = {
            "enabled": False,
            "scale_factors": [1.0, 2.0, 3.0],
            "factory": "richardson",
            "folding": "global",
            "num_to_average": 1,
            "fail_open": True,
            "poly_order": 2,
        }
        if zne_default_config:
            if OmegaConf.is_config(zne_default_config):
                resolved_default_config = OmegaConf.to_container(
                    zne_default_config,
                    resolve=True,
                )
            else:
                resolved_default_config = dict(zne_default_config)
            self._zne_default_config.update(resolved_default_config)
        logger.info(
            "ZneMitigationStep was initialized",
            extra={
                "mitigator_address": mitigator_address,
                "mitigator_timeout_seconds": mitigator_timeout_seconds,
                "zne_default_config": self._zne_default_config,
            },
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        zne_config = self._resolve_zne_config(job.mitigation_info or {})
        if not bool(zne_config.get("enabled", False)):
            return

        fail_open = bool(zne_config.get("fail_open", True))
        if job.job_type != "estimation":
            if fail_open:
                logger.warning(
                    "zne is configured for non-estimation job, skipping due to fail_open",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
                return
            msg = "zne is supported only for estimation jobs"
            raise ValueError(msg)

        if "estimation_job_info" not in jctx:
            if fail_open:
                logger.warning(
                    "estimation_job_info not found in jctx for zne, fallback allowed",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
                return
            raise ValueError("estimation_job_info not found in jctx for zne")

        estimation_job_info = jctx["estimation_job_info"]
        grouped_operators = estimation_job_info.grouped_operators
        if grouped_operators is None:
            if fail_open:
                logger.warning(
                    "grouped_operators is None in estimation_job_info for zne, fallback allowed",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
                return
            raise ValueError("grouped_operators is None in estimation_job_info for zne")

        grouped_operators_json = json.dumps(grouped_operators)
        zne_config_json = json.dumps(zne_config)
        pre_request = mitigator_pb2.ReqZnePreProcessRequest(
            job_id=job.job_id,
            programs=estimation_job_info.preprocessed_qasms or job.job_info.program,
            zne_config_json=zne_config_json,
        )
        try:
            pre_response = await self._stub.ReqZnePreProcess(
                pre_request,
                timeout=self._mitigator_timeout_seconds,
            )
        except Exception:
            if fail_open:
                logger.exception(
                    "ReqZnePreProcess failed, fallback allowed",
                    extra={"job_id": job.job_id, "job_type": job.job_type},
                )
                return
            raise

        jctx["zne_job_info"] = {
            "execution_programs": list(pre_response.execution_programs),
            "execution_results": [],
            "grouped_operators_json": grouped_operators_json,
            "zne_config_json": zne_config_json,
            "fail_open": fail_open,
        }

    async def post_process(
        self,
        gctx: GlobalContext,  # noqa: ARG002
        jctx: JobContext,
        job: Job,
    ) -> None:
        zne_job_info = jctx.get("zne_job_info")
        if not zne_job_info:
            return
        execution_results_raw = zne_job_info.get("execution_results") or []
        if len(execution_results_raw) == 0:
            return

        execution_results = [
            mitigator_pb2.ZneExecutionResult(
                scale_factor=float(result["scale_factor"]),
                repetition=int(result["repetition"]),
                program_index=int(result["program_index"]),
                counts=dict(result["counts"]),
            )
            for result in execution_results_raw
        ]

        post_request = mitigator_pb2.ReqZnePostProcessRequest(
            grouped_operators_json=zne_job_info["grouped_operators_json"],
            zne_config_json=zne_job_info["zne_config_json"],
            execution_results=execution_results,
        )
        post_response = await self._stub.ReqZnePostProcess(
            post_request,
            timeout=self._mitigator_timeout_seconds,
        )

        if job.job_info.result is None:
            job.job_info.result = JobResult()
        if job.job_info.result.estimation is None:
            job.job_info.result.estimation = EstimationResult()
        job.job_info.result.estimation.exp_value = float(post_response.exp_value)
        job.job_info.result.estimation.stds = float(post_response.stds)

    def _resolve_zne_config(self, mitigation_info: dict[str, Any]) -> dict[str, Any]:
        zne_cfg = dict(self._zne_default_config)
        req_cfg = mitigation_info.get("zne")
        if isinstance(req_cfg, dict):
            zne_cfg.update(req_cfg)
        return self._to_builtin(zne_cfg)

    def _to_builtin(self, value: Any) -> Any:
        if OmegaConf.is_config(value):
            return OmegaConf.to_container(value, resolve=True)
        if isinstance(value, dict):
            return {k: self._to_builtin(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._to_builtin(v) for v in value]
        return value
