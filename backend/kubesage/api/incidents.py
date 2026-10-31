from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from kubesage.db.models import Incident
from kubesage.db.session import get_session
from kubesage.schemas import IncidentOut

router = APIRouter(prefix="/api", tags=["incidents"])


def incident_out(incident: Incident) -> IncidentOut:
    return IncidentOut(
        id=incident.id,
        workload_id=incident.workload_id,
        workload_name=incident.workload.name,
        namespace=incident.workload.namespace,
        startup_id=incident.startup_id,
        pod_name=incident.startup.pod_name,
        status=incident.status,
        detected_at=incident.detected_at,
        total_s=incident.total_s,
        threshold_s=incident.threshold_s,
        breach_reason=incident.breach_reason,
        baseline_s=incident.baseline_s,
        top_category=incident.top_category,
        candidates=incident.candidates,
    )


@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(status: str | None = None, limit: int = 100, session: Session = Depends(get_session)):
    query = select(Incident).order_by(Incident.detected_at.desc()).limit(min(limit, 500))
    if status:
        query = query.where(Incident.status == status)
    return [incident_out(i) for i in session.scalars(query).all()]


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
def get_incident(incident_id: int, session: Session = Depends(get_session)):
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    return incident_out(incident)
