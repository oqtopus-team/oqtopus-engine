"""Job-level observability helpers (job_id propagation + business metrics).

Provides:
- A SpanProcessor that copies ``oqtopus.*`` baggage entries onto every span,
  so any span created inside a job context (including auto-instrumented
  HTTP/DB spans) carries ``oqtopus.job_id`` and ``oqtopus.job_type``,
  enabling TraceQL lookups like ``{ .oqtopus.job_id = "..." }``. The baggage
  itself is attached by the pipeline when the job-level root span is opened.
- A logging.Filter with the same job_id/job_type baggage-mirroring for log
  records, so job-related log lines can be filtered in Loki via LogQL
  (``| json | job_id = "..."``).
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

# Explicit baggage-key -> LogRecord-attribute mapping for JobBaggageLogFilter,
# rather than deriving the attribute name from the baggage key at runtime
# (e.g. via str.removeprefix). Baggage keys are just strings, so deriving a
# setattr target from one could land on a dunder or a reserved LogRecord
# attribute (msg, levelname, asctime, ...) and corrupt the record; this map
# only ever writes the two names below, both known non-reserved.
_BAGGAGE_TO_LOG_ATTR = {
    "oqtopus.job_id": "job_id",
    "oqtopus.job_type": "job_type",
}

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


class JobBaggageLogFilter(logging.Filter):
    """Copy ``_BAGGAGE_TO_LOG_ATTR``'s baggage keys onto LogRecord attributes.

    python-json-logger includes any non-reserved LogRecord attribute in the
    emitted JSON automatically, so the mirrored fields (``job_id``,
    ``job_type``) show up in log output without touching the formatter.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: PLR6301
        """Mirror the mapped baggage entries onto the record.

        Returns:
            Always ``True`` (this filter only enriches records, never drops them).

        """
        ctx_baggage = baggage.get_all(context.get_current())
        for baggage_key, attr_name in _BAGGAGE_TO_LOG_ATTR.items():
            value = ctx_baggage.get(baggage_key)
            if value is not None:
                setattr(record, attr_name, value)
        return True


def register_log_filter() -> None:
    """Attach the baggage→attribute log filter to the core logger's handlers.

    A ``logging.Filter`` only runs for the logger that actually emits the
    record, not for ancestor loggers it propagates to (see
    ``Logger.handle``/``Logger.callHandlers``). Since most core code logs
    through per-module child loggers (e.g. ``oqtopus_engine_core.framework.
    pipeline``) that propagate up to ``oqtopus_engine_core``'s handlers, the
    filter must be attached to handlers rather than to the
    ``oqtopus_engine_core`` logger object itself.

    Walks up the logger hierarchy the same way ``Logger.callHandlers`` does,
    so this still finds the right handlers if a deployment's logging config
    puts them on ``root`` instead of on ``oqtopus_engine_core`` directly.
    Skips handlers that already have the filter attached, so calling this
    more than once (e.g. across tests constructing multiple ``Engine``
    instances) doesn't double-enrich every record.

    Logs and returns silently if no handlers are found anywhere in the chain.
    """
    handlers: list[logging.Handler] = []
    current: logging.Logger | None = logging.getLogger("oqtopus_engine_core")
    while current is not None:
        handlers.extend(current.handlers)
        if not current.propagate:
            break
        current = current.parent

    if not handlers:
        logger.info(
            "no handlers found on oqtopus_engine_core or its ancestor loggers; "
            "job_id log enrichment will not be active"
        )
        return

    log_filter = JobBaggageLogFilter()
    for handler in handlers:
        if not any(isinstance(f, JobBaggageLogFilter) for f in handler.filters):
            handler.addFilter(log_filter)
    logger.info("JobBaggageLogFilter registered")


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
