"""API response models. The frontend's TypeScript types mirror these."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

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


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    kubernetes: bool
    jaeger: bool
    prometheus: bool
    watchers_running: bool
    clock_offset_s: float | None
