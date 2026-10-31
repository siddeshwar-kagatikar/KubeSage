from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kubesage.copilot.service import CopilotService, DiagnosisInProgress
from kubesage.db.models import DiagnosisRun, Incident, LlmCall
from kubesage.db.session import get_session
from kubesage.schemas import DiagnosisRunOut, EvidenceOut, LlmCallOut

router = APIRouter(prefix="/api", tags=["diagnosis"])


def run_out(session: Session, run: DiagnosisRun) -> DiagnosisRunOut:
    calls, tokens_in, tokens_out = session.execute(
        select(func.count(LlmCall.id), func.sum(LlmCall.input_tokens), func.sum(LlmCall.output_tokens)).where(
            LlmCall.run_id == run.id
        )
    ).one()
    return DiagnosisRunOut(
        id=run.id,
        incident_id=run.incident_id,
        status=run.status,
        source=run.source,
        provider=run.provider,
        model=run.model,
        prompt_version=run.prompt_version,
        category=run.category,
        summary=run.summary,
        confidence=run.confidence,
        findings=run.findings,
        ruled_out=run.ruled_out,
        recommendations=run.recommendations,
        validation_errors=run.validation_errors,
        tool_calls=run.tool_calls,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
        llm_calls=calls,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        evidence=[EvidenceOut.model_validate(e) for e in run.evidence],
    )


@router.post("/incidents/{incident_id}/diagnose", response_model=DiagnosisRunOut, status_code=202)
def start_diagnosis(incident_id: int, request: Request, session: Session = Depends(get_session)):
    copilot: CopilotService = request.app.state.copilot
    if session.get(Incident, incident_id) is None:
        raise HTTPException(404, "incident not found")
    try:
        run_id = copilot.start(incident_id)
    except DiagnosisInProgress as exc:
        raise HTTPException(409, str(exc)) from exc
    return run_out(session, session.get(DiagnosisRun, run_id))


@router.get("/incidents/{incident_id}/diagnosis", response_model=DiagnosisRunOut)
def latest_diagnosis(incident_id: int, session: Session = Depends(get_session)):
    run = session.scalar(
        select(DiagnosisRun).where(DiagnosisRun.incident_id == incident_id).order_by(DiagnosisRun.id.desc()).limit(1)
    )
    if run is None:
        raise HTTPException(404, "no diagnosis for this incident")
    return run_out(session, run)


@router.get("/llm/calls", response_model=list[LlmCallOut])
def list_llm_calls(limit: int = 100, session: Session = Depends(get_session)):
    calls = session.scalars(select(LlmCall).order_by(LlmCall.id.desc()).limit(min(limit, 500))).all()
    return [LlmCallOut.model_validate(c) for c in calls]
