"""Runs an approved plan as a controlled before/after experiment in the sandbox namespace.

Protocol: one discarded warm-up trial, then alternating baseline/candidate trials (ABAB...), so drift affects both
arms alike. Before every trial the images are evicted from the sandbox node (true cold start for both arms).
Each trial creates the Deployment, waits for Ready, takes the startup measured by the profiler, runs guardrails
(Ready, smoke request, no restarts), then deletes it. Everything is cleaned up by label at the end.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from kubernetes import client
from sqlalchemy import select

from kubesage.audit import audit
from kubesage.collectors.kube import new_api_client, to_dict
from kubesage.config import Settings
from kubesage.db.models import ActionPlan, Experiment, PodStartup, Trial, utcnow
from kubesage.db.session import session_scope
from kubesage.experiments import k8s
from kubesage.experiments.compare import compare
from kubesage.experiments.lab import evict_images
from kubesage.experiments.sandbox import LABEL_CLONE, LABEL_MANAGED, LABEL_PLAN, SANDBOX_POOL_LABEL
from kubesage.optimizer.catalog import ACTIONS, image_repository
from kubesage.optimizer.registry import split_repository

log = logging.getLogger(__name__)

FATAL_WAIT_REASONS = {
    "CrashLoopBackOff",
    "CreateContainerConfigError",
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
}
STARTUP_RECORD_TIMEOUT_S = 120


class Aborted(Exception):
    pass


class ExperimentInProgress(RuntimeError):
    pass


class ExperimentService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="experiment")
        self._lock = threading.Lock()
        self._running: int | None = None
        self._abort = threading.Event()

    # --- control -------------------------------------------------------------------------------

    def is_running(self) -> bool:
        with self._lock:
            return self._running is not None

    def start(self, plan_id: int) -> int:
        with self._lock:
            if self._running is not None:
                raise ExperimentInProgress(f"experiment {self._running} is running; one at a time")
            with session_scope() as session:
                plan = session.get(ActionPlan, plan_id)
                experiment = Experiment(plan_id=plan_id, trials_per_arm=ACTIONS[plan.action].trials_per_arm)
                session.add(experiment)
                session.flush()
                experiment_id = experiment.id
            self._running = experiment_id
            self._abort.clear()
        audit("experiment.started", f"experiment {experiment_id}", plan_id=plan_id)
        self._executor.submit(self._run_safely, experiment_id)
        return experiment_id

    def abort(self, experiment_id: int, actor: str) -> bool:
        with self._lock:
            if self._running != experiment_id:
                return False
            self._abort.set()
        audit("experiment.abort_requested", f"experiment {experiment_id}", actor=actor)
        return True

    def recover(self) -> None:
        """After a restart: runs marked running cannot continue. Mark them failed and remove their sandbox objects."""
        with session_scope() as session:
            for experiment in session.scalars(select(Experiment).where(Experiment.status == "running")):
                experiment.status, experiment.error = "failed", "interrupted by a backend restart"
                experiment.finished_at = utcnow()
                experiment.plan.status = "failed"
            for plan in session.scalars(select(ActionPlan).where(ActionPlan.status == "proposing")):
                plan.status = "failed"
        try:
            k8s.cleanup(new_api_client(self.settings.kube_context), self.settings.sandbox_namespace)
        except Exception:
            log.exception("sandbox cleanup on startup failed")

    def stop(self) -> None:
        self._abort.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # --- run -----------------------------------------------------------------------------------

    def _run_safely(self, experiment_id: int) -> None:
        try:
            self._run(experiment_id)
        finally:
            with self._lock:
                self._running = None

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise Aborted()

    def _run(self, experiment_id: int) -> None:
        settings, ns = self.settings, self.settings.sandbox_namespace
        with session_scope() as session:
            experiment = session.get(Experiment, experiment_id)
            plan = experiment.plan
            plan_id, manifests, n = plan.id, plan.manifests, experiment.trials_per_arm
        api = new_api_client(settings.kube_context)
        label = str(plan_id)
        status, error, verdict, comparison, prep = "failed", None, "failed", {}, {}
        try:
            node = self._sandbox_node(api)
            self._update(experiment_id, node=node)
            k8s.cleanup(api, ns, label)  # leftovers of an earlier attempt
            self._copy_configmaps(api, manifests, label)
            prep = self._one_off_objects(api, manifests["objects"])
            self._update(experiment_id, prep=prep)
            paths = self._repository_paths(manifests)

            sequence = [("baseline", True)] + [
                arm for _ in range(n) for arm in (("baseline", False), ("candidate", False))
            ]
            for index, (arm, warmup) in enumerate(sequence):
                self._check_abort()
                result = self._trial(api, node, paths, manifests, arm)
                with session_scope() as session:
                    session.add(Trial(experiment_id=experiment_id, index=index, arm=arm, warmup=warmup, **result))
            with session_scope() as session:
                trials = session.get(Experiment, experiment_id).trials
                rows = [
                    {
                        "arm": t.arm,
                        "warmup": t.warmup,
                        "status": t.status,
                        "total_s": t.total_s,
                        "phases": t.phases,
                        "prep_s": t.prep_s,
                    }
                    for t in trials
                ]
            comparison = compare(rows)
            verdict, status = comparison["verdict"], "completed"
        except Aborted:
            status, error = "aborted", "aborted by request"
        except Exception as exc:
            log.exception("experiment %s failed", experiment_id)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                k8s.cleanup(api, ns, label)
            except Exception as exc:
                log.exception("cleanup of experiment %s failed", experiment_id)
                error = (error + "; " if error else "") + f"cleanup failed: {exc}"
            with session_scope() as session:
                experiment = session.get(Experiment, experiment_id)
                experiment.status, experiment.error = status, error
                experiment.verdict = verdict if status == "completed" else None
                experiment.comparison, experiment.finished_at = comparison, utcnow()
                experiment.plan.status = status
            audit(f"experiment.{status}", f"experiment {experiment_id}", verdict=verdict, error=error)

    def _update(self, experiment_id: int, **fields) -> None:
        with session_scope() as session:
            experiment = session.get(Experiment, experiment_id)
            for key, value in fields.items():
                setattr(experiment, key, value)

    def _sandbox_node(self, api: client.ApiClient) -> str:
        nodes = client.CoreV1Api(api).list_node(label_selector=f"{SANDBOX_POOL_LABEL}=sandbox").items
        if not nodes:
            raise RuntimeError(f"no node labelled {SANDBOX_POOL_LABEL}=sandbox")
        return nodes[0].metadata.name

    def _repository_paths(self, manifests: dict) -> list[str]:
        """Image repositories (registry paths) used by either arm: both are evicted before every trial."""
        images = {
            c["image"]
            for arm in ("baseline", "candidate")
            for c in manifests[arm]["spec"]["template"]["spec"]["containers"]
        }
        paths = {split_repository(image_repository(i), self.settings.registry_hosts) for i in images}
        if None in paths:
            raise RuntimeError(f"cold-start trials need images from the lab registry, got {sorted(images)}")
        return sorted(paths)

    def _copy_configmaps(self, api: client.ApiClient, manifests: dict, label: str) -> None:
        source_ns = manifests["baseline"]["metadata"]["annotations"]["kubesage.io/source"].split("/")[0]
        core = client.CoreV1Api(api)
        for name in manifests["configmaps"]:
            source = to_dict(api, core.read_namespaced_config_map(name, source_ns))
            k8s.create(
                api,
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": name,
                        "namespace": self.settings.sandbox_namespace,
                        "labels": {LABEL_PLAN: label, LABEL_MANAGED: "kubesage"},
                    },
                    "data": source.get("data", {}),
                    "binaryData": source.get("binaryData", {}),
                },
            )

    def _one_off_objects(self, api: client.ApiClient, objects: list[dict]) -> dict:
        """Create objects needed by the candidate arm once (PVC, prefetch Job) and time any Job."""
        prep = {}
        for obj in objects:
            k8s.create(api, obj)
            if obj["kind"] == "Job":
                prep[f"{obj['metadata']['name']}_s"] = self._wait_job(api, obj["metadata"]["name"])
        return prep

    def _wait_job(self, api: client.ApiClient, name: str) -> float:
        batch = client.BatchV1Api(api)
        deadline = time.monotonic() + self.settings.trial_timeout_s
        while True:
            self._check_abort()
            job = batch.read_namespaced_job(name, self.settings.sandbox_namespace).status
            if job.succeeded:
                return round((job.completion_time - job.start_time).total_seconds(), 1)
            if job.failed and job.failed > 1:  # backoffLimit 1 -> two attempts
                raise RuntimeError(f"job {name} failed")
            if time.monotonic() > deadline:
                raise TimeoutError(f"job {name} did not finish")
            time.sleep(2)

    def _prepull(self, api: client.ApiClient, daemonset: dict) -> float:
        """Run the pre-pull DaemonSet until its pods are ready on the node, then remove it. Returns seconds."""
        apps, ns, name = client.AppsV1Api(api), self.settings.sandbox_namespace, daemonset["metadata"]["name"]
        started = time.monotonic()
        k8s.create(api, daemonset)
        while True:
            self._check_abort()
            status = apps.read_namespaced_daemon_set(name, ns).status
            if status.desired_number_scheduled and status.number_ready == status.desired_number_scheduled:
                break
            if time.monotonic() - started > self.settings.trial_timeout_s:
                raise TimeoutError(f"pre-pull {name} not ready")
            time.sleep(1)
        elapsed = round(time.monotonic() - started, 1)
        k8s.delete_object(api, "DaemonSet", ns, name)
        selector = ",".join(f"{k}={v}" for k, v in daemonset["spec"]["selector"]["matchLabels"].items())
        k8s.wait_no_pods(api, ns, selector, timeout_s=120)
        return elapsed

    def _trial(self, api: client.ApiClient, node: str, paths: list[str], manifests: dict, arm: str) -> dict:
        ns = self.settings.sandbox_namespace
        deployment = manifests[arm]
        name = deployment["metadata"]["name"]
        selector = f"{LABEL_CLONE}={deployment['spec']['template']['metadata']['labels'][LABEL_CLONE]}"
        evict_images(node, paths)
        prep_s = None
        if arm == "candidate":
            for daemonset in manifests["per_trial_objects"]:
                prep_s = self._prepull(api, daemonset)
        result = {"status": "failed", "prep_s": prep_s, "guardrails": {}, "phases": {}}
        k8s.create(api, deployment)
        try:
            pod = self._wait_ready(api, selector)
            result["pod_name"] = pod.metadata.name
            startup = self._wait_startup(pod.metadata.uid)
            restarts = sum(s.restart_count for s in pod.status.container_statuses or [])
            smoke_ok, smoke_detail = self._smoke(api, pod)
            result.update(
                startup_id=startup.id,
                total_s=startup.total_s,
                phases={p["name"]: p["duration_s"] for p in startup.phases if p["parent"] is None},
                guardrails={"ready": True, "restarts": restarts, "smoke_ok": smoke_ok, "smoke": smoke_detail},
                status="ok" if smoke_ok and restarts == 0 else "failed",
            )
        except Aborted:
            raise
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            k8s.delete_object(api, "Deployment", ns, name)
            k8s.wait_no_pods(api, ns, selector, timeout_s=180)
        return result

    def _wait_ready(self, api: client.ApiClient, selector: str):
        core, ns = client.CoreV1Api(api), self.settings.sandbox_namespace
        deadline = time.monotonic() + self.settings.trial_timeout_s
        while True:
            self._check_abort()
            pods = [
                p
                for p in core.list_namespaced_pod(ns, label_selector=selector).items
                if not p.metadata.deletion_timestamp
            ]
            for pod in pods:
                if any(c.type == "Ready" and c.status == "True" for c in pod.status.conditions or []):
                    return pod
                for status in pod.status.container_statuses or []:
                    reason = status.state.waiting.reason if status.state and status.state.waiting else None
                    if reason in FATAL_WAIT_REASONS:
                        raise RuntimeError(f"pod {pod.metadata.name}: {reason}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"no Ready pod after {self.settings.trial_timeout_s:.0f} s")
            time.sleep(1)

    def _wait_startup(self, pod_uid: str) -> PodStartup:
        """The profiler records every sandbox pod start; wait until it has finalised this one."""
        deadline = time.monotonic() + STARTUP_RECORD_TIMEOUT_S
        while True:
            self._check_abort()
            with session_scope() as session:
                startup = session.scalar(
                    select(PodStartup).where(PodStartup.pod_uid == pod_uid, PodStartup.finalized_at.is_not(None))
                )
                if startup is not None:
                    session.expunge(startup)
                    return startup
            if time.monotonic() > deadline:
                raise TimeoutError("the profiler did not record this startup (are the watchers running?)")
            time.sleep(2)

    def _smoke(self, api: client.ApiClient, pod) -> tuple[bool, str]:
        container = pod.spec.containers[0]
        port = container.ports[0].container_port if container.ports else 8080
        try:
            body = client.CoreV1Api(api).connect_get_namespaced_pod_proxy_with_path(
                f"{pod.metadata.name}:{port}", self.settings.sandbox_namespace, self.settings.smoke_path
            )
            return True, str(body)[:200]
        except client.ApiException as exc:
            return False, f"{exc.status} {exc.reason}"
