import json
import os
import stat
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src import scheduler
from src.pipeline import PipelineResult
from src.fetchers.base import BackfillReport, DownloadResult, DownloadStatus
from src.utils.dates import yesterday_utc


@pytest.fixture
def fake_crontab(tmp_path: Path, monkeypatch):
    """Создает фейковый бинарник crontab, сохраняющий строки в файл crontab.txt."""
    import importlib
    store_file = tmp_path / "crontab_store.txt"
    bin_file = tmp_path / "fake_crontab"

    script = f"""#!/bin/sh
if [ "$1" = "-l" ]; then
    if [ -f "{store_file}" ]; then
        cat "{store_file}"
        exit 0
    else
        echo "no crontab for user" >&2
        exit 1
    fi
elif [ "$1" = "-" ]; then
    cat > "{store_file}"
    exit 0
fi
exit 0
"""
    bin_file.write_text(script)
    bin_file.chmod(bin_file.stat().st_mode | stat.S_IEXEC)

    config_file = tmp_path / "schedule.json"
    config_file.write_text(json.dumps(dict(scheduler.DEFAULTS)))

    monkeypatch.setenv("PIPELINE_CRONTAB_BIN", str(bin_file))
    monkeypatch.setenv("PIPELINE_SCHEDULE_CONFIG", str(config_file))
    importlib.reload(scheduler)

    yield bin_file, store_file, config_file

    # Восстанавливаем оригинальные значения
    monkeypatch.delenv("PIPELINE_CRONTAB_BIN", raising=False)
    monkeypatch.delenv("PIPELINE_SCHEDULE_CONFIG", raising=False)
    importlib.reload(scheduler)


def test_cron_line_format_and_validation():
    line = scheduler.cron_line("02:45")
    assert line.startswith("45 2 * * *")
    assert scheduler.CRON_MARKER in line

    line_midnight = scheduler.cron_line("00:00")
    assert line_midnight.startswith("0 0 * * *")

    with pytest.raises(ValueError, match="time должен быть в формате HH:MM"):
        scheduler.cron_line("25:00")

    with pytest.raises(ValueError, match="time должен быть в формате HH:MM"):
        scheduler.cron_line("invalid")


def test_install_and_uninstall(fake_crontab):
    _, store_file, config_file = fake_crontab

    # Исходно crontab пуст
    assert not store_file.exists()
    assert scheduler.installed_cron_line() is None

    # Добавляем чужую запись
    store_file.write_text("0 12 * * * /usr/bin/backup.sh\n")

    # Устанавливаем cron
    line = scheduler.install("03:15")
    assert scheduler.CRON_MARKER in line
    content = store_file.read_text()
    assert "/usr/bin/backup.sh" in content
    assert line in content

    # Конфиг обновил время
    cfg = scheduler.load_config(config_file)
    assert cfg["time"] == "03:15"

    # Повторный install не дублирует строку
    scheduler.install("04:30")
    lines = store_file.read_text().splitlines()
    marker_lines = [l for l in lines if scheduler.CRON_MARKER in l]
    assert len(marker_lines) == 1
    assert marker_lines[0].startswith("30 4 * * *")
    assert any("/usr/bin/backup.sh" in l for l in lines)

    # uninstall удаляет только нашу строку
    uninstalled = scheduler.uninstall()
    assert uninstalled is True
    remaining = store_file.read_text().splitlines()
    assert not any(scheduler.CRON_MARKER in l for l in remaining)
    assert any("/usr/bin/backup.sh" in l for l in remaining)

    # Повторный uninstall возвращает False
    assert scheduler.uninstall() is False


def test_status_and_missing_binary(fake_crontab, monkeypatch):
    bin_file, store_file, config_file = fake_crontab

    # status при установленном cron и enabled=false
    st = scheduler.status()
    assert st["enabled"] is False
    assert st["cron_installed"] is False
    assert st["will_run"] is False

    scheduler.install()
    scheduler.set_enabled(True)
    st = scheduler.status()
    assert st["enabled"] is True
    assert st["cron_installed"] is True
    assert st["will_run"] is True

    # Отсутствие бинарника crontab
    monkeypatch.setattr("src.scheduler.CRONTAB_BIN", "/nonexistent/crontab")
    st = scheduler.status()
    assert st["cron_installed"] is False
    assert st["cron_error"] == "crontab не найден в системе"
    assert st["will_run"] is False


def test_run_disabled_and_force(fake_crontab):
    _, _, config_file = fake_crontab
    scheduler.set_enabled(False)

    with patch("src.scheduler.run_pipeline") as mock_run:
        code = scheduler.run(force_run=False)
        assert code == 0
        assert not mock_run.called

        # force_run запускает пайплайн даже при enabled=false
        mock_run.return_value = PipelineResult()
        code = scheduler.run(force_run=True)
        assert code == 0
        assert mock_run.called


def test_run_lookback_dates_and_failures(fake_crontab):
    _, _, config_file = fake_crontab
    cfg = scheduler.load_config(config_file)
    cfg["enabled"] = True
    cfg["lookback_days"] = 4
    scheduler.save_config(cfg, config_file)

    yesterday = yesterday_utc()
    expected_start = yesterday - timedelta(days=3)

    captured_params = None

    def fake_run_pipeline(params):
        nonlocal captured_params
        captured_params = params
        return PipelineResult()

    with patch("src.scheduler.run_pipeline", side_effect=fake_run_pipeline):
        code = scheduler.run()
        assert code == 0
        assert captured_params is not None
        assert captured_params.start == expected_start
        assert captured_params.end == yesterday
        assert captured_params.mode == "backfill"

    # При наличии FAILED шагов run() возвращает 1
    failed_report = BackfillReport("BTC-USDT", "trades", [
        DownloadResult(yesterday, DownloadStatus.FAILED, error="Timeout")
    ])
    with patch("src.scheduler.run_pipeline", return_value=PipelineResult(ingest=[failed_report])):
        assert scheduler.run() == 1

    # При исключении run() возвращает 1
    with patch("src.scheduler.run_pipeline", side_effect=RuntimeError("Crash")):
        assert scheduler.run() == 1


def test_scheduler_cli_main(fake_crontab, capsys):
    # show-cron
    assert scheduler.main(["show-cron"]) == 0
    out = capsys.readouterr().out
    assert scheduler.CRON_MARKER in out

    # status
    assert scheduler.main(["status"]) == 0
    st_json = json.loads(capsys.readouterr().out)
    assert "will_run" in st_json

    # enable / disable
    assert scheduler.main(["enable"]) == 0
    assert scheduler.load_config()["enabled"] is True
    assert scheduler.main(["disable"]) == 0
    assert scheduler.load_config()["enabled"] is False

    # install / uninstall
    assert scheduler.main(["install", "--time", "05:15"]) == 0
    assert scheduler.load_config()["time"] == "05:15"
    assert scheduler.main(["uninstall"]) == 0
