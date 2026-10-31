import json
from dataclasses import dataclass, field

MAX_RESULT_CHARS = 6000


@dataclass
class EvidenceItem:
    ref: str
    tool: str
    args: dict
    result: dict


@dataclass
class EvidenceStore:
    """Every tool result gets a reference (E1, E2, ...). Diagnoses may only cite references in this store."""

    items: list[EvidenceItem] = field(default_factory=list)

    def add(self, tool: str, args: dict, result: dict) -> str:
        ref = f"E{len(self.items) + 1}"
        self.items.append(EvidenceItem(ref, tool, args, result))
        return ref

    @property
    def refs(self) -> set[str]:
        return {item.ref for item in self.items}

    def get(self, ref: str) -> EvidenceItem | None:
        return next((i for i in self.items if i.ref == ref), None)

    def digest(self) -> str:
        """All evidence as text for the synthesis prompt."""
        return "\n\n".join(f"[{i.ref}] {i.tool}({json.dumps(i.args)})\n{render(i.result)}" for i in self.items)


def render(result: dict) -> str:
    text = json.dumps(result, default=str, indent=1)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n... (truncated)"
    return text
