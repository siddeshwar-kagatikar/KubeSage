import json

from fastapi import APIRouter, Depends, HTTPException, Request
from kubernetes import client
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from kubesage.config import REPO_ROOT
from kubesage.db.models import ActionPlan, AuditLog, Experiment, Incident, LlmCall
from kubesage.db.session import get_session
from kubesage.experiments.runner import ExperimentInProgress, ExperimentService
from kubesage.optimizer.catalog import ACTIONS
from kubesage.optimizer.planner import NoAutomatedAction
from kubesage.optimizer.service import OptimizerService, PlanStateError
from kubesage.schemas import ActionPlanOut, AuditOut, DecisionIn, ExperimentOut, LlmOpsOut, LlmUsageOut, TrialOut

router = APIRouter(prefix="/api", tags=["optimizer"])

EVAL_RESULTS = REPO_ROOT / "backend" / "evals" / "results"


def plan_out(session: Session, plan: ActionPlan) -> ActionPlanOut:
    action = ACTIONS.get(plan.action)
    experiment_id = session.scalar(select(Experiment.id).where(Experiment.plan_id == plan.id))
    return ActionPlanOut(
        id=plan.id,
        incident_id=plan.incident_id,
        diagnosis_run_id=plan.diagnosis_run_id,
        category=plan.category,
        action=plan.action,
        action_title=action.title if action else plan.action,
        action_note=action.note if action else "",
        params=plan.params,
        rationale=plan.rationale,
        source=plan.source,
        llm_error=plan.llm_error,
        risk=plan.risk,
        status=plan.status,
        policy_checks=plan.policy_checks,
        diff=plan.diff,
        export=plan.export,
        decided_by=plan.decided_by,
        decision_note=plan.decision_note,
        decided_at=plan.decided_at,
        created_at=plan.created_at,
        experiment_id=experiment_id,
    )


def experiment_out(experiment: Experiment) -> ExperimentOut:
    action = ACTIONS[experiment.plan.action]
    return ExperimentOut(
        id=experiment.id,
        plan_id=experiment.plan_id,
        incident_id=experiment.plan.incident_id,
        action=action.name,
        action_note=action.note,
        status=experiment.status,
        trials_per_arm=experiment.trials_per_arm,
        planned_trials=1 + 2 * experiment.trials_per_arm,
        node=experiment.node,
        prep=experiment.prep,
        comparison=experiment.comparison,
        verdict=experiment.verdict,
        error=experiment.error,
        started_at=experiment.started_at,
        finished_at=experiment.finished_at,
        trials=[TrialOut.model_validate(t) for t in experiment.trials],
    )


def _optimizer(request: Request) -> OptimizerService:
    return request.app.state.optimizer


def _experiments(request: Request) -> ExperimentService:
    return request.app.state.experiments


@router.post("/incidents/{incident_id}/plans", response_model=ActionPlanOut, status_code=201)
def propose_plan(incident_id: int, request: Request, session: Session = Depends(get_session)):
    if session.get(Incident, incident_id) is None:
        raise HTTPException(404, "incident not found")
    try:
        plan_id = _optimizer(request).create_plan(incident_id)
    except NoAutomatedAction as exc:
        raise HTTPException(
            422, f"No automated action for {exc}: follow the runbook recommendations in the diagnosis."
        ) from exc
    except client.ApiException as exc:
        raise HTTPException(502, f"Kubernetes API error reading the workload: {exc.status} {exc.reason}") from exc
    return plan_out(session, session.get(ActionPlan, plan_id))


@router.get("/incidents/{incident_id}/plans", response_model=list[ActionPlanOut])
def list_plans(incident_id: int, session: Session = Depends(get_session)):
    plans = session.scalars(
        select(ActionPlan)
        .where(ActionPlan.incident_id == incident_id, ActionPlan.status != "proposing")
        .order_by(ActionPlan.id.desc())
    ).all()
    return [plan_out(session, p) for p in plans]


@router.get("/plans/{plan_id}", response_model=ActionPlanOut)
def get_plan(plan_id: int, session: Session = Depends(get_session)):
    plan = session.get(ActionPlan, plan_id)
    if plan is None or plan.status == "proposing":
        raise HTTPException(404, "plan not found")
    return plan_out(session, plan)


@router.post("/plans/{plan_id}/approve", response_model=ExperimentOut, status_code=202)
def approve_plan(plan_id: int, body: DecisionIn, request: Request, session: Session = Depends(get_session)):
    try:
        experiment_id = _optimizer(request).approve(plan_id, body.approver.strip())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (PlanStateError, ExperimentInProgress) as exc:
        raise HTTPException(409, str(exc)) from exc
    return experiment_out(session.get(Experiment, experiment_id))


@router.post("/plans/{plan_id}/reject", response_model=ActionPlanOut)
def reject_plan(plan_id: int, body: DecisionIn, request: Request, session: Session = Depends(get_session)):
    try:
        _optimizer(request).reject(plan_id, body.approver.strip(), body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PlanStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.expire_all()
    return plan_out(session, session.get(ActionPlan, plan_id))


@router.get("/experiments", response_model=list[ExperimentOut])
def list_experiments(limit: int = 50, session: Session = Depends(get_session)):
    experiments = session.scalars(select(Experiment).order_by(Experiment.id.desc()).limit(min(limit, 200))).all()
    return [experiment_out(e) for e in experiments]


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(experiment_id: int, session: Session = Depends(get_session)):
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, "experiment not found")
    return experiment_out(experiment)


@router.post("/experiments/{experiment_id}/abort", status_code=202)
def abort_experiment(experiment_id: int, body: DecisionIn, request: Request):
    if not _experiments(request).abort(experiment_id, body.approver.strip()):
        raise HTTPException(409, "experiment is not running")
    return {"aborting": True}


@router.get("/audit", response_model=list[AuditOut])
def list_audit(limit: int = 200, session: Session = Depends(get_session)):
    rows = session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))).all()
    return [AuditOut.model_validate(r) for r in rows]


@router.get("/llmops/summary", response_model=LlmOpsOut)
def llmops_summary(session: Session = Depends(get_session)):
    rows = session.execute(
        select(
            LlmCall.purpose,
            LlmCall.model,
            func.count(LlmCall.id),
            func.sum(case((LlmCall.status == "error", 1), else_=0)),
            func.coalesce(func.sum(LlmCall.input_tokens), 0),
            func.coalesce(func.sum(LlmCall.output_tokens), 0),
            func.avg(LlmCall.latency_ms),
        ).group_by(LlmCall.purpose, LlmCall.model)
    ).all()
    usage = [
        LlmUsageOut(
            purpose=p, model=m, calls=c, errors=e, input_tokens=i, output_tokens=o, avg_latency_ms=int(lat or 0)
        )
        for p, m, c, e, i, o, lat in rows
    ]
    files = sorted(EVAL_RESULTS.glob("*.json")) if EVAL_RESULTS.exists() else []
    latest = json.loads(files[-1].read_text(encoding="utf-8")) | {"file": files[-1].name} if files else None
    return LlmOpsOut(usage=usage, latest_eval=latest)
