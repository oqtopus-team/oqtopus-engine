"""Job-level observability helpers (job_id propagation + business metrics).

Provides:
- A SpanProcessor that copies ``oqtopus.*`` baggage entries onto every span,
  so any span created inside a job context (including auto-instrumented
  HTTP/DB spans) carries ``oqtopus.job_id`` and ``oqtopus.job_type``,
  enabling TraceQL lookups like ``{ .oqtopus.job_id = "..." }``. The baggage
  itself is attached by the pipeline when the job-level root span is opened.
- Meter instruments for job-level business metrics (counters + duration
  histogram) used by the pipeline to emit SLO-relevant signals.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import baggage, context, metrics, trace
from opentelemetry.instrumentation.grpc import (
    GrpcAioInstrumentorClient,
    GrpcAioInstrumentorServer,
)
from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
from opentelemetry.sdk.trace import SpanProcessor

if TYPE_CHECKING:
    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import Span

logger = logging.getLogger(__name__)

_BAGGAGE_PREFIX = "oqtopus."

_meter = metrics.get_meter(__name__)

job_ready_counter = _meter.create_counter(
    name="oqtopus.jobs.ready",
    description="Number of jobs that entered the pipeline",
    unit="1",
)
job_completed_counter = _meter.create_counter(
    name="oqtopus.jobs.completed",
    description="Number of jobs that finished (labelled by status)",
    unit="1",
)
job_duration_histogram = _meter.create_histogram(
    name="oqtopus.job.duration",
    description="Backend (engine) job processing duration",
    unit="s",
)


class JobBaggageSpanProcessor(SpanProcessor):
    """Copy ``oqtopus.*`` baggage keys onto span attributes on span start.

    The other ``SpanProcessor`` hook methods are inherited from the base
    class (all no-ops); only ``on_start`` is overridden.
    """

    def on_start(  # noqa: PLR6301
        self, span: Span, parent_context: Context | None = None
    ) -> None:
        """Mirror ``oqtopus.*`` baggage entries onto the starting span."""
        ctx = parent_context if parent_context is not None else context.get_current()
        for key, value in baggage.get_all(ctx).items():
            if key.startswith(_BAGGAGE_PREFIX) and value is not None:
                span.set_attribute(key, value)


def register_span_processor() -> None:
    """Register the baggage→attribute span processor on the active TracerProvider.

    Logs and returns silently if the active provider does not support
    ``add_span_processor`` (e.g. the default ProxyTracerProvider before SDK init).
    """
    provider = trace.get_tracer_provider()
    add_span_processor = getattr(provider, "add_span_processor", None)
    if add_span_processor is None:
        logger.info(
            "tracer provider does not support add_span_processor; "
            "job_id baggage enrichment will not be active",
            extra={"provider": type(provider).__name__},
        )
        return

    add_span_processor(JobBaggageSpanProcessor())
    logger.info("JobBaggageSpanProcessor registered")


def instrument_clients() -> None:
    """Enable OTel auto-instrumentation for gRPC and HTTP clients.

    With this, outgoing calls to tranqu / estimator / mitigator /
    device-gateway (gRPC) and oqtopus-cloud (urllib3) become child spans
    of the active pipeline span and propagate traceparent to the callee.

    Also enables the gRPC aio server instrumentor so the SSE engine
    gateway server picks up traceparent from incoming RPCs.
    """
    URLLib3Instrumentor().instrument()
    GrpcAioInstrumentorClient().instrument()
    GrpcAioInstrumentorServer().instrument()
    logger.info("gRPC/HTTP auto-instrumentation enabled")
