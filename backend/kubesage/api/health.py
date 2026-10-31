import httpx
from fastapi import APIRouter, Depends, Request
from kubernetes import client
from sqlalchemy import text
from sqlalchemy.orm import Session

from kubesage.collectors.kube import new_api_client
from kubesage.config import get_settings
from kubesage.db.session import get_session
from kubesage.schemas import HealthOut

router = APIRouter(prefix="/api", tags=["health"])


def _database_ok(session: Session) -> bool:
    try:
        session.execute(text("select 1"))
        return True
    except Exception:
        return False


def _kubernetes_ok(context: str) -> bool:
    try:
        client.VersionApi(new_api_client(context)).get_code(_request_timeout=3)
        return True
    except Exception:
        return False


def _http_ok(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/health", response_model=HealthOut)
def health(request: Request, session: Session = Depends(get_session)):
    settings = get_settings()
    profiler = getattr(request.app.state, "profiler", None)
    checks = {
        "database": _database_ok(session),
        "kubernetes": _kubernetes_ok(settings.kube_context),
        "jaeger": _http_ok(f"{settings.jaeger_url}/api/v3/services"),
        "prometheus": _http_ok(f"{settings.prometheus_url}/-/ready"),
        "watchers_running": bool(profiler and profiler.running),
    }
    return HealthOut(
        status="ok" if all(checks.values()) else "degraded",
        clock_offset_s=profiler.clock_offset_s if profiler else None,
        **checks,
    )
