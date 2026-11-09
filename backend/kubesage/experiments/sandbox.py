"""Builds a sanitised copy of a Deployment for the sandbox namespace."""

import copy
import re

LABEL_CLONE = "kubesage.io/clone"
LABEL_PLAN = "kubesage.io/plan"  # the ActionPlan id (one experiment per plan)
LABEL_MANAGED = "app.kubernetes.io/managed-by"
SANDBOX_POOL_LABEL = "kubesage.io/pool"
MAX_LABEL_VALUE = 63

# http(s)://host[:port|/...] where host is a bare service name (no dots), e.g. http://slow-dependency/
# shortcut: only http(s) URLs are qualified; bare "host:port" values (e.g. redis:6379) are left as they are.
SHORT_HOST_RE = re.compile(r"(?P<scheme>https?://)(?P<host>[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)(?=[:/]|$)")


class CloneRefused(ValueError):
    """The workload references something that must not be copied into the sandbox (Secrets, foreign PVCs)."""


def clone_name(source_name: str) -> str:
    return f"{source_name[: MAX_LABEL_VALUE - len('-sandbox')]}-sandbox"


def qualify_hosts(value: str, namespace: str) -> str:
    """Short service names only resolve in their own namespace; make them fully qualified."""

    def repl(m: re.Match) -> str:
        if m["host"] == "localhost":
            return m.group(0)
        return f"{m['scheme']}{m['host']}.{namespace}.svc.cluster.local"

    return SHORT_HOST_RE.sub(repl, value)


def clone_deployment(deployment: dict, plan_id: int, namespace: str, pool: str = "sandbox") -> tuple[dict, list[str]]:
    """(sandbox Deployment manifest, names of ConfigMaps to copy). Raises CloneRefused."""
    meta = deployment["metadata"]
    source_ns, name = meta["namespace"], clone_name(meta["name"])
    template = copy.deepcopy(deployment["spec"]["template"])
    pod = template["spec"]
    configmaps: set[str] = set()

    for container in pod.get("containers", []) + pod.get("initContainers", []):
        for item in container.get("env", []):
            source = item.get("valueFrom", {})
            if "secretKeyRef" in source:
                raise CloneRefused(f"container {container['name']} reads Secret {source['secretKeyRef'].get('name')}")
            if "configMapKeyRef" in source:
                configmaps.add(source["configMapKeyRef"]["name"])
            if "value" in item:
                item["value"] = qualify_hosts(item["value"], source_ns)
        for source in container.get("envFrom", []):
            if "secretRef" in source:
                raise CloneRefused(f"container {container['name']} loads Secret {source['secretRef'].get('name')}")
            if "configMapRef" in source:
                configmaps.add(source["configMapRef"]["name"])
    for volume in pod.get("volumes", []):
        if "secret" in volume:
            raise CloneRefused(f"volume {volume['name']} mounts a Secret")
        if "persistentVolumeClaim" in volume:
            raise CloneRefused(f"volume {volume['name']} mounts PVC {volume['persistentVolumeClaim']['claimName']}")
        if "configMap" in volume:
            configmaps.add(volume["configMap"]["name"])
        for source in volume.get("projected", {}).get("sources", []):
            if "secret" in source:
                raise CloneRefused(f"volume {volume['name']} projects a Secret")
            if "configMap" in source:
                configmaps.add(source["configMap"]["name"])

    # No credentials, no placement from the source cluster area: run on the sandbox node only.
    for key in (
        "serviceAccountName",
        "serviceAccount",
        "imagePullSecrets",
        "nodeName",
        "affinity",
        "topologySpreadConstraints",
    ):
        pod.pop(key, None)
    pod["automountServiceAccountToken"] = False
    pod["nodeSelector"] = {SANDBOX_POOL_LABEL: pool}

    labels = {k: v for k, v in template.get("metadata", {}).get("labels", {}).items() if k != "pod-template-hash"}
    labels.update({LABEL_CLONE: name, LABEL_PLAN: str(plan_id), LABEL_MANAGED: "kubesage"})
    template["metadata"] = {"labels": labels}

    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {LABEL_PLAN: str(plan_id), LABEL_MANAGED: "kubesage"},
            "annotations": {"kubesage.io/source": f"{source_ns}/{meta['name']}"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {LABEL_CLONE: name}},
            "template": template,
        },
    }
    return manifest, sorted(configmaps)


def label_objects(objects: list[dict], plan_id: int) -> list[dict]:
    """Mark extra objects (and their pod templates) so cleanup by label finds everything."""
    labelled = []
    for obj in objects:
        obj = copy.deepcopy(obj)
        marks = {LABEL_PLAN: str(plan_id), LABEL_MANAGED: "kubesage"}
        obj["metadata"].setdefault("labels", {}).update(marks)
        if "template" in obj.get("spec", {}):
            obj["spec"]["template"].setdefault("metadata", {}).setdefault("labels", {}).update(marks)
        labelled.append(obj)
    return labelled
