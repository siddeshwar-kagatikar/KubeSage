"""Create, clean up and wait on the sandbox objects an experiment uses. Every write is audited."""

import time

from kubernetes import client

from kubesage.audit import audit
from kubesage.experiments.sandbox import LABEL_MANAGED, LABEL_PLAN

CREATE = {
    "Deployment": (client.AppsV1Api, "create_namespaced_deployment"),
    "DaemonSet": (client.AppsV1Api, "create_namespaced_daemon_set"),
    "Job": (client.BatchV1Api, "create_namespaced_job"),
    "PersistentVolumeClaim": (client.CoreV1Api, "create_namespaced_persistent_volume_claim"),
    "ConfigMap": (client.CoreV1Api, "create_namespaced_config_map"),
}
# Deletion order: workloads first, so PVCs are no longer in use when they go.
DELETE_COLLECTION = [
    (client.AppsV1Api, "delete_collection_namespaced_deployment"),
    (client.AppsV1Api, "delete_collection_namespaced_daemon_set"),
    (client.BatchV1Api, "delete_collection_namespaced_job"),
    (client.CoreV1Api, "delete_collection_namespaced_config_map"),
    (client.CoreV1Api, "delete_collection_namespaced_persistent_volume_claim"),
]


def create(api_client: client.ApiClient, obj: dict, dry_run: bool = False):
    api_cls, method = CREATE[obj["kind"]]
    meta = obj["metadata"]
    kwargs = {"dry_run": "All"} if dry_run else {}
    result = getattr(api_cls(api_client), method)(meta["namespace"], obj, **kwargs)
    if not dry_run:
        audit("k8s.create", f"{obj['kind']} {meta['namespace']}/{meta['name']}")
    return result


def delete_object(api_client: client.ApiClient, kind: str, namespace: str, name: str) -> None:
    """Delete one object (and its pods); a 404 means it is already gone."""
    methods = {
        "Deployment": client.AppsV1Api(api_client).delete_namespaced_deployment,
        "DaemonSet": client.AppsV1Api(api_client).delete_namespaced_daemon_set,
        "Job": client.BatchV1Api(api_client).delete_namespaced_job,
    }
    try:
        methods[kind](name, namespace, propagation_policy="Background")
    except client.ApiException as exc:
        if exc.status != 404:
            raise
    audit("k8s.delete", f"{kind} {namespace}/{name}")


def cleanup(api_client: client.ApiClient, namespace: str, plan: str | None = None) -> None:
    """Delete everything KubeSage created in the sandbox (for one plan, or all) and wait for pods."""
    selector = f"{LABEL_MANAGED}=kubesage," + (f"{LABEL_PLAN}={plan}" if plan else LABEL_PLAN)
    for api_cls, method in DELETE_COLLECTION:
        kwargs = {"label_selector": selector}
        if "config_map" not in method and "persistent_volume_claim" not in method:
            kwargs["propagation_policy"] = "Background"
        getattr(api_cls(api_client), method)(namespace, **kwargs)
    audit("k8s.cleanup", f"{namespace} {selector}")
    wait_no_pods(api_client, namespace, selector, timeout_s=180)


def wait_no_pods(api_client: client.ApiClient, namespace: str, selector: str, timeout_s: float) -> None:
    core = client.CoreV1Api(api_client)
    deadline = time.monotonic() + timeout_s
    while core.list_namespaced_pod(namespace, label_selector=selector).items:
        if time.monotonic() > deadline:
            raise TimeoutError(f"pods matching {selector} still present after {timeout_s:.0f} s")
        time.sleep(2)
