import os
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.jobs import JobRunner, JobStore
from src.pipeline import PipelineParams, PipelineResult


def test_job_store_crud_and_fail_interrupted(tmp_path: Path):
    db_file = tmp_path / "jobs_test.sqlite"
    store = JobStore(str(db_file))

    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="daily",
    )

    # 1. Create
    job_id = store.create(params)
    assert job_id is not None
    job = store.get(job_id)
    assert job["id"] == job_id
    assert job["status"] == "queued"
    assert job["progress"] == 0.0
    assert job["params"]["exchange"] == "okx"

    # 2. Update
    store.update(job_id, status="running", progress=0.5, message="Halfway")
    job = store.get(job_id)
    assert job["status"] == "running"
    assert job["progress"] == 0.5
    assert job["message"] == "Halfway"

    # 3. Create another job that finishes
    done_params = PipelineParams(exchange="okx", symbols=["ETH-USDT"], mode="daily")
    done_id = store.create(done_params)
    store.update(done_id, status="done", progress=1.0, message="Complete", result={"ok": True})

    # 4. List
    jobs = store.list(limit=10)
    assert len(jobs) == 2
    job_ids = {j["id"] for j in jobs}
    assert job_ids == {job_id, done_id}

    # 5. fail_interrupted: job_id (running) -> failed, done_id remains done
    interrupted_count = store.fail_interrupted()
    assert interrupted_count == 1

    job_after = store.get(job_id)
    assert job_after["status"] == "failed"
    assert "Прервано перезапуском" in job_after["message"]

    done_after = store.get(done_id)
    assert done_after["status"] == "done"


def test_job_runner_execution(tmp_path: Path):
    db_file = tmp_path / "runner_test.sqlite"
    store = JobStore(str(db_file))
    runner = JobRunner(store, max_workers=1)

    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="daily",
    )

    fake_result = PipelineResult()

    try:
        with patch("src.api.jobs.run_pipeline", return_value=fake_result):
            job_id = runner.submit(params)
            # Wait for thread to finish
            for _ in range(50):
                job = store.get(job_id)
                if job["status"] in ("done", "failed"):
                    break
                time.sleep(0.05)

            assert job["status"] == "done"
            assert job["progress"] == 1.0
            assert job["result"]["ok"] is True
    finally:
        runner.shutdown()


@pytest.fixture
def api_client(tmp_path: Path):
    db_file = tmp_path / "api_test.sqlite"
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"

    with patch("src.api.app.JOBS_DB", str(db_file)), \
         patch("src.api.app.RAW_DIR", str(raw_dir)), \
         patch("src.api.app.PROCESSED_DIR", str(processed_dir)):
        with TestClient(app) as client:
            yield client, raw_dir, processed_dir


def test_api_meta_and_health(api_client):
    client, _, _ = api_client
    resp_h = client.get("/health")
    assert resp_h.status_code == 200
    assert resp_h.json() == {"status": "ok"}

    resp_m = client.get("/meta")
    assert resp_m.status_code == 200
    data = resp_m.json()
    assert "okx" in data["exchanges"]
    assert "trades" in data["data_types"]


def test_api_create_and_get_job(api_client):
    client, _, _ = api_client

    # 1. Validation error: 422
    bad_req = {"exchange": "okx", "symbols": ["BTC-USDT"], "mode": "backfill"}
    # Backfill missing start & end -> 422
    resp_bad = client.post("/jobs", json=bad_req)
    assert resp_bad.status_code == 422

    # 2. Valid request: 202
    good_req = {
        "exchange": "okx",
        "symbols": ["BTC-USDT"],
        "mode": "daily",
    }
    fake_result = PipelineResult()
    with patch("src.api.jobs.run_pipeline", return_value=fake_result):
        resp = client.post("/jobs", json=good_req, headers={"X-Client": "ui"})
        assert resp.status_code == 202
        created = resp.json()
        job_id = created["id"]
        assert created["status"] in ("queued", "running", "done")

        # 3. GET /jobs/{job_id}
        resp_get = client.get(f"/jobs/{job_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["id"] == job_id

        # 4. Unknown job: 404
        assert client.get("/jobs/nonexistent_id").status_code == 404

        # 5. GET /jobs list
        resp_list = client.get("/jobs")
        assert resp_list.status_code == 200
        assert any(j["id"] == job_id for j in resp_list.json())


def test_api_coverage(api_client):
    client, raw_dir, processed_dir = api_client

    # Create synthetic raw files and events file
    trades_dir = raw_dir / "okx" / "BTC-USDT" / "trades"
    trades_dir.mkdir(parents=True, exist_ok=True)
    (trades_dir / "BTC-USDT-trades-2024-01-01.zip").write_bytes(b"dummy")

    events_dir = processed_dir / "okx" / "BTC-USDT" / "events" / "year=2024" / "month=01"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "2024-01-01.parquet").write_bytes(b"dummy")

    resp = client.get(f"/coverage/okx/BTC-USDT?raw_dir={raw_dir}&processed_dir={processed_dir}")
    assert resp.status_code == 200
    cov = resp.json()
    assert "2024-01-01" in cov["raw"]["trades"]
    assert "2024-01-01" in cov["events"]


def test_api_symbols_invalid_type(api_client):
    client, _, _ = api_client
    # Invalid inst_type should return 422
    resp = client.get("/exchanges/okx/symbols?inst_type=XYZ")
    assert resp.status_code == 422
    assert "inst_type: допустимо" in resp.json()["detail"]
