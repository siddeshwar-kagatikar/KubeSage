"""Everything the Copilot may look at for one incident, loaded from persisted data (pods are transient)."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from kubesage.db.models import DiagnosisRun, Incident, K8sEvent, PodStartup

HISTORY_LIMIT = 10
PAST_INCIDENT_LIMIT = 5


@dataclass
class IncidentContext:
    incident_id: int
    workload: dict
    incident: dict
    startup: dict
    pod_snapshot: dict | None = None
    events: list[dict] = field(default_factory=list)
    logs: str | None = None
    history: list[dict] = field(default_factory=list)
    past_incidents: list[dict] = field(default_factory=list)

    @property
    def top_candidate(self) -> dict | None:
        candidates = self.incident.get("candidates") or []
        return candidates[0] if candidates else None


def load_context(session: Session, incident_id: int) -> IncidentContext:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise LookupError(f"incident {incident_id} not found")
    startup = incident.startup
    workload = incident.workload

    events = session.scalars(
        select(K8sEvent).where(K8sEvent.involved_uid == startup.pod_uid).order_by(K8sEvent.first_seen)
    ).all()
    history = session.scalars(
        select(PodStartup)
        .where(PodStartup.workload_id == workload.id, PodStartup.status == "ready", PodStartup.id != startup.id)
        .order_by(PodStartup.created_at.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    past = session.execute(
        select(Incident, DiagnosisRun)
        .join(DiagnosisRun, DiagnosisRun.incident_id == Incident.id)
        .where(Incident.id != incident.id, DiagnosisRun.status == "completed", DiagnosisRun.source == "llm")
        .order_by(DiagnosisRun.finished_at.desc())
        .limit(PAST_INCIDENT_LIMIT * 4)
    ).all()

    return IncidentContext(
        incident_id=incident.id,
        workload={
            "namespace": workload.namespace,
            "kind": workload.kind,
            "name": workload.name,
            "slo_seconds": workload.slo_seconds,
        },
        incident={
            "total_s": incident.total_s,
            "threshold_s": incident.threshold_s,
            "breach_reason": incident.breach_reason,
            "baseline_s": incident.baseline_s,
            "top_category": incident.top_category,
            "candidates": incident.candidates,
        },
        startup={
            "pod_name": startup.pod_name,
            "namespace": startup.namespace,
            "node": startup.node,
            "image": startup.image,
            "created_at": startup.created_at.isoformat(),
            "ready_at": startup.ready_at.isoformat() if startup.ready_at else None,
            "total_s": startup.total_s,
            "cold": startup.cold,
            "phases": startup.phases,
            "signals": startup.signals,
        },
        pod_snapshot=startup.pod_snapshot,
        events=[
            {
                "reason": e.reason,
                "type": e.type,
                "message": e.message,
                "count": e.count,
                "first_seen": e.first_seen.isoformat() if e.first_seen else None,
                "last_seen": e.last_seen.isoformat() if e.last_seen else None,
            }
            for e in events
        ],
        logs=startup.logs,
        history=[
            {
                "pod_name": s.pod_name,
                "created_at": s.created_at.isoformat(),
                "total_s": s.total_s,
                "cold": s.cold,
                "top_category": s.top_category,
                "pull_s": (s.signals or {}).get("pull_s"),
                "image": s.image,
            }
            for s in history
        ],
        past_incidents=_dedupe_past(past),
    )


def _dedupe_past(rows) -> list[dict]:
    """Latest completed LLM diagnosis per past incident."""
    seen, result = set(), []
    for incident, run in rows:
        if incident.id in seen:
            continue
        seen.add(incident.id)
        result.append(
            {
                "incident_id": incident.id,
                "workload": incident.workload.name,
                "detected_at": incident.detected_at.isoformat(),
                "total_s": incident.total_s,
                "category": run.category,
                "summary": run.summary,
                "recommendations": [r.get("action") for r in run.recommendations],
                "status": incident.status,
            }
        )
        if len(result) >= PAST_INCIDENT_LIMIT:
            break
    return result
