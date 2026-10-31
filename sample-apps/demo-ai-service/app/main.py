"""demo-ai-service: an inference-style service with configurable startup behaviour.

KubeSage uses it to reproduce cold-start scenarios. Startup work runs in a background
thread, so /healthz answers immediately and /ready only flips once the model is loaded.
That lets the profiler separate "app ready" from the Kubernetes Ready condition.

Each startup phase is emitted as an OpenTelemetry span (children of a root "startup" span
that begins at process start) and as a JSON log line.

Environment:
  MODEL_URL            weights to download at startup (optional)
  MODEL_DIR            directory for the weights (default /tmp/model)
  MODEL_CACHE          "true" to reuse a complete earlier download in MODEL_DIR
  INIT_CPU_WORK        units of CPU-bound initialisation work (default 0)
  DEPENDENCY_URL       URL that must answer 2xx before the app is ready (optional)
  DEPENDENCY_TIMEOUT   seconds to wait for the dependency (default 300)
  OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME, OTEL_RESOURCE_ATTRIBUTES  standard OTel settings
"""

import time

MODULE_START_NS = time.time_ns()

import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
import urllib.request  # noqa: E402
from contextlib import asynccontextmanager, contextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI, Response  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: E402
from opentelemetry.trace import Status, StatusCode  # noqa: E402

IMPORTS_DONE_NS = time.time_ns()

MODEL_URL = os.getenv("MODEL_URL", "")
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/tmp/model"))
MODEL_CACHE = os.getenv("MODEL_CACHE", "false").lower() == "true"
INIT_CPU_WORK = int(os.getenv("INIT_CPU_WORK", "0"))
DEPENDENCY_URL = os.getenv("DEPENDENCY_URL", "")
DEPENDENCY_TIMEOUT = float(os.getenv("DEPENDENCY_TIMEOUT", "300"))

CPU_ITERATIONS_PER_UNIT = 1_000_000
CHUNK_BYTES = 1 << 20


def process_start_ns() -> int:
    """Wall-clock start of this process from /proc (10 ms resolution); falls back to module import time."""
    try:
        stat = Path("/proc/self/stat").read_text()
        start_ticks = int(stat.rsplit(")", 1)[1].split()[19])
        boot_time = next(
            int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime")
        )
        return int((boot_time + start_ticks / os.sysconf("SC_CLK_TCK")) * 1e9)
    except (OSError, ValueError, IndexError, StopIteration):
        return MODULE_START_NS


PROCESS_START_NS = process_start_ns()


def log(event: str, **fields) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    print(json.dumps(record, default=str), flush=True)


def setup_tracing() -> TracerProvider | None:
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return None
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


PROVIDER = setup_tracing()
tracer = trace.get_tracer("demo-ai-service")


class State:
    ready = False
    failed: str | None = None
    weights_digest: str | None = None
    phases: list[dict] = []


def _span_attributes(record: dict) -> dict:
    return {k: v for k, v in record.items() if isinstance(v, (str, int, float, bool))}


@contextmanager
def phase(name: str, **attrs):
    """Time one startup phase as a span, a JSON log line and an entry in /startup-report."""
    start = time.time_ns()
    record = {"phase": name, **attrs}
    log("phase_start", phase=name)
    with tracer.start_as_current_span(name, start_time=start) as span:
        try:
            yield record
        except Exception as exc:
            record["error"] = str(exc)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        finally:
            end = time.time_ns()
            record.update(start_ns=start, end_ns=end, duration_s=round((end - start) / 1e9, 3))
            span.set_attributes(_span_attributes(record))
            State.phases.append(record)
            log("phase_end", **record)


def download_model() -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    target = MODEL_DIR / "weights.bin"
    marker = MODEL_DIR / "weights.complete"
    with phase("model.download", url=MODEL_URL) as record:
        if MODEL_CACHE and marker.exists() and target.exists():
            record.update(cache_hit=True, bytes=target.stat().st_size)
            return target
        record["cache_hit"] = False
        partial = MODEL_DIR / "weights.part"
        for attempt in range(1, 4):
            try:
                received = 0
                with urllib.request.urlopen(MODEL_URL, timeout=30) as resp, open(partial, "wb") as out:
                    while chunk := resp.read(CHUNK_BYTES):
                        out.write(chunk)
                        received += len(chunk)
                break
            except OSError as exc:
                if attempt == 3:
                    raise
                log("model_download_retry", attempt=attempt, error=str(exc))
                time.sleep(2 * attempt)
        os.replace(partial, target)
        marker.touch()
        record.update(bytes=received, attempts=attempt)
    return target


def load_model(path: Path) -> None:
    with phase("model.load", path=str(path)) as record:
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
        State.weights_digest = digest.hexdigest()
        record.update(bytes=size, digest=State.weights_digest[:16])


def cpu_init() -> None:
    with phase("init.compute", work_units=INIT_CPU_WORK):
        h = hashlib.sha256()
        for _ in range(INIT_CPU_WORK * CPU_ITERATIONS_PER_UNIT):
            h.update(b"kubesage")


def wait_for_dependency() -> None:
    with phase("dependency.connect", url=DEPENDENCY_URL) as record:
        deadline = time.monotonic() + DEPENDENCY_TIMEOUT
        attempts = 0
        while True:
            attempts += 1
            try:
                with urllib.request.urlopen(DEPENDENCY_URL, timeout=5) as resp:
                    if 200 <= resp.status < 300:
                        break
            except OSError as exc:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"dependency not ready after {attempts} attempts: {exc}") from exc
                log("dependency_retry", attempt=attempts, error=str(exc))
            time.sleep(1)
        record["attempts"] = attempts


def predict(text: str) -> dict:
    seed = State.weights_digest or "no-model"
    d = hashlib.sha256(f"{seed}:{text}".encode()).digest()
    return {"label": ("positive", "neutral", "negative")[d[0] % 3], "score": round(d[1] / 255, 3)}


def run_startup() -> None:
    with tracer.start_as_current_span("startup", start_time=PROCESS_START_NS) as root:
        tracer.start_span("process.boot", start_time=PROCESS_START_NS).end(end_time=MODULE_START_NS)
        tracer.start_span("import", start_time=MODULE_START_NS).end(end_time=IMPORTS_DONE_NS)
        State.phases += [
            {"phase": "process.boot", "start_ns": PROCESS_START_NS, "end_ns": MODULE_START_NS,
             "duration_s": round((MODULE_START_NS - PROCESS_START_NS) / 1e9, 3)},
            {"phase": "import", "start_ns": MODULE_START_NS, "end_ns": IMPORTS_DONE_NS,
             "duration_s": round((IMPORTS_DONE_NS - MODULE_START_NS) / 1e9, 3)},
        ]
        try:
            if MODEL_URL:
                load_model(download_model())
            if INIT_CPU_WORK > 0:
                cpu_init()
            if DEPENDENCY_URL:
                wait_for_dependency()
            with phase("warmup"):
                predict("warmup")
            State.ready = True
            log("startup_complete", startup_s=round((time.time_ns() - PROCESS_START_NS) / 1e9, 3))
        except Exception as exc:
            State.failed = str(exc)
            root.set_status(Status(StatusCode.ERROR, str(exc)))
            log("startup_failed", error=str(exc))
    if PROVIDER:
        # Trial pods can be deleted soon after Ready; don't lose the startup trace.
        PROVIDER.force_flush(timeout_millis=5000)


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=run_startup, name="startup", daemon=True).start()
    yield
    if PROVIDER:
        PROVIDER.shutdown()


# FastAPI's native telemetry would emit a span per readiness probe (every second) and try to export
# metrics/logs to Jaeger, which only accepts traces. Only the startup spans above are wanted.
app = FastAPI(
    title="demo-ai-service",
    lifespan=lifespan,
    telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response) -> dict:
    if not State.ready:
        response.status_code = 503
    return {"ready": State.ready, "failed": State.failed}


@app.get("/predict")
def predict_endpoint(response: Response, text: str = "hello") -> dict:
    if not State.ready:
        response.status_code = 503
        return {"error": "model not ready"}
    return predict(text)


@app.get("/startup-report")
def startup_report() -> dict:
    return {
        "ready": State.ready,
        "failed": State.failed,
        "process_start_ns": PROCESS_START_NS,
        "phases": State.phases,
    }
