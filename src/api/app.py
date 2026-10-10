"""
HTTP API пайплайна. Запуск:
  uvicorn src.api.app:app --port 8000
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.jobs import JobRunner, JobStore
from src.fetchers import FETCHERS, get_fetcher
from src.normalizers import get_normalizer
from src.pipeline import DATA_TYPES, PipelineParams

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = os.getenv("PIPELINE_RAW_DIR", "data/raw")
PROCESSED_DIR = os.getenv("PIPELINE_PROCESSED_DIR", "data/processed")
JOBS_DB = os.getenv("PIPELINE_JOBS_DB", "data/jobs.sqlite")


class JobRequest(BaseModel):
    exchange: str
    symbols: List[str] = Field(min_length=1)
    mode: Literal["backfill", "daily"] = "backfill"
    start: Optional[date] = None
    end: Optional[date] = None
    data_types: List[Literal["trades", "orderbook"]] = Field(default_factory=lambda: list(DATA_TYPES))
    stage: Literal["all", "ingest", "normalize"] = "all"
    raw_dir: str = RAW_DIR
    processed_dir: str = PROCESSED_DIR
    max_concurrent: int = Field(5, ge=1, le=32)
    force: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = JobStore(JOBS_DB)
    n = store.fail_interrupted()
    if n:
        logger.warning(f"{n} задач(и) прерваны прошлым перезапуском — помечены failed")
    app.state.store = store
    app.state.runner = JobRunner(store)
    yield
    app.state.runner.shutdown()


app = FastAPI(title="Backtest Data Pipeline", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/meta")
def meta():
    return {"exchanges": sorted(FETCHERS), "data_types": list(DATA_TYPES), "raw_dir": RAW_DIR, "processed_dir": PROCESSED_DIR}


@app.get("/exchanges/{exchange}/symbols")
async def symbols(exchange: str, inst_type: str = Query("SPOT")):
    try:
        fetcher = get_fetcher(exchange)
    except ValueError as e:
        raise HTTPException(404, str(e))
    try:
        return {"symbols": await fetcher.list_symbols(inst_type)}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"Биржа недоступна: {e}")


@app.post("/jobs", status_code=202)
def create_job(req: JobRequest):
    params = PipelineParams(**req.model_dump())
    try:
        job_id = app.state.runner.submit(params)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return app.state.store.get(job_id)


@app.get("/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=500)):
    return app.state.store.list(limit)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = app.state.store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@app.get("/coverage/{exchange}/{symbol}")
def coverage(exchange: str, symbol: str, raw_dir: str = RAW_DIR, processed_dir: str = PROCESSED_DIR):
    """Какие дни уже есть на диске: сырые архивы по типам и собранные события."""
    try:
        norm = get_normalizer(exchange, raw_dir=raw_dir, processed_dir=processed_dir)
    except ValueError as e:
        raise HTTPException(404, str(e))
    raw = {dt: sorted(norm.source_date(p).isoformat() for p in norm.raw_files(symbol, dt)) for dt in DATA_TYPES}
    events_root = norm.symbol_dir(symbol) / "events"
    events = sorted(p.stem for p in events_root.glob("year=*/month=*/*.parquet")) if events_root.exists() else []
    return {"raw": raw, "events": events}
