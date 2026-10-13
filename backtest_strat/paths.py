"""
Пути по умолчанию. Относительные — от текущей рабочей папки процесса (папки вашего проекта).
Переопределяются переменными окружения, чтобы один раз настроить и забыть:

  PIPELINE_RAW_DIR        сырые архивы            (по умолчанию data/raw)
  PIPELINE_PROCESSED_DIR  нормализованные данные  (по умолчанию data/processed)
  PIPELINE_DATA_DIR       корень остальных данных: внешние ряды, база задач (по умолчанию data)
"""
import os
from pathlib import Path

DATA_DIR = os.getenv("PIPELINE_DATA_DIR", "data")
RAW_DIR = os.getenv("PIPELINE_RAW_DIR", f"{DATA_DIR}/raw")
PROCESSED_DIR = os.getenv("PIPELINE_PROCESSED_DIR", f"{DATA_DIR}/processed")
EXTERNAL_DIR = os.getenv("PIPELINE_EXTERNAL_DIR", f"{DATA_DIR}/external")
JOBS_DB = os.getenv("PIPELINE_JOBS_DB", f"{DATA_DIR}/jobs.sqlite")
WORKDIR = Path(os.getenv("PIPELINE_WORKDIR", ".")).resolve()   # откуда запускается cron-задача и где лежит config/
