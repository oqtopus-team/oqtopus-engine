import math
import re
from pathlib import Path

# ruff: noqa: DOC201, DOC501
from oqtopus_engine_core.framework import (
    EstimationResult,
    JobResult,
    SamplingResult,
)

from .models import QulacsExecutionRequest, QulacsExecutionResult

_BITSTRING = re.compile(r"^[01]+$")
_COMPLEX_PART_COUNT = 2


def read_worker_result(
    path: Path,
    request: QulacsExecutionRequest,
    *,
    imaginary_tolerance: float,
) -> tuple[JobResult, float]:
    """Validate a worker result and convert it to the existing Engine model."""
    result = QulacsExecutionResult.model_validate_json(path.read_text(encoding="utf-8"))
    if result.job_type != request.job_type:
        message = "worker result job_type does not match request"
        raise ValueError(message)

    if request.job_type == "sampling":
        return _read_sampling_result(result, request)

    return _read_estimation_result(result, imaginary_tolerance)


def _read_sampling_result(
    result: QulacsExecutionResult,
    request: QulacsExecutionRequest,
) -> tuple[JobResult, float]:
    if result.counts is None or result.exp_value is not None:
        message = "sampling worker result must contain counts only"
        raise ValueError(message)
    if request.shots is None:
        message = "sampling request is missing shots"
        raise ValueError(message)
    bit_width = len(request.measurement_mapping)
    for bitstring, count in result.counts.items():
        if not _BITSTRING.fullmatch(bitstring) or len(bitstring) != bit_width:
            message = f"invalid sampling bitstring: {bitstring!r}"
            raise ValueError(message)
        if isinstance(count, bool) or count < 0:
            message = f"invalid sampling count for {bitstring!r}"
            raise ValueError(message)
    if sum(result.counts.values()) != request.shots:
        message = "sampling counts do not sum to shots"
        raise ValueError(message)
    return (
        JobResult(sampling=SamplingResult(counts=result.counts)),
        result.duration_seconds,
    )


def _read_estimation_result(
    result: QulacsExecutionResult,
    imaginary_tolerance: float,
) -> tuple[JobResult, float]:

    if result.exp_value is None or result.counts is not None:
        message = "estimation worker result must contain exp_value only"
        raise ValueError(message)
    if len(result.exp_value) != _COMPLEX_PART_COUNT or not all(
        math.isfinite(value) for value in result.exp_value
    ):
        message = "estimation exp_value must contain finite real and imaginary parts"
        raise ValueError(message)
    real_value, imaginary_value = result.exp_value
    if abs(imaginary_value) > imaginary_tolerance:
        message = (
            "estimation result imaginary component exceeds tolerance: "
            f"{imaginary_value}"
        )
        raise ValueError(message)
    return (
        JobResult(
            estimation=EstimationResult(
                exp_value=real_value,
                stds=0.0,
            )
        ),
        result.duration_seconds,
    )
