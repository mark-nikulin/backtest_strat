import os
import uuid
from pathlib import Path
from typing import Callable, Optional

import aiohttp

from src.utils.retry import RETRYABLE_STATUSES, NotFoundError, RetryableError

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
CHUNK_SIZE = 1024 * 1024

ProgressCallback = Callable[[int, Optional[int]], None]


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date формат не поддерживаем — уйдем в обычный backoff


async def download_to_file(
    session: aiohttp.ClientSession,
    url: str,
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
) -> Path:
    """
    Атомарно скачивает url в dest.

    Пишем во временный dest.part и переименовываем только после полной загрузки
    с проверкой Content-Length. Так оборванная загрузка никогда не оставит
    "битый" файл по финальному пути, и проверка идемпотентности остается корректной.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Уникальное имя: параллельные процессы (cron + Web UI) не пишут в один и тот же .part
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:8]}.part")

    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status == 404:
                raise NotFoundError(f"404 {url}")
            if resp.status in RETRYABLE_STATUSES:
                raise RetryableError(
                    f"HTTP {resp.status} {url}",
                    retry_after=_parse_retry_after(resp.headers.get("Retry-After")),
                )
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} {url}")

            expected = resp.content_length
            written = 0
            with open(tmp, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    f.write(chunk)
                    written += len(chunk)
                    if on_progress:
                        on_progress(written, expected)

        if expected is not None and written != expected:
            raise RetryableError(f"Неполная загрузка {url}: {written}/{expected} байт")

        os.replace(tmp, dest)
        return dest

    except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, TimeoutError) as e:
        raise RetryableError(f"Сетевая ошибка {url}: {e!r}") from e
    finally:
        if tmp.exists():
            tmp.unlink()
