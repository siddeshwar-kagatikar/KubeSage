"""Runs Copilot diagnoses in the background and persists runs, evidence and incident status."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from kubesage.collectors.kube import new_api_client, node_capacity
from kubesage.collectors.prometheus import PrometheusClient
from kubesage.config import Settings
from kubesage.copilot.context import load_context
from kubesage.copilot.evidence import EvidenceStore
from kubesage.copilot.graph import run_copilot
from kubesage.copilot.prompts import PROMPT_VERSION
from kubesage.copilot.tools import build_tools
from kubesage.db.models import DiagnosisRun, Evidence, Incident, utcnow
from kubesage.db.session import session_scope
from kubesage.llm.calls import CallLogger
from kubesage.llm.provider import create_chat_model

log = logging.getLogger(__name__)


class DiagnosisInProgress(RuntimeError):
    pass


class CopilotService:
    def __init__(self, settings: Settings, model_factory=create_chat_model):
        self.settings = settings
        self.model_factory = model_factory
        self.prometheus = PrometheusClient(settings.prometheus_url)
        # One diagnosis at a time keeps LLM quota use predictable.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="copilot")
        self._lock = threading.Lock()
        self._active: set[int] = set()

    def start(self, incident_id: int) -> int:
        with self._lock:
            if incident_id in self._active:
                raise DiagnosisInProgress(f"incident {incident_id} is already being diagnosed")
            self._active.add(incident_id)
        try:
            with session_scope() as session:
                if session.get(Incident, incident_id) is None:
                    raise LookupError(f"incident {incident_id} not found")
                run = DiagnosisRun(
                    incident_id=incident_id,
                    status="running",
                    provider=self.settings.llm_provider,
                    model=self.settings.llm_model,
                    prompt_version=PROMPT_VERSION,
                )
                session.add(run)
                session.flush()
                run_id = run.id
        except Exception:
            with self._lock:
                self._active.discard(incident_id)
            raise
        self._executor.submit(self._run_safely, incident_id, run_id)
        return run_id

    def recover(self) -> None:
        """Runs left 'running' by a previous process will never finish: mark them failed."""
        with session_scope() as session:
            for run in session.scalars(select(DiagnosisRun).where(DiagnosisRun.status == "running")):
                run.status = "failed"
                run.error = "interrupted by a backend restart"
                run.finished_at = utcnow()

    def stop(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_safely(self, incident_id: int, run_id: int) -> None:
        try:
            self._run(incident_id, run_id)
        except Exception as exc:
            log.exception("diagnosis run %s failed", run_id)
            with session_scope() as session:
                run = session.get(DiagnosisRun, run_id)
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
                run.finished_at = utcnow()
        finally:
            with self._lock:
                self._active.discard(incident_id)

    def _run(self, incident_id: int, run_id: int) -> None:
        with session_scope() as session:
            ctx = load_context(session, incident_id)
        evidence = EvidenceStore()
        api_client = new_api_client(self.settings.kube_context)
        tools = build_tools(
            ctx,
            evidence,
            metric_fn=self.prometheus.pod_metric,
            node_fn=lambda node: node_capacity(api_client, node),
        )
        calls = CallLogger(run_id, self.settings.llm_provider, self.settings.llm_model, PROMPT_VERSION)
        try:
            model = self.model_factory(self.settings)
        except Exception as exc:
            model = _Unavailable(exc)
        result = run_copilot(ctx, model, tools, evidence, calls, self.settings)

        draft = result.draft
        with session_scope() as session:
            run = session.get(DiagnosisRun, run_id)
            run.status = "completed"
            run.source = result.source
            run.category = draft.category
            run.summary = draft.summary
            run.confidence = draft.confidence
            run.findings = [f.model_dump() for f in draft.findings]
            run.ruled_out = [r.model_dump() for r in draft.ruled_out]
            run.recommendations = [r.model_dump() for r in draft.recommendations]
            run.validation_errors = result.validation_errors
            run.tool_calls = result.tool_calls
            run.error = result.llm_error
            run.finished_at = utcnow()
            for item in evidence.items:
                session.add(Evidence(run_id=run_id, ref=item.ref, tool=item.tool, args=item.args, result=item.result))
            incident = session.get(Incident, incident_id)
            if incident.status == "open":
                incident.status = "diagnosed"
        log.info("diagnosis run %s: %s (%s, %d tool calls)", run_id, draft.category, result.source, result.tool_calls)


class _Unavailable:
    """Stands in for a model that could not be created, so the run degrades to the deterministic fallback."""

    def __init__(self, exc: Exception):
        self.exc = exc

    def bind_tools(self, *_args, **_kwargs):
        return self

    def with_structured_output(self, *_args, **_kwargs):
        return self

    def invoke(self, *_args, **_kwargs):
        raise self.exc
