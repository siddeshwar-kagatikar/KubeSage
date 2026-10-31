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
