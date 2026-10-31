from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings. Read from the environment and the repository-level .env file."""

    model_config = SettingsConfigDict(env_prefix="KUBESAGE_", env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = Field(
        "postgresql+psycopg://kubesage:kubesage@localhost:5433/kubesage", validation_alias="DATABASE_URL"
    )

    kube_context: str = "kind-kubesage"
    monitored_namespaces: list[str] = ["demo", "kubesage-sandbox"]
    incident_namespaces: list[str] = ["demo"]
    sandbox_namespace: str = "kubesage-sandbox"
    lab_mode: bool = False
    watchers_enabled: bool = True

    jaeger_url: str = "http://127.0.0.1:30686"
    prometheus_url: str = "http://127.0.0.1:30090"
    # Wait after Ready before reading spans: the app flushes its startup trace asynchronously.
    span_wait_seconds: float = 3.0

    # LLM (pluggable provider; Gemini Flash by default)
    llm_provider: str = "google_genai"
    llm_model: str = "gemini-2.5-flash"
    llm_thinking_budget: int = 1024
    llm_max_retries: int = 3
    llm_timeout_s: float = 90.0
    google_api_key: SecretStr | None = Field(None, validation_alias="GOOGLE_API_KEY")

    # Incident Copilot
    copilot_max_tool_calls: int = 6
    copilot_max_synthesis_attempts: int = 2

    # Incident rules
    default_slo_seconds: float = 30.0
    regression_factor: float = 1.5
    baseline_window: int = 10
    baseline_min_samples: int = 3

    # Detector thresholds
    min_phase_seconds: float = 5.0
    min_phase_share: float = 0.25
    large_image_bytes: int = 300_000_000
    slow_registry_bytes_per_s: float = 5_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
