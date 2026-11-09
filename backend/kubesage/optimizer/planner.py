"""Optimization Agent: chooses an allow-listed action for a diagnosis and checks it against policy.

    viable actions (category + preconditions) -> LLM picks one + parameters (validated) | deterministic fallback
    -> deterministic sandbox clone + changes + diff -> policy checks (incl. server-side dry-run as KubeSage's SA)

Approval is a persisted plan status, so a pending approval survives restarts without any extra machinery.
"""

import difflib
import json
import logging
from dataclasses import dataclass, field

import yaml
from kubernetes import client
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from kubesage.config import Settings
from kubesage.experiments import k8s
from kubesage.experiments.sandbox import CloneRefused, clone_deployment, label_objects
from kubesage.llm.calls import CallLogger
from kubesage.optimizer.catalog import ACTIONS, Action, Changes, Params, export_patch, image_repository
from kubesage.optimizer.registry import Registry, split_repository

log = logging.getLogger(__name__)

PROMPT_VERSION = "optimizer-v1"

SYSTEM = """\
You are KubeSage's Optimization Agent. Choose ONE remediation for a diagnosed slow Kubernetes pod startup from the
allow-listed actions given, and its parameters. The change is first tested in an isolated sandbox and a human
approves it. Prefer the action that removes the cost rather than hiding it, follow the diagnosis and its runbook
recommendations, and keep parameters conservative. Reply with params_json as a JSON object matching the action's
parameter schema exactly (use {} for an action without parameters).
"""


class ActionChoice(BaseModel):
    action: str = Field(description="Name of one of the allowed actions.")
    params_json: str = Field(description='Parameters as a JSON object string, e.g. \'{"tag": "slim"}\'.')
    rationale: str = Field(description="One or two sentences: why this action and these parameters.")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class Proposal:
    action: Action
    params: Params
    rationale: str
    source: str  # llm | fallback
    llm_error: str | None = None


@dataclass
class Prepared:
    """Everything an experiment needs, built deterministically from the source Deployment."""

    baseline: dict  # sandbox Deployment manifest, unchanged spec
    candidate: dict  # same with the action applied
    configmaps: list[str]
    changes: Changes
    diff: str
    export: dict
    checks: list[Check] = field(default_factory=list)


class NoAutomatedAction(ValueError):
    pass


def viable_actions(
    category: str, signals: dict, deployment: dict, registry: Registry, settings: Settings
) -> tuple[list[Action], dict]:
    """Actions whose category and preconditions fit this workload, plus facts the chooser needs (image variants)."""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"] for e in container.get("env", [])}
    facts: dict = {}
    actions = []
    for action in ACTIONS.values():
        if category not in action.categories:
            continue
        if action.name == "switch_image_variant":
            variants = _variants(container["image"], registry, settings)
            if not variants:
                continue
            facts["image_variants_compressed_bytes"] = variants
        if action.name == "model_cache_pvc" and "MODEL_URL" not in env:
            continue
        # Probe tuning only helps when the delay is readiness detection, not a dependency wait.
        readiness_bound = (signals.get("readiness_s") or 0) >= (signals.get("dependency_s") or 0)
        if action.name == "tune_probes" and ("readinessProbe" not in container or not readiness_bound):
            continue
        actions.append(action)
    return actions, facts


def _variants(image: str, registry: Registry, settings: Settings) -> dict[str, int]:
    """Smaller tags of the same repository in the lab registry (current tag excluded)."""
    repository = image_repository(image)
    path = split_repository(repository, settings.registry_hosts)
    if not path:
        return {}
    current = image[len(repository) + 1 :] if image != repository else "latest"
    try:
        sizes = registry.variants(path)
    except Exception as exc:
        log.warning("registry lookup for %s failed: %s", path, exc)
        return {}
    if current not in sizes:
        return {}
    return {tag: size for tag, size in sizes.items() if tag != current and size < sizes[current]}


def default_params(action: Action, facts: dict) -> Params:
    if action.name == "switch_image_variant":
        variants = facts["image_variants_compressed_bytes"]
        return action.params(tag=min(variants, key=variants.get))
    return action.params()


def choose(actions: list[Action], facts: dict, diagnosis: dict, container: dict, model, calls: CallLogger) -> Proposal:
    """One LLM call to pick the action and parameters; the deterministic first action on any failure."""
    fallback = actions[0]
    options = [
        {
            "action": a.name,
            "title": a.title,
            "description": a.description,
            "risk": a.risk,
            "params_schema": a.params.model_json_schema(),
            "default_params": default_params(a, facts).model_dump(),
        }
        for a in actions
    ]
    user = json.dumps(
        {
            "diagnosis": diagnosis,
            "container": {k: container.get(k) for k in ("image", "resources", "readinessProbe")},
            "facts": facts,
            "allowed_actions": options,
        },
        indent=1,
        default=str,
    )
    try:
        structured = model.with_structured_output(ActionChoice, include_raw=True)
        output = calls.call("select_action", lambda: structured.invoke([SystemMessage(SYSTEM), HumanMessage(user)]))
        choice: ActionChoice | None = output.get("parsed")
        if choice is None:
            raise ValueError(f"output did not parse: {output.get('parsing_error')}")
        action = next((a for a in actions if a.name == choice.action), None)
        if action is None:
            raise ValueError(f"{choice.action!r} is not an allowed action here")
        if action.name == "switch_image_variant":
            tag = json.loads(choice.params_json or "{}").get("tag")
            if tag not in facts["image_variants_compressed_bytes"]:
                raise ValueError(f"tag {tag!r} is not a smaller variant in the registry")
        params = action.params.model_validate_json(choice.params_json or "{}")
        return Proposal(action, params, choice.rationale, "llm")
    except (ValueError, ValidationError) as exc:
        error = str(exc)
    except Exception as exc:  # model unavailable, quota, timeout
        error = f"{type(exc).__name__}: {exc}"
    log.warning("action selection fell back to %s: %s", fallback.name, error)
    return Proposal(
        fallback,
        default_params(fallback, facts),
        f"{fallback.title} with default parameters (deterministic choice; the model's selection was unavailable).",
        "fallback",
        error,
    )


def prepare(deployment: dict, proposal: Proposal, plan_id: int, settings: Settings) -> Prepared:
    """Sandbox manifests, diff and export patch. Raises CloneRefused."""
    baseline, configmaps = clone_deployment(deployment, plan_id, settings.sandbox_namespace)
    name = baseline["metadata"]["name"]
    changes = proposal.action.build(baseline["spec"]["template"], proposal.params, name, settings.sandbox_namespace)
    changes = Changes(
        changes.template, label_objects(changes.objects, plan_id), label_objects(changes.per_trial_objects, plan_id)
    )
    candidate = json.loads(json.dumps(baseline))
    candidate["spec"]["template"] = changes.template
    before = yaml.safe_dump(baseline["spec"]["template"]["spec"], sort_keys=True).splitlines()
    after = yaml.safe_dump(candidate["spec"]["template"]["spec"], sort_keys=True).splitlines()
    diff = "\n".join(difflib.unified_diff(before, after, "baseline (pod spec)", "candidate (pod spec)", lineterm=""))
    extra = changes.objects + changes.per_trial_objects
    if extra:
        diff += "\n\n# Additional sandbox objects\n" + yaml.safe_dump_all(extra, sort_keys=False)
    return Prepared(
        baseline, candidate, configmaps, changes, diff, export_patch(deployment, proposal.action, proposal.params)
    )


def policy_checks(
    prepared: Prepared | None,
    clone_error: str | None,
    proposal: Proposal,
    category: str,
    workload_kind: str,
    settings: Settings,
    api_client: client.ApiClient | None,
    experiment_running: bool,
) -> list[Check]:
    checks = [
        Check(
            "category_supported",
            category in proposal.action.categories,
            f"{proposal.action.name} handles {', '.join(sorted(proposal.action.categories))}; diagnosis is {category}",
        ),
        Check("params_within_bounds", True, json.dumps(proposal.params.model_dump())),  # validated on construction
        Check("workload_is_deployment", workload_kind == "Deployment", f"workload kind {workload_kind}"),
        Check("lab_mode_enabled", settings.lab_mode, "cold-start trials evict images on the sandbox node"),
        Check("no_experiment_running", not experiment_running, "one experiment at a time"),
        Check("clone_safe", clone_error is None, clone_error or "no Secrets or foreign PVCs referenced"),
    ]
    if prepared is None:
        return checks
    objects = [prepared.candidate, *prepared.changes.objects, *prepared.changes.per_trial_objects]
    namespaces = {o["metadata"]["namespace"] for o in objects}
    checks.append(Check("sandbox_only", namespaces == {settings.sandbox_namespace}, f"writes to {sorted(namespaces)}"))
    if api_client is not None:
        checks.append(dry_run(api_client, objects))
    return checks


def dry_run(api_client: client.ApiClient, objects: list[dict]) -> Check:
    """Server-side dry-run as KubeSage's ServiceAccount: RBAC, quota and admission without changing anything."""
    for obj in objects:
        try:
            k8s.create(api_client, obj, dry_run=True)
        except client.ApiException as exc:
            reason = exc.reason
            try:
                reason = json.loads(exc.body).get("message", reason)
            except (TypeError, ValueError):
                pass
            return Check("server_dry_run", False, f"{obj['kind']} {obj['metadata']['name']}: {exc.status} {reason}")
    return Check("server_dry_run", True, f"{len(objects)} object(s) accepted by the API server (dryRun=All)")


def build_plan(
    *,
    plan_id: int,
    category: str,
    signals: dict,
    diagnosis: dict,
    deployment: dict,
    workload_kind: str,
    model,
    calls: CallLogger,
    registry: Registry,
    settings: Settings,
    api_client: client.ApiClient | None,
    experiment_running: bool,
) -> tuple[Proposal, Prepared | None, list[Check]]:
    actions, facts = viable_actions(category, signals, deployment, registry, settings)
    if not actions:
        raise NoAutomatedAction(category)
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    proposal = choose(actions, facts, diagnosis, container, model, calls)
    prepared, clone_error = None, None
    try:
        prepared = prepare(deployment, proposal, plan_id, settings)
    except CloneRefused as exc:
        clone_error = str(exc)
    checks = policy_checks(
        prepared, clone_error, proposal, category, workload_kind, settings, api_client, experiment_running
    )
    return proposal, prepared, checks
