"""OpenTelemetry: the domain instruments and the API wiring (/metrics, optional OTLP traces).

The instruments come from the global meter, so services record into them unconditionally: in the
CLI and the MCP server no provider is set and the calls are no-ops; ``instrument_app`` installs the
SDK provider for the API, and the proxy instruments start exporting from then on.
"""

import os
from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from buysmarter import __version__

if TYPE_CHECKING:
    from fastapi import FastAPI, Response

# The fastapi/Prometheus imports below sit inside the functions: they come with the [server]
# extra, and the core (CLI, MCP, GUI) imports this module for the instruments alone.

# Stable HTTP semconv: http_server_request_duration_seconds, labelled by http_route. Read once,
# by the first instrumentor (taskiq's, at worker import), so it is set when this module loads.
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

_meter = metrics.get_meter("buysmarter", __version__)

# Label values stay low-cardinality: no query text, prices or product ids.
RECOMMEND_DURATION = _meter.create_histogram(
    "buysmarter.recommend.duration",
    unit="s",
    description="Wall time of one recommend run, by outcome (ok, no_match, error)",
)
OFFERS_RANKED = _meter.create_histogram(
    "buysmarter.recommend.offers_ranked",
    description="Candidates left in the ranked pool of a successful run",
)
SOURCE_REQUESTS = _meter.create_counter(
    "buysmarter.source.requests",
    description="Marketplace searches, by source (kaspi, ozon, olx) and outcome (ok, error)",
)
LLM_CALLS = _meter.create_counter(
    "buysmarter.llm.calls",
    description="LLM generations, by model and cache (hit, miss)",
)
JOBS = _meter.create_counter(
    "buysmarter.jobs",
    description="Finished recommend jobs, by status (done, no_match, failed)",
)
JOB_WAIT = _meter.create_histogram(
    "buysmarter.job.wait",
    unit="s",
    description="Time a recommend job spent queued before a worker picked it up",
)
RATE_LIMITED = _meter.create_counter(
    "buysmarter.rate_limited",
    description="POST /recommend requests refused with 429",
)

# Probes and the scrape itself would drown the request metrics.
_UNTRACED_PATHS = "health,ready,metrics"


def instrument_app(app: "FastAPI") -> None:
    """Install the SDK providers, instrument FastAPI/httpx/SQLAlchemy and serve ``GET /metrics``."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    _install_providers()
    FastAPIInstrumentor.instrument_app(app, excluded_urls=_UNTRACED_PATHS)
    _instrument_clients()
    app.add_api_route("/metrics", _metrics_endpoint, include_in_schema=False)


def instrument_worker(metrics_port: int) -> None:
    """The worker's side: same providers and client instrumentation, /metrics on its own port."""
    from prometheus_client import start_http_server

    _install_providers()
    _instrument_clients()
    start_http_server(metrics_port)


def _instrument_clients() -> None:
    """Spans and metrics for outgoing HTTP (the marketplaces) and SQL."""
    from buysmarter.db.base import engine

    HTTPXClientInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def _install_providers() -> None:
    """Global meter provider with the Prometheus reader; a tracer provider only for OTLP.

    Traces are exported only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set; the exporter reads the
    rest of its configuration from the standard ``OTEL_*`` variables.
    """
    from opentelemetry.exporter.prometheus import PrometheusMetricReader

    resource = Resource.create({SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "buysmarter")})
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[PrometheusMetricReader()])
    )
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)


def _metrics_endpoint() -> "Response":
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
