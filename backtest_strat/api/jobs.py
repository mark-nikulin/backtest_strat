"""
Реестр фоновых задач в SQLite.

Почему не Celery: один пользователь и один сервер — брокер (Redis) и отдельный воркер
не дают выгоды, но добавляют инфраструктуру. Задачи выполняются в пуле потоков процесса API,
состояние и прогресс живут в SQLite и переживают перезапуск.
"""
import json
import logging
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from backtest_strat.paths import JOBS_DB
from backtest_strat.pipeline import PipelineParams, run_pipeline

logger = logging.getLogger(__name__)

STATUSES = ("queued", "running", "done", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    status      TEXT NOT NULL,
    progress    REAL NOT NULL DEFAULT 0,
    message     TEXT,
    params      TEXT NOT NULL,
    result      TEXT
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _params_to_json(p: PipelineParams) -> str:
    return json.dumps(asdict(p), default=lambda v: v.isoformat() if isinstance(v, date) else str(v))


class JobStore:
    def __init__(self, db_path: str = JOBS_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, params: PipelineParams) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = _now()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id, created_at, updated_at, status, progress, message, params) VALUES (?,?,?,?,?,?,?)",
                (job_id, now, now, "queued", 0.0, "В очереди", _params_to_json(params)),
            )
        return job_id

    def update(self, job_id: str, **fields) -> None:
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"])
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def get(self, job_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 50) -> List[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def fail_interrupted(self) -> int:
        """После рестарта сервера задачи в queued/running уже никто не выполняет — помечаем их явно."""
        with self._lock, self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status='failed', message='Прервано перезапуском сервера', updated_at=? "
                "WHERE status IN ('queued','running')",
                (_now(),),
            )
            return cur.rowcount

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["params"] = json.loads(d["params"])
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d


class JobRunner:
    """Выполняет задачи пайплайна в фоне и пишет прогресс в JobStore."""

    def __init__(self, store: JobStore, max_workers: int = 2):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")

    def submit(self, params: PipelineParams) -> str:
        params.validate()
        job_id = self.store.create(params)
        self.executor.submit(self._run, job_id, params)
        return job_id

    def _run(self, job_id: str, params: PipelineParams) -> None:
        self.store.update(job_id, status="running", message="Запуск")
        logger.info(f"Job {job_id} started: {params.exchange} {params.symbols} {params.mode}")
        last = {"pct": -1.0}

        def on_progress(frac: float, text: str) -> None:
            # Не пишем в БД на каждый файл — только при заметном изменении
            if frac - last["pct"] >= 0.005 or frac >= 1.0:
                last["pct"] = frac
                self.store.update(job_id, progress=round(frac, 4), message=text)

        try:
            result = run_pipeline(params, on_progress=on_progress)
            summary = result.to_dict()
            self.store.update(
                job_id,
                status="done" if result.ok else "failed",
                progress=1.0,
                message="Готово" if result.ok else f"Есть ошибки: {len(summary['failed'])}",
                result=summary,
            )
            logger.info(f"Job {job_id} finished: ok={result.ok}")
        except Exception as e:
            logger.exception(f"Job {job_id} crashed")
            self.store.update(job_id, status="failed", message=f"Ошибка: {e!r}")

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
