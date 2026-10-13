import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

COMPRESSION = "zstd"


def partition_dir(root: Path, year: int, month: int) -> Path:
    return root / f"year={year}" / f"month={month:02d}"


class PartitionedParquetWriter:
    """
    Пишет поток таблиц в root/year=YYYY/month=MM/<filename>, раскладывая строки
    по месяцу колонки ts. Каждая партиция пишется в .part и переименовывается
    только в commit(), поэтому прерванный процесс не оставит недописанный Parquet.
    """

    def __init__(self, root: Path, filename: str, schema: pa.Schema):
        self.root = root
        self.filename = filename
        self.schema = schema
        self._writers: Dict[int, pq.ParquetWriter] = {}
        self._paths: Dict[int, Path] = {}

    def _writer(self, key: int) -> pq.ParquetWriter:
        if key not in self._writers:
            path = partition_dir(self.root, key // 100, key % 100) / self.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            self._paths[key] = path
            self._writers[key] = pq.ParquetWriter(
                path.with_name(path.name + ".part"), self.schema, compression=COMPRESSION
            )
        return self._writers[key]

    def write(self, table: pa.Table) -> None:
        if table.num_rows == 0:
            return
        ts = table.column("ts")
        keys = pc.add(pc.multiply(pc.year(ts), 100), pc.month(ts))
        unique_keys = pc.unique(keys).to_pylist()
        if len(unique_keys) == 1:  # типичный случай — весь батч в одном месяце, без лишнего копирования
            self._writer(unique_keys[0]).write_table(table)
            return
        for key in unique_keys:
            self._writer(key).write_table(table.filter(pc.equal(keys, key)))

    def commit(self) -> List[Path]:
        for w in self._writers.values():
            w.close()
        for path in self._paths.values():
            os.replace(path.with_name(path.name + ".part"), path)
        return sorted(self._paths.values())

    def abort(self) -> None:
        for w in self._writers.values():
            try:
                w.close()
            except Exception:
                pass
        for path in self._paths.values():
            path.with_name(path.name + ".part").unlink(missing_ok=True)

    def __enter__(self) -> "PartitionedParquetWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.abort()


class Manifest:
    """
    JSON-реестр обработанных источников: ключ -> {fingerprint, outputs}.
    Позволяет пропускать уже обработанные файлы и удалять устаревшие выходы при пересборке.
    """

    def __init__(self, path: Path):
        self.path = path
        self._data: Dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def is_current(self, key: str, fingerprint: dict) -> bool:
        entry = self._data.get(key)
        if not entry or entry.get("fingerprint") != fingerprint:
            return False
        return all(Path(p).exists() for p in entry.get("outputs", []))

    def set(self, key: str, fingerprint: dict, outputs: List[Path]) -> None:
        self._data[key] = {"fingerprint": fingerprint, "outputs": [str(p) for p in outputs]}
        self._save()

    def remove_outputs(self, key: str) -> None:
        """Удаляет выходы прошлой сборки: при пересборке набор партиций может измениться."""
        entry = self._data.pop(key, None)
        for p in (entry or {}).get("outputs", []):
            Path(p).unlink(missing_ok=True)
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=1, sort_keys=True))
        os.replace(tmp, self.path)
