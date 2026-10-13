"""Запуск веб-частей из установленного пакета (streamlit run нужен путь к файлу, который лежит в site-packages)."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _streamlit(script: str) -> None:
    try:
        from streamlit.web import cli as stcli
    except ImportError as e:  # pragma: no cover
        raise SystemExit("Нужен extra [ui]:  pip install 'backtest-strat[ui]'") from e
    sys.argv = ["streamlit", "run", str(_HERE / script), *sys.argv[1:]]
    sys.exit(stcli.main())


def dashboard() -> None:
    """Дашборд: задачи, покрытие, качество данных, расписание, логи."""
    _streamlit("dashboard.py")


def monitor() -> None:
    """Монитор процесса (читает состояние с диска текущей папки)."""
    _streamlit("monitor.py")


def api(host: str = "127.0.0.1", port: int = 8000) -> None:
    """HTTP API задач. По умолчанию слушает только localhost."""
    try:
        import uvicorn
    except ImportError as e:  # pragma: no cover
        raise SystemExit("Нужен extra [api]:  pip install 'backtest-strat[api]'") from e
    uvicorn.run("backtest_strat.api.app:app", host=host, port=port)
