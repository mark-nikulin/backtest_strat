from datetime import date, datetime, timedelta, timezone
from typing import List


def date_range(start: date, end: date) -> List[date]:
    """Все даты от start до end включительно."""
    if end < start:
        raise ValueError(f"end ({end}) раньше start ({start})")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def yesterday_utc() -> date:
    """Последний полный день по UTC — именно за него биржи публикуют daily-архивы."""
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()
