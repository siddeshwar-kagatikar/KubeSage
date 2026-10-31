"""KubeSage API. Run from backend/:  uvicorn kubesage.main:app --port 8000"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kubesage.api import health, incidents, workloads
from kubesage.config import get_settings
from kubesage.db.session import init_db
from kubesage.profiler.service import ProfilerService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    profiler = None
    if settings.watchers_enabled:
        profiler = ProfilerService(settings)
        profiler.start()
    app.state.profiler = profiler
    yield
    if profiler:
        profiler.stop()


app = FastAPI(title="KubeSage", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(workloads.router)
app.include_router(incidents.router)
