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


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    kubernetes: bool
    jaeger: bool
    prometheus: bool
    watchers_running: bool
    clock_offset_s: float | None
