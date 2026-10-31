"""Times every model call and records it in `llm_calls` (tokens, latency, prompt version, failures)."""

import logging
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage

from kubesage.db.models import LlmCall
from kubesage.db.session import session_scope

log = logging.getLogger(__name__)


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
        started = time.perf_counter()
        try:
            output = invoke()
        except Exception as exc:
            self._record(purpose, started, None, None, "error", f"{type(exc).__name__}: {exc}")
            raise
        input_tokens, output_tokens = _usage(output)
        self._record(purpose, started, input_tokens, output_tokens, "ok", None)
        return output

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
