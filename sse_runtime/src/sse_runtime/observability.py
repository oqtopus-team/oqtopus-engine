"""OTel observability helpers for sse-runtime.

When ``MONITORING_ENABLED=true``, registers a ``SpanProcessor`` that copies
``oqtopus.*`` baggage entries onto every span as attributes, attaches the
parent trace context from the ``TRACEPARENT`` env var (set by sse_step on the
engine side when it spawns this container), and seeds ``oqtopus.job_id`` /
``oqtopus.job_type`` baggage from ``JOB_JSON`` so every span created in this
process is parented under the engine's per-job root and carries the job
identifier without per-call decoration.

The TracerProvider itself is initialised by the ``opentelemetry-instrument``
auto-instrumentation entrypoint used to launch the user script; this module
does not configure exporters or the SDK.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from opentelemetry import baggage, context, trace
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

if TYPE_CHECKING:
    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import Span

logger = logging.getLogger(__name__)

_BAGGAGE_PREFIX = "oqtopus."


class JobBaggageSpanProcessor(SpanProcessor):
    """Copy ``oqtopus.*`` baggage keys onto span attributes on span start."""

    def on_start(  # noqa: PLR6301
        self, span: Span, parent_context: Context | None = None
    ) -> None:
        """Mirror ``oqtopus.*`` baggage entries onto the starting span."""
        ctx = parent_context if parent_context is not None else context.get_current()
        for key, value in baggage.get_all(ctx).items():
            if key.startswith(_BAGGAGE_PREFIX) and value is not None:
                span.set_attribute(key, value)  # type: ignore[arg-type]


def setup_observability() -> None:
    """Register baggage processor and attach parent context from env vars.

    Reads ``TRACEPARENT`` (W3C trace context) and ``JOB_JSON`` from the
    container environment and attaches both as the implicit current context,
    so subsequent spans are parented under the engine's per-job root span and
    carry ``oqtopus.job_id`` / ``oqtopus.job_type`` baggage automatically.
    """
    if os.environ.get("MONITORING_ENABLED", "false").lower() != "true":
        return

    provider = trace.get_tracer_provider()
    add_span_processor = getattr(provider, "add_span_processor", None)
    if add_span_processor is None:
        logger.info(
            "tracer provider does not support add_span_processor; "
            "oqtopus.* baggage enrichment will not be active",
            extra={"provider": type(provider).__name__},
        )
        return
    add_span_processor(JobBaggageSpanProcessor())

    ctx = context.get_current()

    traceparent = os.environ.get("TRACEPARENT")
    if traceparent:
        ctx = TraceContextTextMapPropagator().extract(
            {"traceparent": traceparent}, context=ctx
        )

    job_json = os.environ.get("JOB_JSON")
    if job_json:
        try:
            job_dict = json.loads(job_json)
        except json.JSONDecodeError:
            logger.exception("failed to parse JOB_JSON for baggage seeding")
        else:
            job_id = job_dict.get("job_id")
            if job_id:
                ctx = baggage.set_baggage("oqtopus.job_id", job_id, context=ctx)
            job_type = job_dict.get("job_type")
            if job_type:
                ctx = baggage.set_baggage("oqtopus.job_type", job_type, context=ctx)

    context.attach(ctx)
    logger.info("JobBaggageSpanProcessor registered, parent context attached")
