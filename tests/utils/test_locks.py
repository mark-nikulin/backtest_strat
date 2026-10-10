import multiprocessing
from pathlib import Path
import time
import pytest

from src.utils.locks import file_lock


def _hold_lock(lock_path_str: str, duration: float, ready_event):
    lock_path = Path(lock_path_str)
    with file_lock(lock_path):
        ready_event.set()
        time.sleep(duration)


def test_file_lock_mutual_exclusion(tmp_path: Path):
    lock_path = tmp_path / "test.lock"
    ready = multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold_lock, args=(str(lock_path), 0.3, ready))
    proc.start()
    try:
        assert ready.wait(timeout=5), "Worker did not acquire lock in time"
        t0 = time.time()
        with file_lock(lock_path):
            t1 = time.time()
        # Main process had to wait until worker finished holding lock
        assert (t1 - t0) >= 0.2
    finally:
        proc.join(timeout=5)


def test_file_lock_creates_parent_dir(tmp_path: Path):
    nested_lock = tmp_path / "nested" / "sub" / ".lock"
    assert not nested_lock.parent.exists()
    with file_lock(nested_lock):
        assert nested_lock.exists()
