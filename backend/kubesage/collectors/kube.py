"""Kubernetes API access and a resilient list+watch loop."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

log = logging.getLogger(__name__)


def new_api_client(context: str) -> client.ApiClient:
    """One ApiClient per thread: the generated client is not thread-safe."""
    configuration = client.Configuration()
    config.load_kube_config(context=context, client_configuration=configuration)
    return client.ApiClient(configuration)


def to_dict(api_client: client.ApiClient, obj) -> dict:
    """Model object -> Kubernetes JSON form (camelCase, ISO timestamps), same as `kubectl get -o json`."""
    return api_client.sanitize_for_serialization(obj)


def watch_namespaced(
    context: str,
    namespace: str,
    list_method: str,
    handler: Callable[[dict], None],
    stop: threading.Event,
    on_delete: Callable[[dict], None] | None = None,
    timeout_seconds: int = 120,
) -> None:
    """List, then watch, calling `handler` for every added/modified object and `on_delete` for deletions.

    Relists on 410 Gone or errors.
    """
    api_client = new_api_client(context)
    core = client.CoreV1Api(api_client)
    method = getattr(core, list_method)
    resource_version = None
    while not stop.is_set():
        try:
            if resource_version is None:
                listing = method(namespace)
                for item in listing.items:
                    handler(to_dict(api_client, item))
                resource_version = listing.metadata.resource_version
            stream = watch.Watch()
            for change in stream.stream(
                method, namespace, resource_version=resource_version, timeout_seconds=timeout_seconds
            ):
                if stop.is_set():
                    stream.stop()
                    break
                obj = change["object"]
                resource_version = obj.metadata.resource_version
                if change["type"] in ("ADDED", "MODIFIED"):
                    handler(to_dict(api_client, obj))
                elif change["type"] == "DELETED" and on_delete:
                    on_delete(to_dict(api_client, obj))
        except ApiException as exc:
            if exc.status == 410:
                resource_version = None  # history expired: relist
                continue
            log.warning("watch %s/%s failed: %s", namespace, list_method, exc)
            resource_version = None
            stop.wait(5)
        except Exception:
            log.exception("watch %s/%s crashed; restarting", namespace, list_method)
            resource_version = None
            stop.wait(5)


def node_capacity(api_client: client.ApiClient, node_name: str) -> dict:
    """Allocatable CPU/memory of a node and what its scheduled pods request."""
    core = client.CoreV1Api(api_client)
    node = core.read_node(node_name)
    pods = core.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}").items
    active = [p for p in pods if p.status.phase in ("Pending", "Running")]
    cpu_m = sum(_cpu_millicores(c.resources.requests or {}) for p in active for c in p.spec.containers)
    mem_b = sum(_memory_bytes(c.resources.requests or {}) for p in active for c in p.spec.containers)
    return {
        "node": node_name,
        "allocatable_cpu_millicores": _cpu_millicores({"cpu": node.status.allocatable.get("cpu", "0")}),
        "allocatable_memory_bytes": _memory_bytes({"memory": node.status.allocatable.get("memory", "0")}),
        "requested_cpu_millicores": cpu_m,
        "requested_memory_bytes": mem_b,
        "active_pods": len(active),
        "conditions": {c.type: c.status for c in node.status.conditions or []},
    }


def _cpu_millicores(resources: dict) -> int:
    value = str(resources.get("cpu", "0"))
    return int(value[:-1]) if value.endswith("m") else int(float(value) * 1000)


MEMORY_SUFFIXES = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "k": 1000, "M": 1000**2, "G": 1000**3}


def _memory_bytes(resources: dict) -> int:
    value = str(resources.get("memory", "0"))
    for suffix, factor in MEMORY_SUFFIXES.items():
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * factor)
    return int(float(value))


def measure_clock_offset(api_client: client.ApiClient, namespace: str) -> float:
    """Seconds by which the cluster clock leads this host, from a throwaway ConfigMap's creationTimestamp.

    Resolution is about one second (the server timestamp is truncated to whole seconds).
    """
    core = client.CoreV1Api(api_client)
    before = datetime.now(UTC)
    created = core.create_namespaced_config_map(
        namespace,
        client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                generate_name="kubesage-clock-", labels={"app.kubernetes.io/managed-by": "kubesage"}
            )
        ),
    )
    after = datetime.now(UTC)
    core.delete_namespaced_config_map(created.metadata.name, namespace)
    midpoint = before + (after - before) / 2
    # +0.5 s centres the truncated server timestamp within its second.
    return round((created.metadata.creation_timestamp - midpoint).total_seconds() + 0.5, 1)
