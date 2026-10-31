"""Removes secrets from text and pod specs before they are stored or sent to an LLM."""

import re

REDACTED = "[REDACTED]"

SECRET_NAME = (
    r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)[\w.-]*"
)
SECRET_NAME_RE = re.compile(SECRET_NAME, re.IGNORECASE)

PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*"), f"Bearer {REDACTED}"),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), REDACTED),  # JWT
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),  # AWS access key id
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), REDACTED),  # Google API key
    (re.compile(r"(?i)\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}"), REDACTED),  # common provider key formats
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), rf"\1{REDACTED}@"),  # URL credentials
    (re.compile(rf"(?i)\b({SECRET_NAME})(\"?\s*[:=]\s*\"?)([^\s\",;]+)"), rf"\1\2{REDACTED}"),  # key=value
]


def redact_text(text: str | None) -> str | None:
    if not text:
        return text
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_env(env: list[dict]) -> list[dict]:
    """Container env for display: secret-looking values hidden, secret references shown by name only."""
    safe = []
    for item in env or []:
        name = item.get("name", "")
        if "valueFrom" in item:
            source = item["valueFrom"]
            if "secretKeyRef" in source:
                ref = source["secretKeyRef"]
                safe.append({"name": name, "valueFrom": f"secret {ref.get('name')}/{ref.get('key')}"})
            else:
                safe.append({"name": name, "valueFrom": next(iter(source), "unknown")})
        elif SECRET_NAME_RE.fullmatch(name):
            safe.append({"name": name, "value": REDACTED})
        else:
            safe.append({"name": name, "value": redact_text(item.get("value"))})
    return safe
