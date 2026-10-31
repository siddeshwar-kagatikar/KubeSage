"""Grounding checks applied to every LLM diagnosis before it is accepted."""

from kubesage.copilot.models import DiagnosisDraft

MAX_SUMMARY_CHARS = 1200
STRONG_DETECTOR_SCORE = 0.5


def validate(draft: DiagnosisDraft, evidence_refs: set[str], top_candidate: dict | None) -> list[str]:
    errors = []
    if not draft.summary.strip():
        errors.append("summary is empty")
    elif len(draft.summary) > MAX_SUMMARY_CHARS:
        errors.append(f"summary is longer than {MAX_SUMMARY_CHARS} characters")
    if not draft.findings:
        errors.append("at least one finding is required")

    def check(kind: str, index: int, refs: list[str], required: bool) -> None:
        if required and not refs:
            errors.append(f"{kind} {index + 1} cites no evidence")
        unknown = sorted(set(refs) - evidence_refs)
        if unknown:
            errors.append(f"{kind} {index + 1} cites unknown evidence {', '.join(unknown)}")

    for i, finding in enumerate(draft.findings):
        check("finding", i, finding.evidence_ids, required=True)
    for i, item in enumerate(draft.ruled_out):
        check("ruled_out", i, item.evidence_ids, required=True)
    for i, rec in enumerate(draft.recommendations):
        check("recommendation", i, rec.evidence_ids, required=True)

    if draft.category == "unknown" and draft.confidence != "low":
        errors.append("category 'unknown' must have low confidence")

    if (
        top_candidate
        and top_candidate["score"] >= STRONG_DETECTOR_SCORE
        and draft.category != top_candidate["category"]
        and top_candidate["category"] not in {r.category for r in draft.ruled_out}
    ):
        errors.append(
            f"the top detector candidate '{top_candidate['category']}' explains "
            f"{top_candidate['score']:.0%} of startup; list it under ruled_out with evidence, or choose it"
        )
    return errors
