"""Profiler runtime: watches pods and events, and finalises each pod start once it becomes Ready.

Finalising = fetch the pod's events and startup spans, build the timeline, run detectors, compare against the
workload SLO/baseline and open an incident on breach. Everything is persisted so it survives event TTLs.
"""

import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from kubernetes import client
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from kubesage.collectors.jaeger import JaegerClient
from kubesage.collectors.kube import measure_clock_offset, new_api_client, to_dict, watch_namespaced
from kubesage.config import Settings
from kubesage.db.models import Incident, K8sEvent, PodStartup, Workload, utcnow
from kubesage.db.session import session_scope
from kubesage.llm.redact import redact_env, redact_text
from kubesage.profiler.baselines import evaluate
from kubesage.profiler.detectors import detect
from kubesage.profiler.timeline import Span, build_timeline, event_last_time, event_time, parse_time

log = logging.getLogger(__name__)

SPAN_FETCH_ATTEMPTS = 3
LOG_TAIL_LINES = 300


def workload_ref(pod: dict) -> tuple[str, str]:
    """(kind, name) of the controller that owns the pod. ReplicaSets resolve to their Deployment."""
    meta = pod["metadata"]
    owner = next((o for o in meta.get("ownerReferences", []) if o.get("controller")), None)
    if owner is None:
        return "Pod", meta["name"]
    template_hash = meta.get("labels", {}).get("pod-template-hash")
    if owner["kind"] == "ReplicaSet" and template_hash and owner["name"].endswith(f"-{template_hash}"):
        return "Deployment", owner["name"][: -len(template_hash) - 1]
    return owner["kind"], owner["name"]


def is_ready(pod: dict) -> bool:
    return any(
        c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", [])
    )


def service_name(pod: dict) -> str | None:
    container = pod["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    return env.get("OTEL_SERVICE_NAME") or pod["metadata"].get("labels", {}).get("app.kubernetes.io/name")


class ProfilerService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jaeger = JaegerClient(settings.jaeger_url)
        self.clock_offset_s: float | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="finalise")
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._local = threading.local()

    # --- lifecycle -------------------------------------------------------------------------------

    def start(self) -> None:
        try:
            self.clock_offset_s = measure_clock_offset(self._api(), self.settings.sandbox_namespace)
            if abs(self.clock_offset_s) > 1.0:
                log.warning("host and cluster clocks differ by %.1f s", self.clock_offset_s)
        except Exception:
            log.exception("clock offset check failed")
        # Unfinished startups from a previous run: the initial pod list re-creates those whose pods still exist.
        with session_scope() as session:
            session.execute(delete(PodStartup).where(PodStartup.finalized_at.is_(None)))
        for namespace in self.settings.monitored_namespaces:
            for list_method, handler, on_delete in (
                ("list_namespaced_event", self.on_event, None),
                ("list_namespaced_pod", self.on_pod, self.on_pod_deleted),
            ):
                thread = threading.Thread(
                    target=watch_namespaced,
                    args=(self.settings.kube_context, namespace, list_method, handler, self._stop, on_delete),
                    name=f"watch-{namespace}-{list_method}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def running(self) -> bool:
        return bool(self._threads) and all(t.is_alive() for t in self._threads)

    def _api(self) -> client.ApiClient:
        if not hasattr(self._local, "api"):
            self._local.api = new_api_client(self.settings.kube_context)
        return self._local.api

    # --- watch handlers --------------------------------------------------------------------------

    def on_event(self, event: dict) -> None:
        with session_scope() as session:
            upsert_event(session, event)

    def on_pod(self, pod: dict) -> None:
        uid = pod["metadata"]["uid"]
        with session_scope() as session:
            startup = session.scalar(select(PodStartup).where(PodStartup.pod_uid == uid))
            if startup is None:
                startup = self._new_startup(session, pod)
            if startup.finalized_at is not None or not is_ready(pod):
                return
        with self._lock:
            if uid in self._pending:
                return
            self._pending.add(uid)
        self._executor.submit(self._finalise_safely, uid, pod)

    def on_pod_deleted(self, pod: dict) -> None:
        """A pod deleted before it became Ready is an aborted start, not a startup sample.

        Limitation: pods that never become Ready (e.g. stuck Pending) are therefore not profiled yet.
        """
        uid = pod["metadata"]["uid"]
        with self._lock:
            if uid in self._pending:
                return  # finalisation already has the Ready snapshot
        with session_scope() as session:
            session.execute(delete(PodStartup).where(PodStartup.pod_uid == uid, PodStartup.finalized_at.is_(None)))

    def _new_startup(self, session: Session, pod: dict) -> PodStartup:
        meta = pod["metadata"]
        kind, name = workload_ref(pod)
        workload = session.scalar(
            select(Workload).where(
                Workload.namespace == meta["namespace"], Workload.kind == kind, Workload.name == name
            )
        )
        if workload is None:
            workload = Workload(
                namespace=meta["namespace"], kind=kind, name=name, slo_seconds=self.settings.default_slo_seconds
            )
            session.add(workload)
            session.flush()
        startup = PodStartup(
            workload_id=workload.id,
            namespace=meta["namespace"],
            pod_name=meta["name"],
            pod_uid=meta["uid"],
            node=pod["spec"].get("nodeName"),
            image=pod["spec"]["containers"][0]["image"],
            created_at=parse_time(meta["creationTimestamp"]),
            status="starting",
        )
        session.add(startup)
        session.flush()
        return startup

    # --- finalisation ----------------------------------------------------------------------------

    def _finalise_safely(self, uid: str, pod: dict) -> None:
        try:
            self.finalise(uid, pod)
        except Exception:
            log.exception("finalising pod %s failed", pod["metadata"]["name"])
        finally:
            with self._lock:
                self._pending.discard(uid)

    def finalise(self, uid: str, pod: dict) -> None:
        api = self._api()
        core = client.CoreV1Api(api)
        meta = pod["metadata"]
        # Logs first: they are complete at Ready, and the pod may be deleted during the span wait.
        logs = self._container_logs(core, pod)
        time.sleep(self.settings.span_wait_seconds)
        # Prefer a fresh read, but only while the pod is still Ready: if it was stopped during the span wait
        # it is terminating (Ready=False) or gone, and the Ready snapshot from the watch is the right record.
        try:
            fresh = to_dict(api, core.read_namespaced_pod(meta["name"], meta["namespace"]))
            if is_ready(fresh):
                pod = fresh
        except client.ApiException as exc:
            if exc.status != 404:
                raise
        events = [
            to_dict(api, e)
            for e in core.list_namespaced_event(meta["namespace"], field_selector=f"involvedObject.uid={uid}").items
        ]
        spans = self._startup_spans(pod)
        timeline = build_timeline(pod, events, spans)
        if timeline.ready_at is None:
            log.warning("pod %s has no Ready time; startup left unfinalised", meta["name"])
            return
        candidates = detect(timeline, self.settings)

        with session_scope() as session:
            for event in events:
                upsert_event(session, event)
            startup = session.scalar(select(PodStartup).where(PodStartup.pod_uid == uid))
            if startup is None:
                startup = self._new_startup(session, pod)
            startup.pod_snapshot = snapshot_pod(pod)
            startup.logs = logs
            startup.node = timeline.node
            startup.ready_at = timeline.ready_at
            startup.total_s = timeline.total_s
            startup.cold = timeline.cold
            startup.status = "ready"
            startup.spans_found = timeline.spans_found
            startup.phases = [p.to_dict() for p in timeline.phases]
            startup.signals = timeline.signals
            startup.candidates = [c.to_dict() for c in candidates]
            startup.top_category = candidates[0].category if candidates else None
            startup.finalized_at = utcnow()
            session.flush()
            self._maybe_open_incident(session, startup)
        log.info(
            "finalised %s/%s: %.1f s, cold=%s, top=%s",
            timeline.namespace,
            timeline.pod_name,
            timeline.total_s or -1,
            timeline.cold,
            candidates[0].category if candidates else None,
        )

    def _container_logs(self, core: client.CoreV1Api, pod: dict) -> str | None:
        """Last log lines of the main container, redacted. None if the pod is already gone."""
        meta = pod["metadata"]
        try:
            text = core.read_namespaced_pod_log(
                meta["name"],
                meta["namespace"],
                container=pod["spec"]["containers"][0]["name"],
                tail_lines=LOG_TAIL_LINES,
            )
        except client.ApiException as exc:
            log.info("no logs for %s: %s", meta["name"], exc.status)
            return None
        return redact_text(text)

    def _startup_spans(self, pod: dict) -> list[Span]:
        service = service_name(pod)
        if not service:
            return []
        created = parse_time(pod["metadata"]["creationTimestamp"])
        window = (created - timedelta(minutes=1), created + timedelta(minutes=30))
        spans: list[Span] = []
        for attempt in range(SPAN_FETCH_ATTEMPTS):
            try:
                spans = self.jaeger.startup_spans(service, pod["metadata"]["name"], *window)
            except Exception as exc:
                log.warning("jaeger query for %s failed: %s", service, exc)
            if any(s.name == "startup" for s in spans):
                break
            time.sleep(2 * (attempt + 1))
        return spans

    def _maybe_open_incident(self, session: Session, startup: PodStartup) -> None:
        if startup.namespace not in self.settings.incident_namespaces or startup.total_s is None:
            return
        previous = session.scalars(
            select(PodStartup.total_s)
            .where(
                PodStartup.workload_id == startup.workload_id,
                PodStartup.id != startup.id,
                PodStartup.status == "ready",
                PodStartup.cold.is_(None) if startup.cold is None else PodStartup.cold == startup.cold,
                PodStartup.created_at < startup.created_at,
            )
            .order_by(PodStartup.created_at.desc())
            .limit(self.settings.baseline_window)
        ).all()
        breach = evaluate(startup.total_s, startup.workload.slo_seconds, list(previous), self.settings)
        if breach is None:
            return
        if session.scalar(select(Incident).where(Incident.startup_id == startup.id)):
            return
        session.add(
            Incident(
                workload_id=startup.workload_id,
                startup_id=startup.id,
                total_s=startup.total_s,
                threshold_s=breach.threshold_s,
                breach_reason=breach.reason,
                baseline_s=breach.baseline_s,
                top_category=startup.top_category,
                candidates=startup.candidates,
            )
        )


def snapshot_pod(pod: dict) -> dict:
    """Pod spec and status for later investigation, without managedFields noise and with env values redacted."""
    spec = copy.deepcopy(pod["spec"])
    for container in spec.get("containers", []) + spec.get("initContainers", []):
        container["env"] = redact_env(container.get("env", []))
    metadata = {k: v for k, v in pod["metadata"].items() if k != "managedFields"}
    return {"metadata": metadata, "spec": spec, "status": pod.get("status", {})}


def upsert_event(session: Session, event: dict) -> None:
    """Insert or refresh an event. ON CONFLICT because the event watcher and the finaliser can race."""
    meta = event["metadata"]
    involved = event.get("involvedObject", {})
    values = {
        "uid": meta["uid"],
        "namespace": meta["namespace"],
        "involved_kind": involved.get("kind"),
        "involved_name": involved.get("name"),
        "involved_uid": involved.get("uid"),
        "reason": event.get("reason"),
        "type": event.get("type"),
        "message": event.get("message"),
        "count": event.get("count") or 1,
        "first_seen": event_time(event),
        "last_seen": event_last_time(event),
        "raw": event,
    }
    statement = pg_insert(K8sEvent).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[K8sEvent.uid],
            set_={k: statement.excluded[k] for k in values if k != "uid"},
        )
    )
