"""Fault-injection campaign driver (RQ1 profiling/detection, RQ4 adversarial rows).

    python -m evals.campaign generate rq1            # write evals/campaigns/rq1.yaml: the rows that will run
    python -m evals.campaign run rq1 --dry-run       # build every manifest and server-side dry-run it
    python -m evals.campaign run rq1 --smoke         # one row per fault configuration
    python -m evals.campaign run rq1                 # everything; resumable, appends to evals/data/rq1.jsonl
    python -m evals.campaign summary rq1

Needs the lab cluster, Postgres and the KubeSage backend running: the backend's watchers profile every pod this
driver starts, and the driver reads the result from the database. The driver plays the operator who breaks a
workload on purpose, so it uses the kubeconfig identity (cluster-admin in the lab) and `docker exec` on the
registry proxy and the kind node; KubeSage itself never does (see kubesage/collectors/kube.py::new_admin_client).

Each row: remove the previous workload, set the registry bandwidth, evict (cold) or pull (warm) the demo images on the
workload node, restart the dependency if needed, create the `eval-svc` Deployment, wait for Ready, wait for the
profiler to finalise the startup, then store what it measured next to the expected ground truth.
"""

import argparse
import ctypes
import json
import random
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml
from kubernetes import client
from kubernetes.client.rest import ApiException
from sqlalchemy import select

from evals.expectations import DEFAULT_RATE_MBPS, SLO_S, evaluate_row, rq1_rows, rq4_rows
from kubesage.audit import audit
from kubesage.collectors.kube import new_admin_client, node_capacity
from kubesage.collectors.prometheus import TEMPLATES, PrometheusClient
from kubesage.config import REPO_ROOT, get_settings
from kubesage.db.models import Incident, PodStartup, Workload
from kubesage.db.session import init_db, session_scope
from kubesage.experiments.lab import ensure_image, evict_images

HERE = Path(__file__).parent
CAMPAIGNS = HERE / "campaigns"
DATA = HERE / "data"
BUILDERS = {"rq1": rq1_rows, "rq4": rq4_rows}

NS, INFRA, NAME = "demo", "kubesage-infra", "eval-svc"
SELECTOR = f"app.kubernetes.io/name={NAME}"
PROXY = "kubesage-registry-proxy"
IMAGE_PATHS = ["demo-ai-service"]
BASE_MANIFEST = REPO_ROOT / "deploy" / "scenarios" / "s1-large-image" / "deployment.yaml"
HOG_MANIFEST = REPO_ROOT / "deploy" / "scenarios" / "s5-capacity" / "hog.yaml"
MODEL_FILES = {100: "weights-100m.bin", 250: "weights.bin", 500: "weights-500m.bin"}
FATAL_WAITING = {
    "CrashLoopBackOff",
    "CreateContainerConfigError",
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
}
SHUFFLE_SEED = 20261109


# --- manifests --------------------------------------------------------------------------------------------------


def build_manifest(row: dict) -> dict:
    """The eval-svc Deployment for one row: the S1 scenario manifest with the row's knobs applied."""
    k = row["knobs"]
    m = yaml.safe_load(BASE_MANIFEST.read_text(encoding="utf-8"))
    labels = {"app.kubernetes.io/name": NAME, "kubesage.io/scenario": "eval"}
    m["metadata"].update(name=NAME, labels=dict(labels))
    m["spec"]["selector"]["matchLabels"] = {"app.kubernetes.io/name": NAME}
    template = m["spec"]["template"]
    template["metadata"]["labels"] = {**labels, "kubesage.io/campaign-row": row["id"]}
    pod = template["spec"]
    container = pod["containers"][0]
    container["image"] = f"localhost:5001/demo-ai-service:{k.get('image', 'slim')}"
    env = container["env"]
    for item in env:
        if item["name"] == "OTEL_SERVICE_NAME":
            item["value"] = NAME
    if "model_mb" in k:
        url = f"http://model-hub.kubesage-infra.svc.cluster.local/{MODEL_FILES[k['model_mb']]}"
        env += [{"name": "MODEL_URL", "value": url}, {"name": "MODEL_DIR", "value": "/work/model"}]
        container["volumeMounts"] = [{"name": "work", "mountPath": "/work"}]
        pod["volumes"] = [{"name": "work", "emptyDir": {"sizeLimit": "1Gi"}}]
    if "cpu_work" in k:
        env.append({"name": "INIT_CPU_WORK", "value": str(k["cpu_work"])})
    if "dependency_delay_s" in k:
        env.append({"name": "DEPENDENCY_URL", "value": "http://slow-dependency.kubesage-infra.svc.cluster.local/"})
    if k.get("log_note"):
        env.append({"name": "STARTUP_LOG_NOTE", "value": k["log_note"]})
    for name, value in (k.get("secret_env") or {}).items():
        env.append({"name": name, "value": value})
    probe = k.get("probe", {"initial_delay": 0, "period": 1})
    container["readinessProbe"].update(initialDelaySeconds=probe["initial_delay"], periodSeconds=probe["period"])
    hog = "hog_release_s" in k
    container["resources"] = {
        "requests": {"cpu": "3" if hog else "100m", "memory": "128Mi"},
        "limits": {"cpu": "3" if hog else ("250m" if "cpu_work" in k else "1"), "memory": "1Gi"},
    }
    return m


# --- lab operations -----------------------------------------------------------------------------------------------


def set_registry_rate(mbps: float) -> None:
    """Cap the lab registry path with a token bucket on the proxy's egress (aggregate, not per connection)."""
    mbit = round(mbps * 8)
    subprocess.run(
        ["docker", "exec", PROXY, "tc", "qdisc", "replace", "dev", "eth0", "root", "tbf",
         "rate", f"{mbit}mbit", "burst", "512kb", "latency", "500ms"],
        check=True, capture_output=True, timeout=30,
    )  # fmt: skip
    audit("lab.registry_rate", PROXY, mbit=mbit)


def keep_awake(on: bool) -> None:
    """Windows Modern Standby pauses the cluster (a sleeping laptop failed a trial once); ask Windows not to sleep."""
    if sys.platform == "win32":
        continuous, system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(continuous | (system_required if on else 0))


def ignore_missing(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except ApiException as exc:
        if exc.status != 404:
            raise
        return None


def wait_pods_gone(core: client.CoreV1Api, namespace: str, selector: str, timeout_s: float = 180) -> None:
    deadline = time.monotonic() + timeout_s
    while core.list_namespaced_pod(namespace, label_selector=selector).items:
        if time.monotonic() > deadline:
            raise TimeoutError(f"pods {selector} in {namespace} still present after {timeout_s:.0f} s")
        time.sleep(1)


def teardown(api: client.ApiClient) -> None:
    apps, core = client.AppsV1Api(api), client.CoreV1Api(api)
    ignore_missing(apps.delete_namespaced_deployment, NAME, NS, propagation_policy="Foreground")
    ignore_missing(apps.delete_namespaced_deployment, "capacity-hog", INFRA, propagation_policy="Foreground")
    wait_pods_gone(core, NS, SELECTOR)
    wait_pods_gone(core, INFRA, "app.kubernetes.io/name=capacity-hog")


def stop_other_scenarios(api: client.ApiClient) -> None:
    """Images can only be evicted while no running container uses them: stop the other demo scenarios."""
    apps, core = client.AppsV1Api(api), client.CoreV1Api(api)
    for deployment in apps.list_namespaced_deployment(NS, label_selector="kubesage.io/scenario!=eval").items:
        if deployment.spec.replicas:
            scale = apps.read_namespaced_deployment_scale(deployment.metadata.name, NS)
            scale.spec.replicas = 0
            apps.replace_namespaced_deployment_scale(deployment.metadata.name, NS, scale)
    wait_pods_gone(core, NS, "kubesage.io/scenario,kubesage.io/scenario!=eval")


def workload_node(api: client.ApiClient) -> str:
    nodes = client.CoreV1Api(api).list_node(label_selector="kubesage.io/pool=workload").items
    if not nodes:
        raise RuntimeError("no node labelled kubesage.io/pool=workload")
    return nodes[0].metadata.name


def start_hog(api: client.ApiClient) -> None:
    core = client.CoreV1Api(api)
    client.AppsV1Api(api).create_namespaced_deployment(INFRA, yaml.safe_load(HOG_MANIFEST.read_text(encoding="utf-8")))
    deadline = time.monotonic() + 90
    while not any(
        p.status.phase == "Running"
        for p in core.list_namespaced_pod(INFRA, label_selector="app.kubernetes.io/name=capacity-hog").items
    ):
        if time.monotonic() > deadline:
            raise TimeoutError("capacity hog did not start")
        time.sleep(1)
    audit("lab.capacity_hog", "start")


def release_hog(api: client.ApiClient) -> None:
    ignore_missing(
        client.AppsV1Api(api).delete_namespaced_deployment, "capacity-hog", INFRA, propagation_policy="Background"
    )
    audit("lab.capacity_hog", "release")


def restart_dependency(api: client.ApiClient, delay_s: int) -> str:
    """Restart the slow dependency with a new delay; return when it starts answering (its own start + delay)."""
    apps, core = client.AppsV1Api(api), client.CoreV1Api(api)
    deployment = apps.read_namespaced_deployment("slow-dependency", INFRA)
    stamp = datetime.now(UTC).isoformat()
    template = deployment.spec.template
    template.metadata.annotations = {**(template.metadata.annotations or {}), "kubesage.io/restarted-at": stamp}
    for item in template.spec.containers[0].env:
        if item.name == "DEP_DELAY":
            item.value = str(delay_s)
    apps.replace_namespaced_deployment("slow-dependency", INFRA, deployment)
    deadline = time.monotonic() + 120
    while True:
        for pod in core.list_namespaced_pod(INFRA, label_selector="app.kubernetes.io/name=slow-dependency").items:
            running = pod.status.container_statuses and pod.status.container_statuses[0].state.running
            if (
                not pod.metadata.deletion_timestamp
                and (pod.metadata.annotations or {}).get("kubesage.io/restarted-at") == stamp
                and running
            ):
                audit("lab.dependency_restart", "slow-dependency", delay_s=delay_s)
                return (running.started_at + timedelta(seconds=delay_s)).isoformat()
        if time.monotonic() > deadline:
            raise TimeoutError("slow dependency did not restart")
        time.sleep(0.5)


# --- one row ----------------------------------------------------------------------------------------------------


def pod_ready(pod) -> bool:
    return any(c.type == "Ready" and c.status == "True" for c in pod.status.conditions or [])


def wait_ready(api: client.ApiClient, timeout_s: float, on_tick=None):
    """(Ready pod, monotonic time at which the driver saw it Ready)."""
    core = client.CoreV1Api(api)
    deadline = time.monotonic() + timeout_s
    while True:
        for pod in core.list_namespaced_pod(NS, label_selector=SELECTOR).items:
            if pod.metadata.deletion_timestamp:
                continue
            if pod_ready(pod):
                return pod, time.monotonic()
            for status in pod.status.container_statuses or []:
                reason = status.state.waiting.reason if status.state and status.state.waiting else None
                if reason in FATAL_WAITING:
                    raise RuntimeError(f"pod {pod.metadata.name}: {reason}")
        if on_tick:
            on_tick()
        if time.monotonic() > deadline:
            raise TimeoutError(f"no Ready pod after {timeout_s:.0f} s")
        time.sleep(0.5)


def wait_finalised(uid: str, timeout_s: float = 150) -> dict:
    """Read the profiler's finalised record of this pod start from the database."""
    deadline = time.monotonic() + timeout_s
    while True:
        with session_scope() as session:
            startup = session.scalar(
                select(PodStartup).where(PodStartup.pod_uid == uid, PodStartup.finalized_at.is_not(None))
            )
            if startup is not None:
                incident_id = session.scalar(select(Incident.id).where(Incident.startup_id == startup.id))
                return {
                    "startup_id": startup.id,
                    "incident_id": incident_id,
                    "total_s": startup.total_s,
                    "cold_detected": startup.cold,
                    "top_category": startup.top_category,
                    "candidates": startup.candidates,
                    "signals": startup.signals,
                    "phases": startup.phases,
                    "finalised_at": startup.finalized_at.isoformat(),
                }
        if time.monotonic() > deadline:
            raise TimeoutError("the profiler did not finalise this startup (is the backend running?)")
        time.sleep(1)


def snapshot_tools(api: client.ApiClient, record: dict) -> dict:
    """Freeze what the Copilot's live tools would return for this startup (Prometheus keeps only 2 days, and node
    capacity is a current-state view), so every system in RQ2 sees identical evidence however late it is evaluated."""
    prometheus = PrometheusClient(get_settings().prometheus_url)
    created = datetime.fromisoformat(record["started_at"])
    ready = datetime.fromisoformat(record["observed_ready_at"])
    end = min(ready + timedelta(seconds=30), datetime.now(UTC))
    snapshot: dict = {}
    for metric in TEMPLATES:
        try:
            snapshot[metric] = prometheus.pod_metric(metric, NS, record["pod"], created, end)
        except Exception as exc:
            snapshot[metric] = {"error": f"query failed: {exc}"}
    try:
        snapshot["node_capacity"] = node_capacity(api, record["node"])
    except Exception as exc:
        snapshot["node_capacity"] = {"error": f"lookup failed: {exc}"}
    return snapshot


def set_slo(slo_s: float) -> None:
    with session_scope() as session:
        workload = session.scalar(
            select(Workload).where(Workload.namespace == NS, Workload.kind == "Deployment", Workload.name == NAME)
        )
        if workload is None:
            session.add(Workload(namespace=NS, kind="Deployment", name=NAME, slo_seconds=slo_s))
        else:
            workload.slo_seconds = slo_s


def run_row(api: client.ApiClient, node: str, row: dict, keep: bool = False) -> dict:
    """Run one row; returns {row, record, outcome, status, error}. Never raises (a failed row is data).

    `keep` leaves the eval-svc Deployment in place afterwards (RQ3 needs it to propose a fix for the incident).
    """
    knobs = row["knobs"]
    record: dict = {"started_at": datetime.now(UTC).isoformat()}
    status, error = "failed", None
    apps = client.AppsV1Api(api)
    hog_active = False
    try:
        teardown(api)
        set_registry_rate(knobs.get("registry_mbps", DEFAULT_RATE_MBPS))
        manifest = build_manifest(row)
        if row["cold"]:
            record["evicted"] = evict_images(node, IMAGE_PATHS)
        else:
            record["pulled"] = ensure_image(node, manifest["spec"]["template"]["spec"]["containers"][0]["image"])
        if "dependency_delay_s" in knobs:
            record["dep_answer_at"] = restart_dependency(api, knobs["dependency_delay_s"])
        if "hog_release_s" in knobs:
            start_hog(api)
            hog_active = True
        created = time.monotonic()
        apps.create_namespaced_deployment(NS, manifest)

        def tick() -> None:
            nonlocal hog_active
            if hog_active and time.monotonic() - created >= knobs["hog_release_s"]:
                release_hog(api)
                hog_active = False

        timeout_s = row["expect"].get("est_total_s", 60) * 2 + 180
        pod, seen = wait_ready(api, timeout_s, tick)
        observed = datetime.now(UTC)
        record.update(
            pod=pod.metadata.name,
            pod_uid=pod.metadata.uid,
            node=pod.spec.node_name,
            observed_total_s=round(seen - created, 2),
            observed_ready_at=observed.isoformat(),
        )
        record.update(wait_finalised(pod.metadata.uid))
        record["finalise_lag_s"] = round((datetime.fromisoformat(record["finalised_at"]) - observed).total_seconds(), 2)
        record["tool_snapshots"] = snapshot_tools(api, record)
        status = "ok"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if not keep:
                teardown(api)
            elif hog_active:
                release_hog(api)
        except Exception as exc:
            error = (error + "; " if error else "") + f"teardown failed: {exc}"
    outcome = evaluate_row(row, record) if status == "ok" else None
    return {"row": row, "status": status, "error": error, "record": record, "outcome": outcome}


# --- campaign ---------------------------------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if path.exists()
        else []
    )


def environment(api: client.ApiClient) -> dict:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False).stdout.strip()

    return {
        "date": datetime.now(UTC).isoformat(),
        "git_commit": git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
        "python": sys.version.split()[0],
        "kubernetes": client.VersionApi(api).get_code().git_version,
        "slo_s": SLO_S,
        "span_wait_seconds": get_settings().span_wait_seconds,
    }


def preflight(api_url: str) -> None:
    health = httpx.get(f"{api_url}/api/health", timeout=15).json()
    if not health.get("watchers_running"):
        raise SystemExit(f"backend at {api_url} is not running its watchers: {health}")
    if not health.get("jaeger"):
        raise SystemExit("Jaeger is not reachable; startup spans would be missing")


def load_rows(name: str) -> list[dict]:
    return yaml.safe_load((CAMPAIGNS / f"{name}.yaml").read_text(encoding="utf-8"))["rows"]


def select_rows(rows: list[dict], args, done: set[str]) -> list[dict]:
    rows = list(rows)
    random.Random(SHUFFLE_SEED).shuffle(rows)  # fixed order: drift is spread across faults, and reruns are comparable
    if args.smoke:
        seen: set[str] = set()
        rows = [r for r in rows if r["repeat"] == 1 and not (r["fault"] in seen or seen.add(r["fault"]))]
    if args.only:
        rows = [r for r in rows if r["id"] in args.only]
    rows = [r for r in rows if args.force or r["id"] not in done]
    return rows[: args.limit] if args.limit else rows


def cmd_generate(args) -> None:
    rows = [r.to_dict() for r in BUILDERS[args.name]()]
    CAMPAIGNS.mkdir(exist_ok=True)
    path = CAMPAIGNS / f"{args.name}.yaml"
    path.write_text(yaml.safe_dump({"campaign": args.name, "rows": rows}, sort_keys=False, width=120), encoding="utf-8")
    print(f"{len(rows)} rows -> {path}")


def cmd_run(args) -> None:
    init_db()
    api = new_admin_client(get_settings().kube_context)
    out = DATA / f"{args.name}.jsonl"
    done = {r["row"]["id"] for r in read_jsonl(out) if r["status"] == "ok"}
    todo = select_rows(load_rows(args.name), args, done)
    print(f"{len(todo)} row(s) to run ({len(done)} already done)")

    if args.dry_run:
        apps = client.AppsV1Api(api)
        for row in todo:
            try:
                apps.create_namespaced_deployment(NS, build_manifest(row), dry_run="All")
                state = "accepted"
            except ApiException as exc:
                state = "accepted (already exists)" if exc.status == 409 else f"REJECTED {exc.status} {exc.reason}"
            print(f"  {row['id']:<46} {state}")
        return

    preflight(args.api)
    DATA.mkdir(exist_ok=True)
    meta = DATA / f"{args.name}.meta.json"
    meta.write_text(json.dumps(environment(api), indent=1), encoding="utf-8")
    node = workload_node(api)
    set_slo(SLO_S)
    stop_other_scenarios(api)
    keep_awake(True)
    audit("campaign.started", args.name, rows=len(todo))
    try:
        for i, row in enumerate(todo, 1):
            started = time.monotonic()
            result = run_row(api, node, row)
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(result, default=str) + "\n")
            rec, outcome = result["record"], result["outcome"] or {}
            print(
                f"[{i}/{len(todo)}] {row['id']:<46} {result['status']:<6} total={rec.get('total_s')} "
                f"top={rec.get('top_category')} correct={outcome.get('top1_correct')} "
                f"({time.monotonic() - started:.0f}s) {result['error'] or ''}",
                flush=True,
            )
    finally:
        keep_awake(False)
        try:
            set_registry_rate(DEFAULT_RATE_MBPS)
            teardown(api)
        finally:
            audit("campaign.finished", args.name)


def cmd_summary(args) -> None:
    results = read_jsonl(DATA / f"{args.name}.jsonl")
    latest: dict[str, dict] = {}
    for r in results:  # a later attempt of the same row replaces an earlier one
        latest[r["row"]["id"]] = r
    groups: dict[str, list[dict]] = {}
    for r in latest.values():
        groups.setdefault(r["row"]["group"], []).append(r)
    print(f"{'group':<18}{'rows':>5}{'ok':>5}{'top1':>9}{'incident':>10}{'in-range':>10}")
    for group, items in sorted(groups.items()):
        ok = [r for r in items if r["status"] == "ok"]
        scored = [r["outcome"]["top1_correct"] for r in ok if r["outcome"]["top1_correct"] is not None]
        incidents = [
            r["outcome"]["incident_raised"] == r["outcome"]["incident_expected"]
            for r in ok
            if r["outcome"]["incident_expected"] is not None
        ]
        checks = [c["inside"] for r in ok for c in r["outcome"]["interval_checks"]]
        print(
            f"{group:<18}{len(items):>5}{len(ok):>5}"
            f"{(f'{sum(scored)}/{len(scored)}' if scored else '-'):>9}"
            f"{(f'{sum(incidents)}/{len(incidents)}' if incidents else '-'):>10}"
            f"{(f'{sum(checks)}/{len(checks)}' if checks else '-'):>10}"
        )
    for r in latest.values():
        if r["status"] != "ok":
            print(f"FAILED {r['row']['id']}: {r['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("generate", cmd_generate), ("run", cmd_run), ("summary", cmd_summary)):
        p = sub.add_parser(name)
        p.add_argument("name", choices=sorted(BUILDERS))
        p.set_defaults(fn=fn)
        if name == "run":
            p.add_argument("--dry-run", action="store_true", help="server-side dry-run every manifest; change nothing")
            p.add_argument("--smoke", action="store_true", help="one row per fault configuration")
            p.add_argument("--only", nargs="+", help="run only these row ids")
            p.add_argument("--limit", type=int, help="run at most this many rows")
            p.add_argument("--force", action="store_true", help="rerun rows that already succeeded")
            p.add_argument("--api", default="http://127.0.0.1:8000", help="KubeSage backend URL")
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
