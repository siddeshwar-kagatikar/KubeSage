"""Plan lifecycle: propose (agent + policy), approve (re-check, then experiment) or reject. Every step is audited."""

from kubernetes import client
from sqlalchemy import select

from kubesage.audit import audit
from kubesage.collectors.kube import new_api_client, to_dict
from kubesage.config import Settings
from kubesage.db.models import ActionPlan, DiagnosisRun, Incident, utcnow
from kubesage.db.session import session_scope
from kubesage.experiments.runner import ExperimentService
from kubesage.llm.calls import CallLogger
from kubesage.llm.provider import create_chat_model, create_model_or_placeholder
from kubesage.optimizer import planner
from kubesage.optimizer.planner import PROMPT_VERSION, Check, NoAutomatedAction
from kubesage.optimizer.registry import Registry


class PlanStateError(RuntimeError):
    pass


class OptimizerService:
    def __init__(self, settings: Settings, experiments: ExperimentService, model_factory=create_chat_model):
        self.settings = settings
        self.experiments = experiments
        self.model_factory = model_factory
        self.registry = Registry(settings.registry_url)

    def create_plan(self, incident_id: int) -> int:
        settings = self.settings
        with session_scope() as session:
            incident = session.get(Incident, incident_id)
            if incident is None:
                raise LookupError(f"incident {incident_id} not found")
            run = session.scalar(
                select(DiagnosisRun)
                .where(DiagnosisRun.incident_id == incident_id, DiagnosisRun.status == "completed")
                .order_by(DiagnosisRun.id.desc())
                .limit(1)
            )
            category = run.category if run and run.category != "unknown" else incident.top_category
            diagnosis = (
                {
                    "category": run.category,
                    "source": run.source,
                    "summary": run.summary,
                    "recommendations": run.recommendations,
                }
                if run
                else {"category": incident.top_category, "source": "detectors", "candidates": incident.candidates}
            )
            workload = incident.workload
            namespace, name, kind = workload.namespace, workload.name, workload.kind
            signals = incident.startup.signals
            run_id = run.id if run else None
        if not category:
            raise NoAutomatedAction("unknown")
        if kind != "Deployment":
            raise NoAutomatedAction(f"{category} on a {kind} (only Deployments are supported)")

        api = new_api_client(settings.kube_context)
        deployment = to_dict(api, client.AppsV1Api(api).read_namespaced_deployment(name, namespace))

        with session_scope() as session:
            plan = ActionPlan(
                incident_id=incident_id,
                diagnosis_run_id=run_id,
                category=category,
                action="",
                rationale="",
                source="",
                risk="",
                status="proposing",
            )
            session.add(plan)
            session.flush()
            plan_id = plan.id
        calls = CallLogger(None, settings.llm_provider, settings.llm_model, PROMPT_VERSION)
        try:
            proposal, prepared, checks = planner.build_plan(
                plan_id=plan_id,
                category=category,
                signals=signals,
                diagnosis=diagnosis,
                deployment=deployment,
                workload_kind=kind,
                model=create_model_or_placeholder(settings, self.model_factory),
                calls=calls,
                registry=self.registry,
                settings=settings,
                api_client=api,
                experiment_running=self.experiments.is_running(),
            )
        except Exception:
            with session_scope() as session:
                session.delete(session.get(ActionPlan, plan_id))
            raise

        passed = all(c.passed for c in checks)
        with session_scope() as session:
            plan = session.get(ActionPlan, plan_id)
            plan.action = proposal.action.name
            plan.params = proposal.params.model_dump()
            plan.rationale = proposal.rationale
            plan.source = proposal.source
            plan.llm_error = proposal.llm_error
            plan.risk = proposal.action.risk
            plan.policy_checks = [c.to_dict() for c in checks]
            plan.status = "awaiting_approval" if passed else "rejected_by_policy"
            if prepared:
                plan.diff = prepared.diff
                plan.export = prepared.export
                plan.manifests = {
                    "baseline": prepared.baseline,
                    "candidate": prepared.candidate,
                    "objects": prepared.changes.objects,
                    "per_trial_objects": prepared.changes.per_trial_objects,
                    "configmaps": prepared.configmaps,
                }
        audit(
            "plan.proposed" if passed else "plan.rejected_by_policy",
            f"plan {plan_id}",
            incident_id=incident_id,
            action=proposal.action.name,
            params=proposal.params.model_dump(),
            source=proposal.source,
            failed_checks=[c.name for c in checks if not c.passed],
        )
        return plan_id

    def approve(self, plan_id: int, approver: str) -> int:
        """Re-run the checks that can change after review (lab mode, running experiment, dry-run), then start."""
        with session_scope() as session:
            plan = session.get(ActionPlan, plan_id)
            if plan is None:
                raise LookupError(f"plan {plan_id} not found")
            if plan.status != "awaiting_approval":
                raise PlanStateError(f"plan {plan_id} is {plan.status}, not awaiting approval")
            manifests = plan.manifests
        api = new_api_client(self.settings.kube_context)
        objects = [manifests["candidate"], *manifests["objects"], *manifests["per_trial_objects"]]
        checks = [
            Check("lab_mode_enabled", self.settings.lab_mode, "cold-start trials evict images on the sandbox node"),
            Check("no_experiment_running", not self.experiments.is_running(), "one experiment at a time"),
            planner.dry_run(api, objects),
        ]
        if not all(c.passed for c in checks):
            audit("plan.approval_blocked", f"plan {plan_id}", actor=approver, checks=[c.to_dict() for c in checks])
            raise PlanStateError("; ".join(f"{c.name}: {c.detail}" for c in checks if not c.passed))

        with session_scope() as session:
            plan = session.get(ActionPlan, plan_id)
            if plan.status != "awaiting_approval":  # decided concurrently
                raise PlanStateError(f"plan {plan_id} is {plan.status}, not awaiting approval")
            plan.status, plan.decided_by, plan.decided_at = "running", approver, utcnow()
        audit("plan.approved", f"plan {plan_id}", actor=approver)
        try:
            return self.experiments.start(plan_id)
        except Exception:
            with session_scope() as session:
                session.get(ActionPlan, plan_id).status = "awaiting_approval"
            raise

    def reject(self, plan_id: int, approver: str, note: str | None) -> None:
        with session_scope() as session:
            plan = session.get(ActionPlan, plan_id)
            if plan is None:
                raise LookupError(f"plan {plan_id} not found")
            if plan.status != "awaiting_approval":
                raise PlanStateError(f"plan {plan_id} is {plan.status}, not awaiting approval")
            plan.status, plan.decided_by, plan.decided_at, plan.decision_note = "rejected", approver, utcnow(), note
        audit("plan.rejected", f"plan {plan_id}", actor=approver, note=note)
