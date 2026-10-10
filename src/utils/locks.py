import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """
    Межпроцессная эксклюзивная блокировка (flock). Ночной cron и задача из Web UI
    по одному символу выполняются по очереди, а не портят друг другу manifest.
    Блокировка снимается ОС автоматически, даже если процесс упал.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
