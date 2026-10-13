"""
Адаптер «бары -> события» для низкочастотных исследований.

Движок принимает только поток событий EVENT_SCHEMA (L2). Для стратегий с шагом часы/сутки
стакан не нужен и не всегда есть (качать L2 по десяткам активов — сотни ГБ). Адаптер строит из баров
синтетический стакан: на закрытии каждой минутной свечи — снапшот с одним уровнем на сторону
(bid = close - half_spread, ask = close + half_spread, объем «бесконечный»).

ЧТО ЭТО ДАЕТ: те же Strategy/Context/Portfolio/комиссии/задержки, один движок на все альфы.
ЧЕГО НЕ ДАЕТ (ограничения, учитывать при интерпретации):
  * реальную глубину и проскальзывание от размера — объем на уровне неограничен;
  * реальный спред — задан параметром (half_spread_bps), а не наблюдаем;
  * внутрибарные движения: между закрытиями книга неподвижна;
  * исполнение пассивных ордеров — модель очереди бессмысленна на синтетической книге.
Подходит для проверки сигналов на позициях небольшого размера по ликвидным активам; не подходит
для оценки маркет-мейкинга и крупных размеров.
"""
from typing import Optional

import numpy as np
import pyarrow as pa

from backtest_strat.normalizers.schema import BOOK_SNAPSHOT, BUY, SELL

TS_TYPE = pa.timestamp("ns", tz="UTC")
BIG_SIZE = 1e12


def bars_to_events(
    bar_ts_ns: np.ndarray,
    close: np.ndarray,
    interval_ns: int,
    tick_size: float,
    half_spread_bps: float,
    fill_gaps: bool = True,
) -> pa.RecordBatch:
    """
    bar_ts_ns — начало бара (отсортировано), close — цена закрытия.
    Снапшот ставится на момент закрытия бара (ts + interval): стратегия узнает close только тогда.
    fill_gaps: интервалы без сделок получают прошлый close (цена «стоит»), иначе пропускаются.
    """
    ts = np.asarray(bar_ts_ns, dtype=np.int64)
    px = np.asarray(close, dtype=np.float64)
    if fill_gaps and len(ts):
        grid = np.arange(ts[0], ts[-1] + interval_ns, interval_ns, dtype=np.int64)
        idx = np.searchsorted(ts, grid, side="right") - 1  # последний известный бар на каждый узел сетки
        ts, px = grid, px[idx]

    half = np.maximum(px * half_spread_bps / 1e4, tick_size)
    bid = np.floor((px - half) / tick_size + 1e-9) * tick_size
    ask = np.ceil((px + half) / tick_size - 1e-9) * tick_size
    bid = np.round(bid, 10)
    ask = np.round(ask, 10)

    n = len(ts)
    close_ts = ts + interval_ns
    return pa.RecordBatch.from_arrays(
        [
            pa.array(np.repeat(close_ts, 2), pa.int64()).cast(TS_TYPE),
            pa.array(np.full(2 * n, BOOK_SNAPSHOT, np.int8)),
            pa.array(np.tile(np.array([BUY, SELL], np.int8), n)),
            pa.array(np.column_stack([bid, ask]).ravel()),
            pa.array(np.full(2 * n, BIG_SIZE)),
            pa.array(np.repeat(np.arange(n, dtype=np.int64), 2)),
        ],
        names=["ts", "event_type", "side", "price", "size", "msg_id"],
    )
