"""Structured output the Copilot must produce. Every claim carries evidence references (E1, E2, ...)."""

from typing import Literal

from pydantic import BaseModel, Field

DiagnosisCategory = Literal[
    "large_image", "slow_registry", "model_loading", "app_init", "capacity", "slow_readiness", "unknown"
]


class Finding(BaseModel):
    statement: str = Field(description="One factual observation, with numbers where available.")
    evidence_ids: list[str] = Field(description="Evidence references that support this statement, e.g. ['E1', 'E4'].")


class RuledOut(BaseModel):
    category: DiagnosisCategory
    reason: str
    evidence_ids: list[str]


class Recommendation(BaseModel):
    action: str = Field(description="A concrete fix, preferably one named in the runbook.")
    rationale: str
    evidence_ids: list[str]


class DiagnosisDraft(BaseModel):
    category: DiagnosisCategory = Field(description="Root-cause category of the slow startup.")
    summary: str = Field(description="Two to four sentences explaining what happened and why.")
    confidence: Literal["high", "medium", "low"]
    findings: list[Finding] = Field(description="Supporting observations, each citing evidence.")
    ruled_out: list[RuledOut] = Field(default_factory=list, description="Alternative causes considered and rejected.")
    recommendations: list[Recommendation] = Field(default_factory=list)
