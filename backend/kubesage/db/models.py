from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on Postgres, plain JSON elsewhere (keeps models importable without a database).
JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Workload(Base):
    __tablename__ = "workloads"
    __table_args__ = (UniqueConstraint("namespace", "kind", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(String(253))
    kind: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(253))
    slo_seconds: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    startups: Mapped[list["PodStartup"]] = relationship(back_populates="workload")


class PodStartup(Base):
    """One pod start, from creation to first Ready, with its phase breakdown."""

    __tablename__ = "pod_startups"

    id: Mapped[int] = mapped_column(primary_key=True)
    workload_id: Mapped[int] = mapped_column(ForeignKey("workloads.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(253))
    pod_name: Mapped[str] = mapped_column(String(253))
    pod_uid: Mapped[str] = mapped_column(String(64), unique=True)
    node: Mapped[str | None] = mapped_column(String(253))
    image: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_s: Mapped[float | None] = mapped_column(Float)
    cold: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(32), default="starting")  # starting | ready
    spans_found: Mapped[bool] = mapped_column(Boolean, default=False)
    phases: Mapped[list] = mapped_column(JsonType, default=list)
    signals: Mapped[dict] = mapped_column(JsonType, default=dict)
    candidates: Mapped[list] = mapped_column(JsonType, default=list)
    top_category: Mapped[str | None] = mapped_column(String(64))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Captured at finalisation because pods (and their logs) are transient; the Copilot reads these.
    pod_snapshot: Mapped[dict | None] = mapped_column(JsonType)
    logs: Mapped[str | None] = mapped_column(Text)

    workload: Mapped[Workload] = relationship(back_populates="startups")


class K8sEvent(Base):
    """Kubernetes events persisted beyond their ~1 h TTL."""

    __tablename__ = "k8s_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(64), unique=True)
    namespace: Mapped[str] = mapped_column(String(253))
    involved_kind: Mapped[str | None] = mapped_column(String(64))
    involved_name: Mapped[str | None] = mapped_column(String(253))
    involved_uid: Mapped[str | None] = mapped_column(String(64), index=True)
    reason: Mapped[str | None] = mapped_column(String(128))
    type: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JsonType, default=dict)


class Incident(Base):
    """A startup that breached its SLO or regressed against the workload baseline."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    workload_id: Mapped[int] = mapped_column(ForeignKey("workloads.id"), index=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("pod_startups.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open | diagnosed | resolved
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    total_s: Mapped[float] = mapped_column(Float)
    threshold_s: Mapped[float] = mapped_column(Float)
    breach_reason: Mapped[str] = mapped_column(String(32))  # slo | regression
    baseline_s: Mapped[float | None] = mapped_column(Float)
    top_category: Mapped[str | None] = mapped_column(String(64))
    candidates: Mapped[list] = mapped_column(JsonType, default=list)

    workload: Mapped[Workload] = relationship()
    startup: Mapped[PodStartup] = relationship()


class DiagnosisRun(Base):
    """One Incident Copilot run: its status, the validated diagnosis and how it was produced."""

    __tablename__ = "diagnosis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running | completed | failed
    source: Mapped[str | None] = mapped_column(String(32))  # llm | fallback
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(String(16))
    findings: Mapped[list] = mapped_column(JsonType, default=list)
    ruled_out: Mapped[list] = mapped_column(JsonType, default=list)
    recommendations: Mapped[list] = mapped_column(JsonType, default=list)
    validation_errors: Mapped[list] = mapped_column(JsonType, default=list)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    evidence: Mapped[list["Evidence"]] = relationship(back_populates="run", order_by="Evidence.id")


class Evidence(Base):
    """A tool result the Copilot saw. Diagnoses may only cite these by `ref`."""

    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("run_id", "ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("diagnosis_runs.id"), index=True)
    ref: Mapped[str] = mapped_column(String(16))  # E1, E2, ...
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict] = mapped_column(JsonType, default=dict)
    result: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[DiagnosisRun] = relationship(back_populates="evidence")


class LlmCall(Base):
    """Every model call, for LLMOps visibility: latency, tokens, prompt version and failures."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("diagnosis_runs.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(64))  # investigate | synthesise | select_action
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # ok | error
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActionPlan(Base):
    """An allow-listed optimisation proposed for an incident, its policy checks and the human decision."""

    __tablename__ = "action_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    diagnosis_run_id: Mapped[int | None] = mapped_column(ForeignKey("diagnosis_runs.id"))
    category: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JsonType, default=dict)
    rationale: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # llm | fallback
    risk: Mapped[str] = mapped_column(String(16))
    # proposing | awaiting_approval | rejected_by_policy | rejected | running | completed | failed | aborted
    status: Mapped[str] = mapped_column(String(32))
    policy_checks: Mapped[list] = mapped_column(JsonType, default=list)
    diff: Mapped[str] = mapped_column(Text, default="")
    # Exactly what the reviewer approves: baseline/candidate sandbox Deployments, extra objects, ConfigMaps to copy.
    manifests: Mapped[dict] = mapped_column(JsonType, default=dict)
    export: Mapped[dict] = mapped_column(JsonType, default=dict)  # patch + objects for the original workload
    llm_error: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incident: Mapped[Incident] = relationship()


class Experiment(Base):
    """Before/after test of an approved plan in the sandbox namespace."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("action_plans.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running | completed | failed | aborted
    trials_per_arm: Mapped[int] = mapped_column(Integer)
    node: Mapped[str | None] = mapped_column(String(253))
    prep: Mapped[dict] = mapped_column(JsonType, default=dict)  # one-off costs, e.g. prefetch_s
    comparison: Mapped[dict] = mapped_column(JsonType, default=dict)
    verdict: Mapped[str | None] = mapped_column(String(32))  # improved | no_change | regressed | failed
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan: Mapped[ActionPlan] = relationship()
    trials: Mapped[list["Trial"]] = relationship(back_populates="experiment", order_by="Trial.index")


class Trial(Base):
    __tablename__ = "trials"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    arm: Mapped[str] = mapped_column(String(16))  # baseline | candidate
    warmup: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16))  # ok | failed
    pod_name: Mapped[str | None] = mapped_column(String(253))
    startup_id: Mapped[int | None] = mapped_column(ForeignKey("pod_startups.id"))
    total_s: Mapped[float | None] = mapped_column(Float)
    phases: Mapped[dict] = mapped_column(JsonType, default=dict)  # top-level phase -> seconds
    guardrails: Mapped[dict] = mapped_column(JsonType, default=dict)
    prep_s: Mapped[float | None] = mapped_column(Float)  # per-trial preparation, e.g. image pre-pull
    error: Mapped[str | None] = mapped_column(Text)

    experiment: Mapped[Experiment] = relationship(back_populates="trials")


class AuditLog(Base):
    """Append-only record of every decision and every cluster or node change KubeSage makes."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(128))  # kubesage | a user name
    event: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[dict] = mapped_column(JsonType, default=dict)
