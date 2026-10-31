import json
from pathlib import Path

import pytest

from kubesage.collectors.jaeger import spans_from_otlp
from kubesage.config import Settings
from kubesage.profiler.timeline import Timeline, build_timeline

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> tuple[dict, list[dict], dict]:
    folder = FIXTURES / name
    pod = json.loads((folder / "pod.json").read_text(encoding="utf-8"))
    events = json.loads((folder / "events.json").read_text(encoding="utf-8"))["items"]
    trace = json.loads((folder / "trace.json").read_text(encoding="utf-8"))
    return pod, events, trace


def timeline_for(name: str) -> Timeline:
    pod, events, trace = load_fixture(name)
    return build_timeline(pod, events, spans_from_otlp(trace, pod["metadata"]["name"]))


@pytest.fixture
def settings() -> Settings:
    # Defaults only: ignore any local .env so tests are deterministic.
    return Settings(_env_file=None)
