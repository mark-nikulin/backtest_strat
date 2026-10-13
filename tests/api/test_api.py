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


def test_api_schedule_endpoints(api_client, monkeypatch):
    client, _, _ = api_client
    from src import scheduler

    cfg_data = dict(scheduler.DEFAULTS)

    def fake_load(path=None):
        return dict(cfg_data)

    def fake_save(cfg, path=None):
        scheduler._validate_time(cfg["time"])
        cfg_data.clear()
        cfg_data.update(cfg)

    monkeypatch.setattr("src.scheduler.load_config", fake_load)
    monkeypatch.setattr("src.scheduler.save_config", fake_save)
    monkeypatch.setattr("src.scheduler.status", lambda: {
        "enabled": cfg_data["enabled"],
        "cron_installed": True,
        "cron_line": scheduler.cron_line(cfg_data["time"]),
        "cron_error": None,
        "will_run": cfg_data["enabled"],
        "config": cfg_data,
    })

    # 1. GET /schedule
    resp = client.get("/schedule")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "cron_installed" in data
    assert "config" in data

    # 2. PATCH /schedule valid
    patch_req = {
        "enabled": True,
        "time": "04:15",
        "lookback_days": 5,
        "symbols": ["BTC-USDT", "ETH-USDT"],
    }
    resp_patch = client.patch("/schedule", json=patch_req, headers={"X-Client": "ui"})
    assert resp_patch.status_code == 200
    updated = resp_patch.json()
    assert updated["config"]["time"] == "04:15"
    assert updated["config"]["lookback_days"] == 5
    assert updated["enabled"] is True

    # 3. PATCH /schedule invalid time -> 422
    resp_bad_time = client.patch("/schedule", json={"time": "invalid_time"})
    assert resp_bad_time.status_code == 422

    # 4. PATCH /schedule lookback_days out of range (ge=1, le=30) -> 422
    resp_bad_days = client.patch("/schedule", json={"lookback_days": 50})
    assert resp_bad_days.status_code == 422


def test_api_logs_endpoints(api_client, tmp_path, monkeypatch):
    client, _, _ = api_client
    log_dir = tmp_path / "api_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    today = "2024-01-01"
    log_file = log_dir / f"pipeline-{today}.log"
    log_file.write_text(
        "2024-01-01 12:00:00,000 [INFO] [api] src.api.app: Server startup\n"
        "2024-01-01 12:01:00,000 [WARNING] [ui] src.api.app: UI warning with keyword\n"
    )

    with patch("src.api.app.read_logs") as mock_read:
        mock_read.return_value = [{"level": "WARNING", "source": "ui", "message": "UI warning"}]
        resp = client.get("/logs?days=1&level=WARNING&source=ui&q=keyword&limit=10")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert mock_read.called
        kwargs = mock_read.call_args[1]
        assert kwargs["days"] == 1
        assert kwargs["levels"] == {"WARNING"}
        assert kwargs["source"] == "ui"
        assert kwargs["query"] == "keyword"


def test_api_validation_endpoint(api_client):
    import json
    client, _, processed_dir = api_client

    events_dir = processed_dir / "okx" / "BTC-USDT" / "events" / "year=2024" / "month=01"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "2024-01-01.parquet").write_bytes(b"dummy")
    (events_dir / "2024-01-03.parquet").write_bytes(b"dummy")

    val_dir = processed_dir / "okx" / "BTC-USDT" / "validation"
    val_dir.mkdir(parents=True, exist_ok=True)
    report_data = {
        "fingerprint": {"name": "2024-01-01.parquet", "size": 5, "version": 1},
        "report": {
            "day": "2024-01-01",
            "status": "OK",
            "events": 100,
            "book_messages": 50,
            "trades": 50,
            "snapshots": 1,
            "first_ts": "2024-01-01T00:00:00.000Z",
            "last_ts": "2024-01-01T23:59:59.000Z",
            "checks": [],
            "stats": {},
        },
    }
    (val_dir / "2024-01-01.json").write_text(json.dumps(report_data))

    resp = client.get(f"/validation/okx/BTC-USDT?processed_dir={processed_dir}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["days"]) == 1
    assert data["days"][0]["day"] == "2024-01-01"
    assert data["missing_days"] == ["2024-01-02"]
    assert data["not_validated"] == ["2024-01-03"]
