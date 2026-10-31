"""Incident Copilot as a LangGraph state machine.

    investigate <-> tools   bounded tool loop (model decides which read-only tools to call)
         |
    synthesise -> validate  structured diagnosis; rejected drafts get one retry with the errors as feedback
         |            |
       done        fallback deterministic diagnosis from the detectors if the LLM fails or stays invalid

Synthesis is a separate call from the tool loop and sees an evidence digest rather than the raw transcript.
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from kubesage.config import Settings
from kubesage.copilot import prompts
from kubesage.copilot.context import IncidentContext
from kubesage.copilot.evidence import EvidenceStore, render
from kubesage.copilot.models import DiagnosisDraft, Finding
from kubesage.copilot.tools import detector_result, timeline_result
from kubesage.copilot.validation import validate
from kubesage.llm.calls import CallLogger

log = logging.getLogger(__name__)


class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    tool_calls_made: int
    conclusion: str
    draft: DiagnosisDraft | None
    validation_errors: list[str]
    synthesis_attempts: int
    llm_error: str | None
    source: str  # llm | fallback


@dataclass
class DiagnosisResult:
    draft: DiagnosisDraft
    source: str
    evidence: EvidenceStore
    tool_calls: int
    validation_errors: list[str] = field(default_factory=list)
    llm_error: str | None = None


def message_text(message: AIMessage) -> str:
    """Plain text of a model reply; content may be a string or a list of content blocks."""
    if isinstance(message.content, str):
        return message.content
    return "".join(
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in message.content
        if not isinstance(block, dict) or block.get("type") in (None, "text")
    )


def seed_evidence(ctx: IncidentContext, evidence: EvidenceStore) -> tuple[str, str]:
    """E1 (timeline) and E2 (detectors) are always collected up front; it saves two model round trips."""
    timeline, detectors = timeline_result(ctx), detector_result(ctx)
    evidence.add("get_startup_timeline", {}, timeline)
    evidence.add("get_detector_findings", {}, detectors)
    return render(timeline), render(detectors)


def fallback_draft(ctx: IncidentContext) -> DiagnosisDraft:
    """Deterministic diagnosis from the detectors, citing the seeded evidence only."""
    top = ctx.top_candidate
    if top is None:
        return DiagnosisDraft(
            category="unknown",
            summary="No detector explained the slow startup and no validated AI diagnosis is available.",
            confidence="low",
            findings=[Finding(statement=f"Startup took {ctx.incident['total_s']:.1f} s.", evidence_ids=["E1"])],
        )
    findings = [Finding(statement=c["summary"], evidence_ids=["E1", "E2"]) for c in ctx.incident["candidates"]]
    return DiagnosisDraft(
        category=top["category"],
        summary=f"{top['summary']} (Deterministic detector result; no validated AI diagnosis was produced.)",
        confidence="medium" if top["score"] >= 0.6 else "low",
        findings=findings,
    )


def run_copilot(
    ctx: IncidentContext,
    model,
    tools: list[BaseTool],
    evidence: EvidenceStore,
    calls: CallLogger,
    settings: Settings,
) -> DiagnosisResult:
    """`tools` must write to `evidence`; E1/E2 are seeded here before the graph runs."""
    timeline_text, detectors_text = seed_evidence(ctx, evidence)
    tools_by_name = {t.name: t for t in tools}
    tool_model = model.bind_tools(tools)
    synth_model = model.with_structured_output(DiagnosisDraft, include_raw=True)
    max_calls = settings.copilot_max_tool_calls
    inc, wl = ctx.incident, ctx.workload
    baseline = f", baseline {inc['baseline_s']:.1f} s" if inc.get("baseline_s") else ""

    def investigate(state: State) -> State:
        try:
            response = calls.call("investigate", lambda: tool_model.invoke(state["messages"]))
        except Exception as exc:
            log.warning("investigation call failed: %s", exc)
            return {"llm_error": f"{type(exc).__name__}: {exc}"}
        update: State = {"messages": [response]}
        if not response.tool_calls:
            update["conclusion"] = message_text(response)
        return update

    def after_investigate(state: State) -> str:
        if state.get("llm_error"):
            return "fallback"
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and state.get("tool_calls_made", 0) < max_calls:
            return "tools"
        return "synthesise"

    def run_tools(state: State) -> State:
        made = state.get("tool_calls_made", 0)
        replies = []
        for call in state["messages"][-1].tool_calls:
            if made >= max_calls:
                content = "Tool budget exhausted; no more tool calls will be executed. Conclude now."
            elif call["name"] not in tools_by_name:
                content = f"Unknown tool {call['name']!r}."
            else:
                try:
                    content = tools_by_name[call["name"]].invoke(call["args"])
                except Exception as exc:  # bad arguments from the model: report back, do not crash the run
                    content = f"Tool error: {exc}"
                made += 1
            replies.append(ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]))
        return {"messages": replies, "tool_calls_made": made}

    def after_tools(state: State) -> str:
        return "investigate" if state.get("tool_calls_made", 0) < max_calls else "synthesise"

    def synthesise(state: State) -> State:
        errors = state.get("validation_errors") or []
        feedback = prompts.FEEDBACK.format(errors="\n".join(f"- {e}" for e in errors)) if errors else ""
        user = prompts.SYNTHESISE_USER.format(
            incident_id=ctx.incident_id,
            namespace=wl["namespace"],
            workload=wl["name"],
            total_s=inc["total_s"],
            breach_reason=inc["breach_reason"],
            threshold_s=inc["threshold_s"],
            top_category=inc["top_category"] or "none",
            conclusion=state.get("conclusion") or "(investigation ended without a written conclusion)",
            evidence=evidence.digest(),
            feedback=feedback,
        )
        attempts = state.get("synthesis_attempts", 0) + 1
        try:
            output = calls.call(
                "synthesise",
                lambda: synth_model.invoke([SystemMessage(prompts.SYNTHESISE_SYSTEM), HumanMessage(user)]),
            )
        except Exception as exc:
            log.warning("synthesis call failed: %s", exc)
            return {"synthesis_attempts": attempts, "llm_error": f"{type(exc).__name__}: {exc}"}
        parsed, error = output.get("parsed"), output.get("parsing_error")
        if parsed is None:
            return {
                "synthesis_attempts": attempts,
                "draft": None,
                "validation_errors": [f"output did not parse: {error}"],
            }
        return {"synthesis_attempts": attempts, "draft": parsed, "validation_errors": []}

    def check(state: State) -> State:
        draft = state.get("draft")
        if draft is None:
            return {}
        return {"validation_errors": validate(draft, evidence.refs, ctx.top_candidate)}

    def after_check(state: State) -> str:
        if state.get("llm_error"):
            return "fallback"
        if state.get("draft") is not None and not state.get("validation_errors"):
            return "accept"
        if state.get("synthesis_attempts", 0) < settings.copilot_max_synthesis_attempts:
            return "synthesise"
        return "fallback"

    def accept(state: State) -> State:
        return {"source": "llm"}

    def fallback(state: State) -> State:
        return {"draft": fallback_draft(ctx), "source": "fallback"}

    graph = StateGraph(State)
    graph.add_node("investigate", investigate)
    graph.add_node("tools", run_tools)
    graph.add_node("synthesise", synthesise)
    graph.add_node("validate", check)
    graph.add_node("accept", accept)
    graph.add_node("fallback", fallback)
    graph.add_edge(START, "investigate")
    graph.add_conditional_edges("investigate", after_investigate, ["tools", "synthesise", "fallback"])
    graph.add_conditional_edges("tools", after_tools, ["investigate", "synthesise"])
    graph.add_edge("synthesise", "validate")
    graph.add_conditional_edges("validate", after_check, ["accept", "synthesise", "fallback"])
    graph.add_edge("accept", END)
    graph.add_edge("fallback", END)

    initial: State = {
        "messages": [
            SystemMessage(prompts.INVESTIGATE_SYSTEM.format(max_tool_calls=max_calls)),
            HumanMessage(
                prompts.INVESTIGATE_USER.format(
                    incident_id=ctx.incident_id,
                    namespace=wl["namespace"],
                    workload=wl["name"],
                    total_s=inc["total_s"],
                    breach_reason=inc["breach_reason"],
                    threshold_s=inc["threshold_s"],
                    baseline=baseline,
                    timeline=timeline_text,
                    detectors=detectors_text,
                )
            ),
        ],
        "tool_calls_made": 0,
        "synthesis_attempts": 0,
    }
    # Each investigate/tools round is two steps; leave headroom for synthesis retries.
    final = graph.compile().invoke(initial, {"recursion_limit": 4 * max_calls + 20})
    return DiagnosisResult(
        draft=final["draft"],
        source=final["source"],
        evidence=evidence,
        tool_calls=final.get("tool_calls_made", 0),
        validation_errors=final.get("validation_errors") or [],
        llm_error=final.get("llm_error"),
    )
