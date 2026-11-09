"""Optimization Agent and experiment logic, without a cluster or API calls."""

import copy

import pytest
import yaml
from pydantic import ValidationError

from kubesage.config import REPO_ROOT
from kubesage.experiments.compare import compare
from kubesage.experiments.sandbox import LABEL_CLONE, CloneRefused, clone_deployment, qualify_hosts
from kubesage.llm.calls import CallLogger
from kubesage.llm.provider import UnavailableModel
from kubesage.llm.replay import ScriptedModel
from kubesage.optimizer.catalog import ACTIONS, CpuParams, TuneProbesParams, export_patch
from kubesage.optimizer.planner import Proposal, choose, policy_checks, prepare, viable_actions


def scenario(name: str) -> dict:
    deployment = yaml.safe_load((REPO_ROOT / "deploy" / "scenarios" / name / "deployment.yaml").read_text())
    deployment["metadata"]["namespace"] = "demo"
    return deployment


class FakeRegistry:
    def __init__(self, sizes):
        self.sizes = sizes

    def variants(self, path):
        return self.sizes


def calls():
    return CallLogger(None, "scripted", "scripted", "test", persist=False)


# --- sandbox clone ----------------------------------------------------------------------------------


def test_clone_is_pinned_relabelled_and_stripped():
    deployment = scenario("s3-model-weights")
    deployment["spec"]["template"]["spec"]["serviceAccountName"] = "app-sa"
    clone, configmaps = clone_deployment(deployment, 7, "kubesage-sandbox")
    pod = clone["spec"]["template"]["spec"]
    assert clone["metadata"]["namespace"] == "kubesage-sandbox"
    assert clone["metadata"]["name"] == "model-weights-svc-sandbox"
    assert pod["nodeSelector"] == {"kubesage.io/pool": "sandbox"}
    assert "serviceAccountName" not in pod and pod["automountServiceAccountToken"] is False
    assert clone["spec"]["selector"]["matchLabels"] == {LABEL_CLONE: "model-weights-svc-sandbox"}
    assert clone["spec"]["template"]["metadata"]["labels"][LABEL_CLONE] == "model-weights-svc-sandbox"
    assert configmaps == []
    # short in-cluster URLs stay resolvable from the sandbox; FQDNs are untouched
    env = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}
    assert env["MODEL_URL"] == "http://model-hub.kubesage-infra.svc.cluster.local/weights.bin"
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "app-sa"  # source not mutated


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://slow-dependency/", "http://slow-dependency.demo.svc.cluster.local/"),
        ("http://db:5432/x", "http://db.demo.svc.cluster.local:5432/x"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("https://api.example.com/v1", "https://api.example.com/v1"),
        ("s3://bucket/key", "s3://bucket/key"),
    ],
)
def test_qualify_hosts(value, expected):
    assert qualify_hosts(value, "demo") == expected


@pytest.mark.parametrize(
    "mutate",
    [
        lambda pod: pod["containers"][0]["env"].append(
            {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db", "key": "password"}}}
        ),
        lambda pod: pod["containers"][0].setdefault("envFrom", []).append({"secretRef": {"name": "creds"}}),
        lambda pod: pod.setdefault("volumes", []).append({"name": "s", "secret": {"secretName": "x"}}),
        lambda pod: pod.setdefault("volumes", []).append({"name": "d", "persistentVolumeClaim": {"claimName": "data"}}),
    ],
)
def test_clone_refuses_secrets_and_foreign_pvcs(mutate):
    deployment = scenario("s3-model-weights")
    mutate(deployment["spec"]["template"]["spec"])
    with pytest.raises(CloneRefused):
        clone_deployment(deployment, 1, "kubesage-sandbox")


def test_clone_collects_configmaps():
    deployment = scenario("s1-large-image")
    deployment["spec"]["template"]["spec"]["volumes"] = [{"name": "c", "configMap": {"name": "app-config"}}]
    _, configmaps = clone_deployment(deployment, 1, "kubesage-sandbox")
    assert configmaps == ["app-config"]


# --- catalogue --------------------------------------------------------------------------------------


def test_switch_image_changes_only_the_tag():
    template = scenario("s1-large-image")["spec"]["template"]
    changes = ACTIONS["switch_image_variant"].build(
        template, ACTIONS["switch_image_variant"].params(tag="slim"), "x", "ns"
    )
    assert changes.template["spec"]["containers"][0]["image"] == "localhost:5001/demo-ai-service:slim"
    assert template["spec"]["containers"][0]["image"].endswith(":fat")


def test_model_cache_mounts_pvc_and_prefetches():
    template = scenario("s3-model-weights")["spec"]["template"]
    action = ACTIONS["model_cache_pvc"]
    changes = action.build(template, action.params(), "svc", "kubesage-sandbox")
    container = changes.template["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in container["env"] if "value" in e}
    assert env["MODEL_DIR"] == "/model-cache" and env["MODEL_CACHE"] == "true"
    assert {"name": "model-cache", "mountPath": "/model-cache"} in container["volumeMounts"]
    pvc, job = changes.objects
    assert pvc["kind"] == "PersistentVolumeClaim" and pvc["spec"]["resources"]["requests"]["storage"] == "1Gi"
    assert job["kind"] == "Job"
    job_env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert job_env["MODEL_URL"] == env["MODEL_URL"]


def test_params_are_bounded():
    with pytest.raises(ValidationError):
        TuneProbesParams(initial_delay_seconds=31)
    with pytest.raises(ValidationError):
        CpuParams(cpu_request_millicores=1500, cpu_limit_millicores=1000)
    with pytest.raises(ValidationError):
        CpuParams(cpu_limit_millicores=4000)
    with pytest.raises(ValidationError):
        ACTIONS["switch_image_variant"].params(tag="slim; rm -rf /")


def test_export_patch_targets_the_original_workload():
    deployment = scenario("s3-model-weights")
    export = export_patch(deployment, ACTIONS["model_cache_pvc"], ACTIONS["model_cache_pvc"].params())
    spec = export["patch"]["spec"]["template"]["spec"]
    assert spec["containers"][0]["name"] == "app"
    assert {"name": "model-cache", "persistentVolumeClaim": {"claimName": "model-weights-svc-model-cache"}} in spec[
        "volumes"
    ]
    assert {o["metadata"]["namespace"] for o in export["objects"]} == {"demo"}
    assert "kubectl -n demo patch deployment model-weights-svc" in export["command"]


# --- action selection ---------------------------------------------------------------------------------


def test_viable_actions(settings):
    names = lambda acts: [a.name for a in acts]
    acts, _ = viable_actions("model_loading", {}, scenario("s3-model-weights"), FakeRegistry({}), settings)
    assert names(acts) == ["model_cache_pvc"]
    acts, facts = viable_actions(
        "large_image", {}, scenario("s1-large-image"), FakeRegistry({"fat": 600, "slim": 50, "huge": 900}), settings
    )
    assert names(acts) == ["switch_image_variant", "prepull_daemonset"]
    assert facts["image_variants_compressed_bytes"] == {"slim": 50}  # only smaller variants
    # dependency wait dominates: probe tuning would not help
    acts, _ = viable_actions(
        "slow_readiness",
        {"readiness_s": 1.0, "dependency_s": 40.0},
        scenario("s6a-slow-readiness"),
        FakeRegistry({}),
        settings,
    )
    assert acts == []
    acts, _ = viable_actions("capacity", {}, scenario("s1-large-image"), FakeRegistry({}), settings)
    assert acts == []


def choice(action, params_json, rationale="because"):
    return {"type": "structured", "data": {"action": action, "params_json": params_json, "rationale": rationale}}


FACTS = {"image_variants_compressed_bytes": {"slim": 50}}


def test_llm_choice_is_validated():
    acts = [ACTIONS["switch_image_variant"], ACTIONS["prepull_daemonset"]]
    container = scenario("s1-large-image")["spec"]["template"]["spec"]["containers"][0]
    ok = choose(acts, FACTS, {}, container, ScriptedModel([choice("prepull_daemonset", "{}")]), calls())
    assert (ok.action.name, ok.source) == ("prepull_daemonset", "llm")
    for bad in (
        choice("delete_namespace", "{}"),  # not allowed
        choice("switch_image_variant", '{"tag": "latest"}'),  # not a known smaller variant
        choice("switch_image_variant", '{"tag": "slim", "extra": 1}'),  # unexpected parameter
    ):
        result = choose(acts, FACTS, {}, container, ScriptedModel([bad]), calls())
        assert result.source == "fallback" and result.action.name == "switch_image_variant"
        assert result.params.tag == "slim"
    down = choose(acts, FACTS, {}, container, UnavailableModel(ConnectionError("quota")), calls())
    assert down.source == "fallback" and "quota" in down.llm_error


def test_out_of_bounds_llm_params_fall_back_to_defaults():
    container = scenario("s6a-slow-readiness")["spec"]["template"]["spec"]["containers"][0]
    result = choose(
        [ACTIONS["tune_probes"]],
        {},
        {},
        container,
        ScriptedModel([choice("tune_probes", '{"initial_delay_seconds": 120}')]),
        calls(),
    )
    assert result.source == "fallback" and result.params.initial_delay_seconds == 0


# --- policy -------------------------------------------------------------------------------------------


def proposal(action="tune_probes", **params):
    return Proposal(ACTIONS[action], ACTIONS[action].params(**params), "r", "llm")


def test_policy_passes_for_a_sandbox_plan(settings):
    settings.lab_mode = True
    deployment = scenario("s6a-slow-readiness")
    p = proposal()
    prepared = prepare(deployment, p, 3, settings)
    checks = policy_checks(prepared, None, p, "slow_readiness", "Deployment", settings, None, False)
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]
    assert "initialDelaySeconds: 0" in prepared.diff and "initialDelaySeconds: 45" in prepared.diff


def test_policy_rejects_unsafe_plans(settings):
    settings.lab_mode = False
    deployment = scenario("s6a-slow-readiness")
    p = proposal()
    prepared = prepare(deployment, p, 3, settings)
    prepared.candidate = copy.deepcopy(prepared.candidate)
    prepared.candidate["metadata"]["namespace"] = "demo"  # a write outside the sandbox
    checks = {
        c.name: c.passed for c in policy_checks(prepared, None, p, "large_image", "StatefulSet", settings, None, True)
    }
    assert not checks["sandbox_only"]
    assert not checks["lab_mode_enabled"]
    assert not checks["category_supported"]
    assert not checks["workload_is_deployment"]
    assert not checks["no_experiment_running"]
    refused = {
        c.name: c.passed
        for c in policy_checks(None, "reads Secret db", p, "slow_readiness", "Deployment", settings, None, False)
    }
    assert not refused["clone_safe"]


# --- comparison ---------------------------------------------------------------------------------------


def trial(arm, total, status="ok", warmup=False, prep=None):
    return {
        "arm": arm,
        "warmup": warmup,
        "status": status,
        "total_s": total,
        "phases": {"image_pull": total - 2},
        "prep_s": prep,
    }


def test_compare_improved_excludes_warmup():
    result = compare(
        [
            trial("baseline", 500, warmup=True),
            trial("baseline", 55),
            trial("candidate", 8),
            trial("baseline", 53),
            trial("candidate", 9),
        ]
    )
    assert result["verdict"] == "improved"
    assert result["baseline"]["median_s"] == 54 and result["candidate"]["median_s"] == 8.5
    assert result["delta_s"] == 45.5 and result["baseline"]["trials"] == 2


def test_compare_no_change_and_regressed():
    assert compare([trial("baseline", 30), trial("candidate", 29)])["verdict"] == "no_change"
    assert compare([trial("baseline", 30), trial("candidate", 40)])["verdict"] == "regressed"


def test_candidate_guardrail_failure_fails_the_experiment():
    result = compare([trial("baseline", 30), trial("candidate", 5, status="failed"), trial("candidate", 6)])
    assert result["verdict"] == "failed" and "candidate failed" in result["reason"]
