"""API response models. The frontend's TypeScript types mirror these."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["large_image", "slow_registry", "model_loading", "app_init", "capacity", "slow_readiness"]


class PhaseOut(BaseModel):
    name: str
    start: datetime
    end: datetime
    duration_s: float
    source: Literal["k8s", "event", "span"]
    parent: str | None = None
    attributes: dict = {}


class CandidateOut(BaseModel):
    category: Category
    score: float  # share of total startup time explained, 0..1
    seconds: float
    summary: str
    evidence: dict = {}


class StartupSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workload_id: int
    namespace: str
    pod_name: str
    node: str | None
    image: str | None
    created_at: datetime
    ready_at: datetime | None
    total_s: float | None
    cold: bool | None
    status: Literal["starting", "ready"]
    top_category: Category | None
    incident_id: int | None = None


class StartupDetail(StartupSummary):
    spans_found: bool
    phases: list[PhaseOut]
    signals: dict
    candidates: list[CandidateOut]


class WorkloadOut(BaseModel):
    id: int
    namespace: str
    kind: str
    name: str
    slo_seconds: float
    startup_count: int
    median_total_s: float | None
    open_incidents: int
    last_startup: StartupSummary | None


class SloUpdate(BaseModel):
    slo_seconds: float


class IncidentOut(BaseModel):
    id: int
    workload_id: int
    workload_name: str
    namespace: str
    startup_id: int
    pod_name: str
    status: Literal["open", "diagnosed", "resolved"]
    detected_at: datetime
    total_s: float
    threshold_s: float
    breach_reason: Literal["slo", "regression"]
    baseline_s: float | None
    top_category: Category | None
    candidates: list[CandidateOut]


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ref: str
    tool: str
    args: dict
    result: dict
    created_at: datetime


class FindingOut(BaseModel):
    statement: str
    evidence_ids: list[str]


class RuledOutOut(BaseModel):
    category: str
    reason: str
    evidence_ids: list[str]


class RecommendationOut(BaseModel):
    action: str
    rationale: str
    evidence_ids: list[str]


class DiagnosisRunOut(BaseModel):
    """`source`: 'llm' = validated AI diagnosis; 'fallback' = deterministic detector result (LLM failed or invalid)."""

    id: int
    incident_id: int
    status: Literal["running", "completed", "failed"]
    source: Literal["llm", "fallback"] | None
    provider: str | None
    model: str | None
    prompt_version: str | None
    category: str | None  # a Category or "unknown"
    summary: str | None
    confidence: Literal["high", "medium", "low"] | None
    findings: list[FindingOut]
    ruled_out: list[RuledOutOut]
    recommendations: list[RecommendationOut]
    validation_errors: list[str]
    tool_calls: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    llm_calls: int
    input_tokens: int | None
    output_tokens: int | None
    evidence: list[EvidenceOut]


class LlmCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int | None
    purpose: str
    provider: str
    model: str
    prompt_version: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    status: Literal["ok", "error"]
    error: str | None
    created_at: datetime


class PolicyCheckOut(BaseModel):
    name: str
    passed: bool
    detail: str


class ActionPlanOut(BaseModel):
    """`source`: 'llm' = the agent chose the action; 'fallback' = deterministic default (model unavailable/invalid).
    `export` holds the patch for the original workload: {patch, objects, command}."""

    id: int
    incident_id: int
    diagnosis_run_id: int | None
    category: str
    action: str
    action_title: str
    action_note: str
    params: dict
    rationale: str
    source: Literal["llm", "fallback"]
    llm_error: str | None
    risk: str
    status: Literal["awaiting_approval", "rejected_by_policy", "rejected", "running", "completed", "failed", "aborted"]
    policy_checks: list[PolicyCheckOut]
    diff: str
    export: dict
    decided_by: str | None
    decision_note: str | None
    decided_at: datetime | None
    created_at: datetime
    experiment_id: int | None


class DecisionIn(BaseModel):
    approver: str = Field(min_length=1, max_length=128)
    note: str | None = Field(None, max_length=2000)


class TrialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    arm: Literal["baseline", "candidate"]
    warmup: bool
    status: Literal["ok", "failed"]
    pod_name: str | None
    startup_id: int | None
    total_s: float | None
    phases: dict[str, float]
    guardrails: dict
    prep_s: float | None
    error: str | None


class ExperimentOut(BaseModel):
    """`comparison`: {baseline, candidate: {trials, succeeded, failed, median_s, min_s, max_s, phase_median_s,
    prep_median_s}, delta_s, delta_pct, threshold_s, verdict, reason}; empty until the experiment finishes."""

    id: int
    plan_id: int
    incident_id: int
    action: str
    action_note: str
    status: Literal["running", "completed", "failed", "aborted"]
    trials_per_arm: int
    planned_trials: int  # 1 warm-up + 2 x trials_per_arm
    node: str | None
    prep: dict
    comparison: dict
    verdict: Literal["improved", "no_change", "regressed", "failed"] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    trials: list[TrialOut]


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    actor: str
    event: str
    target: str | None
    details: dict


class LlmUsageOut(BaseModel):
    purpose: str
    model: str
    calls: int
    errors: int
    input_tokens: int
    output_tokens: int
    avg_latency_ms: int


class LlmOpsOut(BaseModel):
    usage: list[LlmUsageOut]
    latest_eval: dict | None  # contents of the newest backend/evals/results/*.json


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    kubernetes: bool
    jaeger: bool
    prometheus: bool
    watchers_running: bool
    clock_offset_s: float | None
