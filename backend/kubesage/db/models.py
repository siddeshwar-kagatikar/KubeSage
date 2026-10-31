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
