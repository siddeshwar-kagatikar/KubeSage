"""Scripted and recording stand-ins for a chat model.

`ScriptedModel` returns pre-defined responses: tool-calling turns as AI messages and synthesis turns as structured
data. Used by unit tests and to replay recorded eval runs without spending API quota.
`RecordingModel` wraps a real model and captures its responses in the same format.
"""

import json
from typing import Any

from langchain_core.messages import AIMessage


def ai_to_record(message: AIMessage) -> dict:
    return {
        "type": "ai",
        "content": message.content if isinstance(message.content, str) else json.dumps(message.content),
        "tool_calls": [{"name": c["name"], "args": c["args"], "id": c.get("id")} for c in message.tool_calls],
    }


def record_to_ai(record: dict, index: int) -> AIMessage:
    calls = [
        {"name": c["name"], "args": c["args"], "id": c.get("id") or f"call_{index}_{i}", "type": "tool_call"}
        for i, c in enumerate(record.get("tool_calls", []))
    ]
    return AIMessage(content=record.get("content", ""), tool_calls=calls)


class _Invoker:
    def __init__(self, fn):
        self._fn = fn

    def invoke(self, value: Any, *args, **kwargs) -> Any:
        return self._fn(value)


class ScriptedModel:
    def __init__(self, records: list[dict]):
        self.records = records
        self.index = 0

    def _next(self, expected: str) -> dict:
        if self.index >= len(self.records):
            raise RuntimeError(f"scripted model exhausted (wanted a {expected!r} response)")
        record = self.records[self.index]
        self.index += 1
        if record["type"] != expected:
            raise RuntimeError(f"scripted response {self.index} is {record['type']!r}, expected {expected!r}")
        return record

    def bind_tools(self, tools, **kwargs) -> _Invoker:
        return _Invoker(lambda _messages: record_to_ai(self._next("ai"), self.index))

    def with_structured_output(self, schema, include_raw: bool = False, **kwargs) -> _Invoker:
        def run(_messages):
            data = self._next("structured")["data"]
            try:
                parsed, error = schema.model_validate(data), None
            except Exception as exc:  # mirror LangChain: parsing errors are returned, not raised
                parsed, error = None, exc
            raw = AIMessage(content=json.dumps(data))
            return {"raw": raw, "parsed": parsed, "parsing_error": error} if include_raw else parsed

        return _Invoker(run)


class RecordingModel:
    def __init__(self, inner):
        self.inner = inner
        self.records: list[dict] = []

    def bind_tools(self, tools, **kwargs) -> _Invoker:
        bound = self.inner.bind_tools(tools, **kwargs)

        def run(messages):
            message = bound.invoke(messages)
            self.records.append(ai_to_record(message))
            return message

        return _Invoker(run)

    def with_structured_output(self, schema, include_raw: bool = False, **kwargs) -> _Invoker:
        structured = self.inner.with_structured_output(schema, include_raw=True, **kwargs)

        def run(messages):
            output = structured.invoke(messages)
            parsed = output.get("parsed")
            data = parsed.model_dump() if parsed is not None else {}
            self.records.append({"type": "structured", "data": data})
            return output if include_raw else parsed

        return _Invoker(run)
