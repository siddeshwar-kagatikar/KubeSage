from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kubesage.db.models import Incident, PodStartup, Workload
from kubesage.db.session import get_session
from kubesage.profiler.timeline import median_or_none
from kubesage.schemas import SloUpdate, StartupDetail, StartupSummary, WorkloadOut

router = APIRouter(prefix="/api", tags=["workloads"])

MEDIAN_WINDOW = 20


def startup_summary(session: Session, startup: PodStartup, detail: bool = False) -> StartupSummary | StartupDetail:
    incident_id = session.scalar(select(Incident.id).where(Incident.startup_id == startup.id))
    model = StartupDetail if detail else StartupSummary
    return model.model_validate(startup, from_attributes=True).model_copy(update={"incident_id": incident_id})


def workload_out(session: Session, workload: Workload) -> WorkloadOut:
    count = session.scalar(select(func.count()).where(PodStartup.workload_id == workload.id))
    totals = session.scalars(
        select(PodStartup.total_s)
        .where(PodStartup.workload_id == workload.id, PodStartup.total_s.is_not(None))
        .order_by(PodStartup.created_at.desc())
        .limit(MEDIAN_WINDOW)
    ).all()
    open_incidents = session.scalar(
        select(func.count()).where(Incident.workload_id == workload.id, Incident.status == "open")
    )
    last = session.scalar(
        select(PodStartup).where(PodStartup.workload_id == workload.id).order_by(PodStartup.created_at.desc()).limit(1)
    )
    return WorkloadOut(
        id=workload.id,
        namespace=workload.namespace,
        kind=workload.kind,
        name=workload.name,
        slo_seconds=workload.slo_seconds,
        startup_count=count,
        median_total_s=median_or_none(list(totals)),
        open_incidents=open_incidents,
        last_startup=startup_summary(session, last) if last else None,
    )


@router.get("/workloads", response_model=list[WorkloadOut])
def list_workloads(session: Session = Depends(get_session)):
    workloads = session.scalars(select(Workload).order_by(Workload.namespace, Workload.name)).all()
    return [workload_out(session, w) for w in workloads]


@router.put("/workloads/{workload_id}/slo", response_model=WorkloadOut)
def update_slo(workload_id: int, body: SloUpdate, session: Session = Depends(get_session)):
    workload = session.get(Workload, workload_id)
    if workload is None:
        raise HTTPException(404, "workload not found")
    if body.slo_seconds <= 0:
        raise HTTPException(422, "slo_seconds must be positive")
    workload.slo_seconds = body.slo_seconds
    session.commit()
    return workload_out(session, workload)


@router.get("/workloads/{workload_id}/startups", response_model=list[StartupSummary])
def list_startups(workload_id: int, limit: int = 50, session: Session = Depends(get_session)):
    if session.get(Workload, workload_id) is None:
        raise HTTPException(404, "workload not found")
    startups = session.scalars(
        select(PodStartup)
        .where(PodStartup.workload_id == workload_id)
        .order_by(PodStartup.created_at.desc())
        .limit(min(limit, 200))
    ).all()
    return [startup_summary(session, s) for s in startups]


@router.get("/startups/{startup_id}", response_model=StartupDetail)
def get_startup(startup_id: int, session: Session = Depends(get_session)):
    startup = session.get(PodStartup, startup_id)
    if startup is None:
        raise HTTPException(404, "startup not found")
    return startup_summary(session, startup, detail=True)
