"""
Проверки качества дня по одним сделкам (без стакана): для датасетов, собранных только из trades.

  no_trades        в сутках нет сделок                                           (FAIL)
  duplicate_ids    повторяющиеся trade_id в сутках                                (FAIL)
  non_positive     цена или объем <= 0                                            (FAIL)
  trade_gap        пауза между сделками > gap_warn_s / gap_fail_s                 (WARN / FAIL)
  price_spike      скачок цены минута-к-минуте >= spike_pct с возвратом >= 80%    (WARN)
  day_coverage     доля минут суток со сделками (инфо; WARN при слишком малой)    (WARN)

Паузы зависят от ликвидности актива: пороги заданы широкими, чтобы отлавливать провалы в данных
(выпавшие часы), а не нормальную редкость сделок.
"""
from dataclasses import asdict, dataclass
from datetime import date
from typing import List

import numpy as np

from src.derived.trade_bars import read_utc_day_trades
from src.validation.day import FAIL, OK, WARN, Check, DayReport, _iso

NS = 1_000_000_000


@dataclass(frozen=True)
class TradeThresholds:
    gap_warn_s: float = 3600.0
    gap_fail_s: float = 6 * 3600.0
    spike_pct: float = 5.0
    spike_reversal: float = 0.8
    spike_window_min: int = 5
    min_active_minutes_share: float = 0.25
    max_examples: int = 5


def validate_trade_day(processed_dir: str, exchange: str, symbol: str, day: date, th: TradeThresholds = TradeThresholds()) -> DayReport:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from datetime import timedelta
    from src.derived.trade_bars import trade_source_files

    # trade_id нужен для проверки дублей — читаем напрямую
    ids, ts_l, px_l, sz_l = [], [], [], []
    lo = int(np.datetime64(day.isoformat(), "ns").astype("int64"))
    hi = lo + 86400 * NS
    for src in (day, day + timedelta(days=1)):
        for f in trade_source_files(processed_dir, exchange, symbol, src):
            t = pq.read_table(f, columns=["ts", "price", "size", "msg_id"])
            ts = t["ts"].cast(pa.int64()).to_numpy()
            m = (ts >= lo) & (ts < hi)
            ts_l.append(ts[m]); px_l.append(t["price"].to_numpy()[m])
            sz_l.append(t["size"].to_numpy()[m]); ids.append(t["msg_id"].to_numpy()[m])
    ts = np.concatenate(ts_l) if ts_l else np.array([], dtype=np.int64)
    px = np.concatenate(px_l) if px_l else np.array([])
    sz = np.concatenate(sz_l) if sz_l else np.array([])
    tid = np.concatenate(ids) if ids else np.array([], dtype=np.int64)
    n = len(ts)
    checks: List[Check] = []

    if n == 0:
        checks.append(Check("no_trades", FAIL, "В сутках нет сделок"))
        return DayReport(day.isoformat(), FAIL, 0, 0, 0, 0, None, None, [asdict(c) for c in checks], {})

    order = np.argsort(ts, kind="stable")
    ts, px, sz, tid = ts[order], px[order], sz[order], tid[order]
    checks.append(Check("no_trades", OK, f"{n} сделок"))

    dup = n - len(np.unique(tid))
    checks.append(Check("duplicate_ids", FAIL if dup else OK, f"{dup} повторяющихся trade_id" if dup else "Дублей нет", value=dup))

    bad = int(((px <= 0) | (sz <= 0)).sum())
    checks.append(Check("non_positive", FAIL if bad else OK, f"{bad} сделок с некорректными ценой/объемом" if bad else "Некорректных значений нет", value=bad))

    gaps = np.diff(np.r_[lo, ts, hi]) / NS  # включая края суток
    gmax = float(gaps.max())
    ex = []
    for i in np.argsort(gaps)[::-1][: th.max_examples]:
        if gaps[i] > th.gap_warn_s:
            start_ns = (np.r_[lo, ts][i])
            ex.append(f"{_iso(int(start_ns))} + {gaps[i] / 60:.0f} мин")
    status = FAIL if gmax > th.gap_fail_s else WARN if gmax > th.gap_warn_s else OK
    checks.append(Check("trade_gap", status, f"Макс. пауза между сделками {gmax / 60:.0f} мин", value=gmax, examples=ex))

    minute = (ts - lo) // (60 * NS)
    last_idx = np.r_[np.flatnonzero(minute[1:] != minute[:-1]), n - 1]
    m_close = px[last_idx]
    m_ids = minute[last_idx]
    spikes, spike_ex = 0, []
    if len(m_close) > 2:
        ret = np.abs(np.diff(m_close) / m_close[:-1]) * 100
        for i in np.flatnonzero(ret >= th.spike_pct):
            j = i + 1
            move = m_close[j] - m_close[i]
            window = (m_ids > m_ids[j]) & (m_ids <= m_ids[j] + th.spike_window_min)
            if move != 0 and window.any() and np.any((m_close[j] - m_close[window]) / move >= th.spike_reversal):
                spikes += 1
                if len(spike_ex) < th.max_examples:
                    spike_ex.append(f"{_iso(lo + int(m_ids[j]) * 60 * NS)} {m_close[i]:g} → {m_close[j]:g}")
    checks.append(Check("price_spike", WARN if spikes else OK,
                        f"{spikes} скачков >= {th.spike_pct:g}% за минуту с возвратом" if spikes else "Скачков с возвратом нет",
                        value=spikes, examples=spike_ex))

    share = len(np.unique(minute)) / 1440
    checks.append(Check("day_coverage", WARN if share < th.min_active_minutes_share else OK,
                        f"Минут со сделками: {share:.1%}", value=share))

    worst = max((c.status for c in checks), key=lambda s: {OK: 0, WARN: 1, FAIL: 2}[s])
    return DayReport(day.isoformat(), worst, n, 0, n, 0, _iso(int(ts[0])), _iso(int(ts[-1])), [asdict(c) for c in checks],
                     {"trades": float(n), "max_gap_s": gmax, "active_minutes_share": share, "spikes": float(spikes)})
