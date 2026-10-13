"""Раскладка каталогов обработанных данных (общая для validation / derived / api)."""
from datetime import date
from pathlib import Path
from typing import Dict


def events_files(processed_dir: str, exchange: str, symbol: str) -> Dict[date, Path]:
    """{UTC-день: файл событий} для символа."""
    root = Path(processed_dir) / exchange / symbol / "events"
    return {date.fromisoformat(p.stem): p for p in root.glob("year=*/month=*/*.parquet")} if root.exists() else {}
