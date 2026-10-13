"""
Производные датасеты из потока событий дня (events/<...>/<YYYY-MM-DD>.parquet).

l1       лучшие bid/ask и объемы на каждое изменение вершины стакана (ts в ns, UTC)
bars_1s  бары по сделкам за 1 секунду
bars_1m  бары по сделкам за 1 минуту

Раскладка: processed/<exchange>/<symbol>/<kind>/year=YYYY/month=MM/<YYYY-MM-DD>.parquet

Допущения:
  * Книга восстанавливается независимо внутри дня и начинается с первого снапшота дня
    (если файл начинается с апдейтов, первые секунды/минута дня в l1 отсутствуют).
  * Бары есть только для интервалов, где были сделки; пустые интервалы не заполняются.
  * mid_close / spread_close в барах — состояние L1 на конец интервала (as-of), NaN до первого L1.
"""
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from backtest_strat.normalizers.schema import BOOK_SNAPSHOT, BUY, TRADE
from backtest_strat.utils.simple_book import SimpleBook

COLUMNS = ["ts", "event_type", "side", "price", "size", "msg_id"]
TS_TYPE = pa.timestamp("ns", tz="UTC")
NS = 1_000_000_000

L1_SCHEMA = pa.schema([
    pa.field("ts", TS_TYPE, nullable=False),
    pa.field("bid_px", pa.float64(), nullable=False),
    pa.field("bid_sz", pa.float64(), nullable=False),
    pa.field("ask_px", pa.float64(), nullable=False),
    pa.field("ask_sz", pa.float64(), nullable=False),
])

BAR_SCHEMA = pa.schema([
    pa.field("ts", TS_TYPE, nullable=False),          # начало интервала
    pa.field("open", pa.float64(), nullable=False),
    pa.field("high", pa.float64(), nullable=False),
    pa.field("low", pa.float64(), nullable=False),
    pa.field("close", pa.float64(), nullable=False),
    pa.field("volume", pa.float64(), nullable=False),
    pa.field("vwap", pa.float64(), nullable=False),
    pa.field("buy_volume", pa.float64(), nullable=False),   # объем сделок с агрессором-покупателем
    pa.field("sell_volume", pa.float64(), nullable=False),
    pa.field("imbalance", pa.float64(), nullable=False),    # (buy - sell) / volume, [-1, 1]
    pa.field("trades", pa.int32(), nullable=False),
    pa.field("mid_close", pa.float64()),
    pa.field("spread_close", pa.float64()),
])

KINDS: Dict[str, Tuple[str, pa.Schema, int]] = {
    "l1": ("l1", L1_SCHEMA, 0),
    "bars_1s": ("bars_1s", BAR_SCHEMA, 1 * NS),
    "bars_1m": ("bars_1m", BAR_SCHEMA, 60 * NS),
}


def build_l1(events_path: Path, batch_size: int = 500_000) -> pa.Table:
    """Один потоковый проход: строка на каждое сообщение стакана, после которого вершина изменилась."""
    book = SimpleBook()
    ts_out: List[int] = []
    bp: List[float] = []
    bs: List[float] = []
    ap: List[float] = []
    asz: List[float] = []
    last = None
    started = False  # эмитим только после первого снапшота дня: до него книга неполна

    cur_key = None
    cur_ts = 0
    cur_snap = False
    rows: List[tuple] = []

    def flush() -> None:
        nonlocal cur_key, started, last
        if cur_key is None:
            return
        if cur_snap:
            book.reset()
            started = True
        for sd, p, s in rows:
            book.set(sd, p, s)
        book.finalize()
        rows.clear()
        cur_key = None
        if started and book.valid():
            top = (book.bb, book.bids[book.bb], book.ba, book.asks[book.ba])
            if top != last:
                last = top
                ts_out.append(cur_ts)
                bp.append(top[0]); bs.append(top[1]); ap.append(top[2]); asz.append(top[3])

    for batch in pq.ParquetFile(events_path).iter_batches(batch_size=batch_size, columns=COLUMNS):
        ts_l = batch.column("ts").cast(pa.int64()).to_pylist()
        et_l = batch.column("event_type").to_pylist()
        sd_l = batch.column("side").to_pylist()
        px_l = batch.column("price").to_pylist()
        sz_l = batch.column("size").to_pylist()
        id_l = batch.column("msg_id").to_pylist()
        for ts, et, sd, px, sz, mid in zip(ts_l, et_l, sd_l, px_l, sz_l, id_l):
            if et == TRADE:
                continue
            key = (mid, ts)
            if key != cur_key:
                flush()
                cur_key, cur_ts, cur_snap = key, ts, et == BOOK_SNAPSHOT
            rows.append((sd, px, sz))
    flush()

    return pa.table(
        {
            "ts": pa.array(ts_out, pa.int64()).cast(TS_TYPE),
            "bid_px": pa.array(bp, pa.float64()), "bid_sz": pa.array(bs, pa.float64()),
            "ask_px": pa.array(ap, pa.float64()), "ask_sz": pa.array(asz, pa.float64()),
        },
        schema=L1_SCHEMA,
    )


def read_trades(events_path: Path) -> Dict[str, np.ndarray]:
    """Только сделки дня (колоночное чтение с фильтром по типу события)."""
    t = pq.read_table(
        events_path, columns=["ts", "event_type", "side", "price", "size"],
        filters=[("event_type", "=", TRADE)],
    )
    return {
        "ts": t["ts"].cast(pa.int64()).to_numpy(),
        "side": t["side"].to_numpy(),
        "price": t["price"].to_numpy(),
        "size": t["size"].to_numpy(),
    }


def build_bars(trades: Dict[str, np.ndarray], interval_ns: int, l1: pa.Table) -> pa.Table:
    ts, side, price, size = trades["ts"], trades["side"], trades["price"], trades["size"]
    if len(ts) == 0:
        return BAR_SCHEMA.empty_table()
    # порядок внутри одной миллисекунды задан сортировкой событий (msg_id) — сохраняем его
    bucket = ts // interval_ns
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(ts)]

    # Суммы внутри группы (reduceat), а не разности кумулятивных сумм за день:
    # на больших накопленных значениях разности теряют точность (VWAP/imbalance вне диапазона).
    notional = price * size
    buy_size = np.where(side == BUY, size, 0.0)
    vol = np.add.reduceat(size, starts)
    buy = np.add.reduceat(buy_size, starts)
    sum_notional = np.add.reduceat(notional, starts)
    sell = vol - buy
    safe_vol = np.where(vol > 0, vol, 1.0)

    high = np.maximum.reduceat(price, starts)
    low = np.minimum.reduceat(price, starts)
    bar_ts = bucket[starts] * interval_ns

    # L1 на конец интервала (as-of): последняя запись с ts < конца бара
    mid_close = np.full(len(starts), np.nan)
    spread_close = np.full(len(starts), np.nan)
    if l1.num_rows:
        l1_ts = l1["ts"].cast(pa.int64()).to_numpy()
        idx = np.searchsorted(l1_ts, bar_ts + interval_ns, side="left") - 1
        ok = idx >= 0
        bid = l1["bid_px"].to_numpy()
        ask = l1["ask_px"].to_numpy()
        mid_close[ok] = (bid[idx[ok]] + ask[idx[ok]]) / 2.0
        spread_close[ok] = ask[idx[ok]] - bid[idx[ok]]

    return pa.table(
        {
            "ts": pa.array(bar_ts, pa.int64()).cast(TS_TYPE),
            "open": pa.array(price[starts]), "high": pa.array(high), "low": pa.array(low),
            "close": pa.array(price[ends - 1]),
            "volume": pa.array(vol),
            "vwap": pa.array(sum_notional / safe_vol),
            "buy_volume": pa.array(buy), "sell_volume": pa.array(sell),
            "imbalance": pa.array(np.clip((buy - sell) / safe_vol, -1.0, 1.0)),
            "trades": pa.array((ends - starts).astype(np.int32)),
            "mid_close": pa.array(mid_close, mask=np.isnan(mid_close)),
            "spread_close": pa.array(spread_close, mask=np.isnan(spread_close)),
        },
        schema=BAR_SCHEMA,
    )


def build_day(events_path: Path) -> Dict[str, pa.Table]:
    """Все производные датасеты дня: {kind: table}."""
    l1 = build_l1(events_path)
    trades = read_trades(events_path)
    return {
        "l1": l1,
        "bars_1s": build_bars(trades, KINDS["bars_1s"][2], l1),
        "bars_1m": build_bars(trades, KINDS["bars_1m"][2], l1),
    }
