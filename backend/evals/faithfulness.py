"""Numeric faithfulness of a diagnosis: does every number it states appear in the evidence it cites?

Citation validity (kubesage.copilot.validation) only checks that cited evidence IDs exist. This goes one step
further and catches invented or mis-copied numbers. A stated number is supported when it matches a number in the
cited evidence after rounding to the precision it was written with, or after a unit conversion (fraction -> percent,
bytes -> MB/MiB/KB/GB, ms <-> s).

shortcut: numbers derived by arithmetic (a difference or ratio of two evidence values) count as unsupported. The
rate is therefore a lower bound on faithfulness; audit a sample by hand to estimate the false-flag share.
"""

import re
from dataclasses import dataclass, field

from kubesage.copilot.evidence import EvidenceStore
from kubesage.copilot.models import DiagnosisDraft

# 1,234,567.8 | 12.5 | 7   (a leading "-" is read as a sign; letters or dots directly before a digit disqualify it,
# so identifiers such as "s3" or "v1.2" are not numbers)
NUMBER_RE = re.compile(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
EVIDENCE_REF_RE = re.compile(r"\bE\d+\b")
ISO_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")

# (stated value) == (evidence value) * factor, for each plausible unit conversion
CONVERSIONS = (1.0, 100.0, 1e-3, 1e3, 1e-6, 1e-9, 1 / 1024, 1 / 1024**2, 1 / 1024**3)


@dataclass
class Faithfulness:
    claims: int = 0
    supported: int = 0
    unsupported: list[dict] = field(default_factory=list)

    @property
    def rate(self) -> float | None:
        return self.supported / self.claims if self.claims else None


def numbers_in(text: str) -> list[tuple[float, int]]:
    """(value, decimals as written) for every number in free text, ignoring evidence references like E3."""
    out = []
    for match in NUMBER_RE.finditer(EVIDENCE_REF_RE.sub(" ", text)):
        raw = match.group(0).replace(",", "")
        out.append((float(raw), len(raw.split(".")[1]) if "." in raw else 0))
    return out


def evidence_numbers(value) -> set[float]:
    """Every numeric value in a JSON-like evidence result: numeric leaves plus numbers inside strings."""
    found: set[float] = set()
    if isinstance(value, bool) or value is None:
        return found
    if isinstance(value, int | float):
        found.add(float(value))
    elif isinstance(value, str):
        if not ISO_TIME_RE.match(value):
            found.update(v for v, _ in numbers_in(value))
    elif isinstance(value, dict):
        for item in value.values():
            found |= evidence_numbers(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= evidence_numbers(item)
    return found


def is_supported(stated: float, decimals: int, pool: set[float]) -> bool:
    tolerance = 0.5 * 10**-decimals + 1e-9
    return any(abs(v * factor - stated) <= tolerance for v in pool for factor in CONVERSIONS)


def check(draft: DiagnosisDraft, evidence: EvidenceStore) -> Faithfulness:
    """Score every statement of a draft. Findings, ruled-out reasons and recommendations are checked against the
    evidence they cite; the summary against all evidence."""
    pools = {item.ref: evidence_numbers(item.result) for item in evidence.items}
    everything = set().union(*pools.values()) if pools else set()
    result = Faithfulness()

    def score(where: str, text: str, pool: set[float]) -> None:
        for value, decimals in numbers_in(text):
            result.claims += 1
            if is_supported(value, decimals, pool):
                result.supported += 1
            else:
                result.unsupported.append({"where": where, "number": value, "text": text})

    def cited(ids: list[str]) -> set[float]:
        return set().union(*(pools.get(i, set()) for i in ids)) if ids else set()

    score("summary", draft.summary, everything)
    for finding in draft.findings:
        score("finding", finding.statement, cited(finding.evidence_ids))
    for item in draft.ruled_out:
        score("ruled_out", item.reason, cited(item.evidence_ids))
    for rec in draft.recommendations:
        score("recommendation", f"{rec.action} {rec.rationale}", cited(rec.evidence_ids))
    return result
