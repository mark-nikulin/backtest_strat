import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from src.utils.logging_setup import (
    DailyFileHandler,
    cleanup_old_logs,
    read_logs,
    setup_logging,
)


def test_daily_logging_format_and_extra_source(tmp_path: Path):
    log_dir = tmp_path / "logs"
    setup_logging("cli", log_dir=log_dir, console=False)

    logger = logging.getLogger("test_logger")
    logger.info("Message from cli")
    logger.warning("Message from ui", extra={"source": "ui"})

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = log_dir / f"pipeline-{today}.log"
    assert log_file.exists()

    content = log_file.read_text()
    assert "[INFO] [cli] test_logger: Message from cli" in content
    assert "[WARNING] [ui] test_logger: Message from ui" in content


def test_repeated_setup_does_not_duplicate_lines(tmp_path: Path):
    log_dir = tmp_path / "logs"
    # Call setup multiple times
    setup_logging("cron", log_dir=log_dir, console=False)
    setup_logging("cron", log_dir=log_dir, console=False)

    logger = logging.getLogger("single_test")
    logger.info("Unique single message")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = log_dir / f"pipeline-{today}.log"
    lines = [l for l in log_file.read_text().splitlines() if "Unique single message" in l]
    assert len(lines) == 1


def test_cleanup_old_logs(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    old_date = (now - timedelta(days=35)).strftime("%Y-%m-%d")
    recent_date = (now - timedelta(days=5)).strftime("%Y-%m-%d")

    old_file = log_dir / f"pipeline-{old_date}.log"
    recent_file = log_dir / f"pipeline-{recent_date}.log"
    other_file = log_dir / "random.txt"

    old_file.write_text("old log")
    recent_file.write_text("recent log")
    other_file.write_text("keep me")

    cleanup_old_logs(log_dir=log_dir, retention_days=30)

    assert not old_file.exists()
    assert recent_file.exists()
    assert other_file.exists()


def test_read_logs_multiline_traceback_and_filters(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    d1 = "2024-01-01"
    file1 = log_dir / f"pipeline-{d1}.log"
    content = """2024-01-01 10:00:00,000 [INFO] [cli] test: Step 1 success
2024-01-01 10:01:00,000 [ERROR] [cron] test: Step 2 crashed
Traceback (most recent call last):
  File "main.py", line 10, in <module>
    raise ValueError("Bad value")
ValueError: Bad value
2024-01-01 10:02:00,000 [WARNING] [api] test: Step 3 warning with query BTC-USDT
"""
    file1.write_text(content)

    # 1. Newest first (limit 10)
    entries = read_logs(log_dir=log_dir, days=1, limit=10)
    assert len(entries) == 3
    assert entries[0]["level"] == "WARNING"
    assert entries[1]["level"] == "ERROR"
    assert entries[2]["level"] == "INFO"

    # Multiline traceback merged into message
    assert 'Traceback (most recent call last):' in entries[1]["message"]
    assert 'ValueError: Bad value' in entries[1]["message"]

    # 2. Filter by level
    err_entries = read_logs(log_dir=log_dir, days=1, levels={"ERROR"})
    assert len(err_entries) == 1
    assert err_entries[0]["level"] == "ERROR"

    # 3. Filter by source
    api_entries = read_logs(log_dir=log_dir, days=1, source="api")
    assert len(api_entries) == 1
    assert api_entries[0]["source"] == "api"

    # 4. Filter by query
    q_entries = read_logs(log_dir=log_dir, days=1, query="BTC-USDT")
    assert len(q_entries) == 1
    assert q_entries[0]["source"] == "api"

    # 5. Limit
    lim_entries = read_logs(log_dir=log_dir, days=1, limit=2)
    assert len(lim_entries) == 2
