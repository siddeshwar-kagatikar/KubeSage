"""The allow-listed optimisations.

Each action has bounded, typed parameters, the categories it may fix, and deterministic code that changes a pod
template (and adds objects such as a PVC). The LLM only chooses an action and its parameters; it never writes YAML.
`export_patch` produces the equivalent change for the original workload, for a human to review and apply.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_CACHE_VOLUME = "model-cache"
MODEL_CACHE_PATH = "/model-cache"
PAUSE_IMAGE = "registry.k8s.io/pause:3.10"  # already present on every kind node (sandbox image)


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SwitchImageParams(Params):
    tag: str = Field(
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$",
        description="Tag of a pre-built variant in the same image repository, e.g. 'slim'.",
    )


class ModelCacheParams(Params):
    size_gi: int = Field(1, ge=1, le=2, description="Persistent volume size in GiB (sandbox quota allows 2).")


class TuneProbesParams(Params):
    initial_delay_seconds: int = Field(0, ge=0, le=30, description="readinessProbe.initialDelaySeconds")
    period_seconds: int = Field(2, ge=1, le=10, description="readinessProbe.periodSeconds")


class CpuParams(Params):
    cpu_request_millicores: int = Field(500, ge=100, le=2000)
    cpu_limit_millicores: int = Field(1000, ge=250, le=2000)

    @model_validator(mode="after")
    def request_within_limit(self):
        if self.cpu_request_millicores > self.cpu_limit_millicores:
            raise ValueError("cpu_request_millicores must not exceed cpu_limit_millicores")
        return self


@dataclass(frozen=True)
class Changes:
    """What an action does: the new pod template, extra objects, and per-trial preparation."""

    template: dict
    objects: list[dict]  # created once before the candidate arm (e.g. PVC, prefetch Job)
    per_trial_objects: list[dict]  # created before every candidate trial, then deleted (e.g. pre-pull DaemonSet)


Builder = Callable[[dict, BaseModel, str, str], Changes]  # (template, params, name, namespace)


@dataclass(frozen=True)
class Action:
    name: str
    title: str
    description: str
    categories: frozenset[str]
    risk: str
    params: type[Params]
    trials_per_arm: int
    note: str  # how to read the before/after result
    build: Builder


def _container(template: dict) -> dict:
    return template["spec"]["containers"][0]


def _set_env(container: dict, name: str, value: str) -> None:
    env = container.setdefault("env", [])
    for item in env:
        if item["name"] == name:
            item.pop("valueFrom", None)
            item["value"] = value
            return
    env.append({"name": name, "value": value})


def image_repository(image: str) -> str:
    """'localhost:5001/demo-ai-service:fat' -> 'localhost:5001/demo-ai-service' (digests are not supported)."""
    last_slash = image.rfind("/")
    colon = image.rfind(":")
    return image[:colon] if colon > last_slash else image


def _switch_image(template: dict, params: SwitchImageParams, name: str, namespace: str) -> Changes:
    template = copy.deepcopy(template)
    container = _container(template)
    container["image"] = f"{image_repository(container['image'])}:{params.tag}"
    return Changes(template, [], [])


def _model_cache(template: dict, params: ModelCacheParams, name: str, namespace: str) -> Changes:
    template = copy.deepcopy(template)
    container = _container(template)
    claim = f"{name}-model-cache"
    _set_env(container, "MODEL_DIR", MODEL_CACHE_PATH)
    _set_env(container, "MODEL_CACHE", "true")
    container.setdefault("volumeMounts", []).append({"name": MODEL_CACHE_VOLUME, "mountPath": MODEL_CACHE_PATH})
    template["spec"].setdefault("volumes", []).append(
        {"name": MODEL_CACHE_VOLUME, "persistentVolumeClaim": {"claimName": claim}}
    )
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": claim, "namespace": namespace},
        "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": f"{params.size_gi}Gi"}}},
    }
    model_url = next(e["value"] for e in container["env"] if e["name"] == "MODEL_URL")
    # The demo app's own download routine fills the cache, so the app later finds a complete download.
    prefetch = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": f"{name}-model-prefetch", "namespace": namespace},
        "spec": {
            "backoffLimit": 1,
            "activeDeadlineSeconds": 600,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "nodeSelector": template["spec"].get("nodeSelector"),
                    "containers": [
                        {
                            "name": "prefetch",
                            "image": container["image"],
                            "command": ["python", "-c", "from app.main import download_model; download_model()"],
                            "env": [
                                {"name": "MODEL_URL", "value": model_url},
                                {"name": "MODEL_DIR", "value": MODEL_CACHE_PATH},
                                {"name": "MODEL_CACHE", "value": "true"},
                            ],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"memory": "512Mi"},
                            },
                            "volumeMounts": [{"name": MODEL_CACHE_VOLUME, "mountPath": MODEL_CACHE_PATH}],
                        }
                    ],
                    "volumes": [{"name": MODEL_CACHE_VOLUME, "persistentVolumeClaim": {"claimName": claim}}],
                }
            },
        },
    }
    return Changes(template, [pvc, prefetch], [])


def _tune_probes(template: dict, params: TuneProbesParams, name: str, namespace: str) -> Changes:
    template = copy.deepcopy(template)
    probe = _container(template)["readinessProbe"]
    probe["initialDelaySeconds"] = params.initial_delay_seconds
    probe["periodSeconds"] = params.period_seconds
    return Changes(template, [], [])


def _adjust_cpu(template: dict, params: CpuParams, name: str, namespace: str) -> Changes:
    template = copy.deepcopy(template)
    resources = _container(template).setdefault("resources", {})
    resources.setdefault("requests", {})["cpu"] = f"{params.cpu_request_millicores}m"
    resources.setdefault("limits", {})["cpu"] = f"{params.cpu_limit_millicores}m"
    return Changes(template, [], [])


def _prepull(template: dict, params: Params, name: str, namespace: str) -> Changes:
    image = _container(template)["image"]
    labels = {"kubesage.io/prepull": name}
    daemonset = {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": {"name": f"{name}-prepull", "namespace": namespace},
        "spec": {
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "nodeSelector": template["spec"].get("nodeSelector"),
                    # The init container only exists to pull the image; it exits at once.
                    "initContainers": [
                        {
                            "name": "prepull",
                            "image": image,
                            "command": ["sh", "-c", "exit 0"],
                            "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"memory": "64Mi"}},
                        }
                    ],
                    "containers": [
                        {
                            "name": "pause",
                            "image": PAUSE_IMAGE,
                            "resources": {"requests": {"cpu": "10m", "memory": "8Mi"}, "limits": {"memory": "16Mi"}},
                        }
                    ],
                },
            },
        },
    }
    return Changes(copy.deepcopy(template), [], [daemonset])


ACTIONS: dict[str, Action] = {
    a.name: a
    for a in [
        Action(
            name="switch_image_variant",
            title="Switch to a slimmer image variant",
            description="Point the container at a pre-built variant (another tag) of the same image repository.",
            categories=frozenset({"large_image"}),
            risk="medium",
            params=SwitchImageParams,
            trials_per_arm=3,
            note="Both arms start on a node with the image evicted, so each trial includes a cold pull.",
            build=_switch_image,
        ),
        Action(
            name="prepull_daemonset",
            title="Pre-pull the image on nodes",
            description="Run a DaemonSet that pulls the image ahead of scale-up, so pods start with it cached.",
            categories=frozenset({"large_image", "slow_registry"}),
            risk="low",
            params=Params,
            trials_per_arm=3,
            note="Moves the pull out of the startup path rather than removing it; see the per-trial pre-pull time.",
            build=_prepull,
        ),
        Action(
            name="model_cache_pvc",
            title="Cache model weights on a persistent volume",
            description="Keep weights on a PVC filled once by a prefetch Job; the app reuses the cached download.",
            categories=frozenset({"model_loading"}),
            risk="medium",
            params=ModelCacheParams,
            trials_per_arm=3,
            note="The one-off prefetch time is reported separately. Repeated reads may benefit from the page cache.",
            build=_model_cache,
        ),
        Action(
            name="tune_probes",
            title="Tighten the readiness probe",
            description="Lower readinessProbe initialDelaySeconds/periodSeconds so readiness is detected promptly.",
            categories=frozenset({"slow_readiness"}),
            risk="low",
            params=TuneProbesParams,
            trials_per_arm=5,
            note="Measures how quickly readiness is detected; the application itself does not start faster.",
            build=_tune_probes,
        ),
        Action(
            name="adjust_cpu_resources",
            title="Raise the CPU request and limit",
            description="Give CPU-bound initialisation more CPU within sandbox bounds (max 2 cores).",
            categories=frozenset({"app_init"}),
            risk="medium",
            params=CpuParams,
            trials_per_arm=5,
            note="Only effective when initialisation is CPU-throttled; check the throttling evidence.",
            build=_adjust_cpu,
        ),
    ]
}


def export_patch(deployment: dict, action: Action, params: Params) -> dict:
    """The change for the original Deployment, for a human to review and apply: a strategic-merge patch of what
    changed in the container and volumes, the extra objects in its namespace, and the kubectl command."""
    meta = deployment["metadata"]
    before = deployment["spec"]["template"]
    changes = action.build(before, params, meta["name"], meta["namespace"])
    container_before, container_after = before["spec"]["containers"][0], changes.template["spec"]["containers"][0]
    container_patch = {"name": container_after["name"]}
    for key in ("image", "env", "resources", "readinessProbe", "volumeMounts"):
        if container_after.get(key) != container_before.get(key):
            container_patch[key] = container_after.get(key)
    template_patch: dict = {"containers": [container_patch]}
    new_volumes = [v for v in changes.template["spec"].get("volumes", []) if v not in before["spec"].get("volumes", [])]
    if new_volumes:
        template_patch["volumes"] = new_volumes
    patch = {"spec": {"template": {"spec": template_patch}}}
    return {
        "patch": patch,
        "objects": changes.objects + changes.per_trial_objects,
        "command": f"kubectl -n {meta['namespace']} patch deployment {meta['name']} --type strategic --patch-file patch.json",
    }
