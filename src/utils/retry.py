import asyncio
import logging
import random
from typing import Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

# Коды, при которых биржа/сеть "временно" недоступна и запрос стоит повторить
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryableError(Exception):
    """Временная ошибка: обрыв сети, 429, 5xx. Запрос нужно повторить."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class NotFoundError(Exception):
    """Ресурса нет (404). Повторять бессмысленно."""


def compute_backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    """Exponential backoff с full jitter: случайное значение в [0, base * 2^attempt]."""
    return random.uniform(0, min(max_delay, base_delay * (2 ** attempt)))


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    description: str = "request",
) -> T:
    """
    Выполняет func, повторяя при RetryableError.
    Retry-After от сервера имеет приоритет над расчетным backoff.
    NotFoundError и прочие исключения пробрасываются сразу.
    """
    for attempt in range(max_attempts):
        try:
            return await func()
        except RetryableError as e:
            if attempt == max_attempts - 1:
                logger.error(f"{description}: исчерпаны попытки ({max_attempts}). Последняя ошибка: {e}")
                raise
            delay = e.retry_after if e.retry_after is not None else compute_backoff(attempt, base_delay, max_delay)
            logger.warning(f"{description}: {e}. Попытка {attempt + 1}/{max_attempts}, пауза {delay:.1f}s")
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
