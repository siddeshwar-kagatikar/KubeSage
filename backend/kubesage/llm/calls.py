"""Times every model call and records it in `llm_calls` (tokens, latency, prompt version, failures)."""

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage

from kubesage.db.models import LlmCall
from kubesage.db.session import session_scope

log = logging.getLogger(__name__)

MAX_RATE_LIMIT_RETRIES = 2
MAX_RETRY_DELAY_S = 65.0
RETRY_IN_RE = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


def per_minute_retry_delay(exc: Exception) -> float | None:
    """Seconds to wait if `exc` is a per-minute rate limit (worth waiting for); None for anything else,
    including daily quota exhaustion, which no short wait will fix."""
    text = str(exc)
    if "RESOURCE_EXHAUSTED" not in text or "PerMinute" not in text:
        return None
    match = RETRY_IN_RE.search(text)
    return min(float(match.group(1)) + 1 if match else MAX_RETRY_DELAY_S, MAX_RETRY_DELAY_S)


def _usage(output: Any) -> tuple[int | None, int | None]:
    message = output.get("raw") if isinstance(output, dict) else output
    usage = getattr(message, "usage_metadata", None) if isinstance(message, AIMessage) else None
    if not usage:
        return None, None
    return usage.get("input_tokens"), usage.get("output_tokens")


class CallLogger:
    """Wraps model invocations for one diagnosis run. `persist=False` keeps records in memory (tests, evals)."""

    def __init__(self, run_id: int | None, provider: str, model: str, prompt_version: str, persist: bool = True):
        self.run_id = run_id
        self.provider = provider
        self.model = model
        self.prompt_version = prompt_version
        self.persist = persist
        self.records: list[dict] = []

    def call(self, purpose: str, invoke: Callable[[], Any]) -> Any:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            started = time.perf_counter()
            try:
                output = invoke()
            except Exception as exc:
                self._record(purpose, started, None, None, "error", f"{type(exc).__name__}: {exc}")
                delay = per_minute_retry_delay(exc)
                if delay is None or attempt == MAX_RATE_LIMIT_RETRIES:
                    raise
                log.info("per-minute rate limit on %s; retrying in %.0f s", purpose, delay)
                time.sleep(delay)
                continue
            input_tokens, output_tokens = _usage(output)
            self._record(purpose, started, input_tokens, output_tokens, "ok", None)
            return output
        raise AssertionError("unreachable")

    def _record(self, purpose, started, input_tokens, output_tokens, status, error) -> None:
        record = {
            "run_id": self.run_id,
            "purpose": purpose,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status": status,
            "error": error,
        }
        self.records.append(record)
        if self.persist:
            try:
                with session_scope() as session:
                    session.add(LlmCall(**record))
            except Exception:  # logging must never break a diagnosis
                log.exception("could not record LLM call")
