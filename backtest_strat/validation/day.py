"""
Проверка качества одного дня событий (data/processed/<ex>/<sym>/events/.../<YYYY-MM-DD>.parquet).

Один потоковый проход: восстанавливаем книгу и на ходу считаем проверки.
Книга здесь облегченная (цена -> объем, без тиков), чтобы валидация не зависела от
параметров инструмента.

Проверки:
  crossed_book     лучший bid >= лучшего ask после применения сообщения          (FAIL при > 0)
  book_gap         пауза между сообщениями стакана больше порога                  (WARN > gap_warn_s, FAIL > gap_fail_s)
  no_initial_book  день не начинается со снапшота: книга неполна до первого снапшота
                   (WARN, если снапшот в пределах first_snapshot_warn_s; иначе FAIL)
  trade_vs_book    сделка-покупка ниже лучшего bid / продажа выше лучшего ask     (WARN при доле > trade_outside_warn)
  price_spike      скачок mid > spike_bps за 1 с с возвратом >= 80% за 5 с         (WARN при наличии)
  non_positive     цена или объем <= 0 у сделки, отрицательный объем в стакане    (FAIL)
  snapshot_drift   расхождение восстановленной книги со снапшотом OKX (инфо)      (WARN при best_price_mismatch > 5%)
"""
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from backtest_strat.normalizers.schema import BOOK_SNAPSHOT, BUY, SELL, TRADE
from backtest_strat.utils.simple_book import SimpleBook as _Book

NS = 1_000_000_000
COLUMNS = ["ts", "event_type", "side", "price", "size", "msg_id"]
OK, WARN, FAIL = "OK", "WARN", "FAIL"
_RANK = {OK: 0, WARN: 1, FAIL: 2}


@dataclass(frozen=True)
class Thresholds:
    gap_warn_s: float = 30.0
    gap_fail_s: float = 300.0
    initial_book_max_s: float = 60.0      # первое сообщение стакана не позже
    first_snapshot_warn_s: float = 120.0  # первый снапшот не позже
    trade_outside_warn: float = 0.01      # доля сделок вне спреда
    spike_bps: float = 50.0
    spike_reversal: float = 0.8
    spike_reversal_window_s: int = 5
    snapshot_best_price_warn: float = 0.05
    max_examples: int = 5


@dataclass
class Check:
    name: str
    status: str
    detail: str
    value: float = 0.0
    examples: List[str] = field(default_factory=list)


@dataclass
class DayReport:
    day: str
    status: str
    events: int
    book_messages: int
    trades: int
    snapshots: int
    first_ts: Optional[str]
    last_ts: Optional[str]
    checks: List[dict]
    stats: Dict[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / NS, tz=timezone.utc).isoformat(timespec="milliseconds")


def validate_events_file(path: Path, th: Thresholds = Thresholds()) -> DayReport:
    day = path.stem
    book = _Book()
    pf = pq.ParquetFile(path)

    events = book_msgs = trades = snapshots = 0
    first_ts = last_ts = None
    first_book_ts: Optional[int] = None
    first_book_is_snapshot = False
    first_snapshot_ts: Optional[int] = None
    last_msg_ts: Optional[int] = None

    crossed = 0
    crossed_ex: List[str] = []
    max_gap = 0.0
    gap_warn = gap_fail = 0
    gap_ex: List[str] = []
    non_pos = 0
    non_pos_ex: List[str] = []
    trades_checked = trades_outside = 0
    outside_ex: List[str] = []

    snap_checks = snap_price_mismatch = snap_qty_mismatch = 0

    # детектор скачков: mid раз в секунду
    last_sample_sec: Optional[int] = None
    mids: Deque[tuple] = deque(maxlen=th.spike_reversal_window_s + 2)  # (sec, mid)
    spikes = 0
    spike_ex: List[str] = []
    pending_spike: Optional[tuple] = None  # (sec, mid_before, mid_after)

    # буфер текущего сообщения стакана (уровни могут лежать в соседних батчах)
    cur_key = None
    cur_ts = 0
    cur_snap = False
    rows: List[tuple] = []

    def flush() -> None:
        nonlocal cur_key, book_msgs, snapshots, first_book_ts, first_book_is_snapshot, last_msg_ts, first_snapshot_ts
        nonlocal crossed, max_gap, gap_warn, gap_fail, non_pos
        nonlocal snap_checks, snap_price_mismatch, snap_qty_mismatch
        if cur_key is None:
            return
        book_msgs += 1
        if cur_snap:
            snapshots += 1
            if first_snapshot_ts is None:
                first_snapshot_ts = cur_ts
        if first_book_ts is None:
            first_book_ts, first_book_is_snapshot = cur_ts, cur_snap

        if last_msg_ts is not None:
            gap = (cur_ts - last_msg_ts) / NS
            if gap > max_gap:
                max_gap = gap
            if gap > th.gap_fail_s:
                gap_fail += 1
                if len(gap_ex) < th.max_examples:
                    gap_ex.append(f"{_iso(last_msg_ts)} → {_iso(cur_ts)} ({gap:.0f}s)")
            elif gap > th.gap_warn_s:
                gap_warn += 1
                if len(gap_ex) < th.max_examples:
                    gap_ex.append(f"{_iso(last_msg_ts)} → {_iso(cur_ts)} ({gap:.0f}s)")
        last_msg_ts = cur_ts

        if cur_snap and book.valid():
            sb = {p: s for sd, p, s in rows if sd == BUY and s > 0}
            sa = {p: s for sd, p, s in rows if sd == SELL and s > 0}
            if sb and sa:
                for ours, snap, pick in ((book.bb, sb, max), (book.ba, sa, min)):
                    snap_checks += 1
                    theirs = pick(snap)
                    if ours != theirs:
                        snap_price_mismatch += 1
                    else:
                        o = (book.bids if pick is max else book.asks).get(ours, 0.0)
                        if abs(o - snap[theirs]) > 1e-9:
                            snap_qty_mismatch += 1

        if cur_snap:
            book.reset()
        for sd, p, s in rows:
            if s < 0:
                non_pos += 1
                if len(non_pos_ex) < th.max_examples:
                    non_pos_ex.append(f"{_iso(cur_ts)} book side={sd} price={p} size={s}")
            book.set(sd, p, s)
        book.finalize()

        if book.valid() and book.bb >= book.ba:
            crossed += 1
            if len(crossed_ex) < th.max_examples:
                crossed_ex.append(f"{_iso(cur_ts)} bid={book.bb} ask={book.ba}")
        cur_key = None
        rows.clear()

    def sample_mid(ts: int) -> None:
        nonlocal last_sample_sec, spikes, pending_spike
        if not book.valid():
            return
        sec = ts // NS
        if last_sample_sec is not None and sec == last_sample_sec:
            return
        last_sample_sec = sec
        mid = (book.bb + book.ba) / 2.0
        if pending_spike is not None:
            s0, before, after = pending_spike
            if sec - s0 <= th.spike_reversal_window_s:
                move = after - before
                back = after - mid
                if move != 0 and back / move >= th.spike_reversal:
                    spikes += 1
                    if len(spike_ex) < th.max_examples:
                        spike_ex.append(f"{_iso(s0 * NS)} {before:.2f} → {after:.2f} → {mid:.2f}")
                    pending_spike = None
            else:
                pending_spike = None
        if mids and pending_spike is None:
            prev_sec, prev_mid = mids[-1]
            if 1 <= sec - prev_sec <= 2 and prev_mid > 0 and abs(mid / prev_mid - 1) * 1e4 >= th.spike_bps:
                pending_spike = (sec, prev_mid, mid)
        mids.append((sec, mid))

    for batch in pf.iter_batches(batch_size=500_000, columns=COLUMNS):
        ts_l = batch.column("ts").cast(pa.int64()).to_pylist()
        et_l = batch.column("event_type").to_pylist()
        sd_l = batch.column("side").to_pylist()
        px_l = batch.column("price").to_pylist()
        sz_l = batch.column("size").to_pylist()
        id_l = batch.column("msg_id").to_pylist()
        events += len(ts_l)
        if first_ts is None and ts_l:
            first_ts = ts_l[0]
        if ts_l:
            last_ts = ts_l[-1]

        for ts, et, sd, px, sz, mid in zip(ts_l, et_l, sd_l, px_l, sz_l, id_l):
            if et == TRADE:
                flush()
                trades += 1
                if px <= 0 or sz <= 0:
                    non_pos += 1
                    if len(non_pos_ex) < th.max_examples:
                        non_pos_ex.append(f"{_iso(ts)} trade price={px} size={sz}")
                if book.valid():
                    trades_checked += 1
                    # покупатель не может исполниться ниже лучшего bid, продавец — выше лучшего ask
                    if (sd == BUY and px < book.bb) or (sd == SELL and px > book.ba):
                        trades_outside += 1
                        if len(outside_ex) < th.max_examples:
                            outside_ex.append(f"{_iso(ts)} side={sd} price={px} bid={book.bb} ask={book.ba}")
                continue
            key = (mid, ts)
            if key != cur_key:
                flush()
                cur_key, cur_ts, cur_snap = key, ts, et == BOOK_SNAPSHOT
                sample_mid(ts)
            rows.append((sd, px, sz))
    flush()

    checks: List[Check] = []

    # --- структура дня ---
    if events == 0:
        checks.append(Check("no_events", FAIL, "Файл событий пуст"))
    else:
        day_start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * NS
        if first_book_ts is None:
            checks.append(Check("no_initial_book", FAIL, "В файле нет сообщений стакана"))
        elif first_snapshot_ts is None:
            checks.append(Check("no_initial_book", FAIL, "В файле нет ни одного снапшота стакана: книгу восстановить нельзя"))
        else:
            first_delay = (first_book_ts - day_start) / NS
            snap_delay = (first_snapshot_ts - day_start) / NS
            if first_book_is_snapshot and first_delay <= th.initial_book_max_s:
                checks.append(Check("no_initial_book", OK, f"Снапшот через {first_delay:.2f}s после начала суток", value=first_delay))
            else:
                st = WARN if snap_delay <= th.first_snapshot_warn_s else FAIL
                checks.append(Check(
                    "no_initial_book", st,
                    f"Файл начинается с апдейтов: книга неполна первые {snap_delay:.0f}s суток "
                    f"(первый снапшот в {_iso(first_snapshot_ts)}). Бэктест стартует с этого снапшота.",
                    value=snap_delay))

    checks.append(Check(
        "crossed_book", FAIL if crossed else OK,
        f"{crossed} сообщений с bid >= ask" if crossed else "Перевернутой книги нет",
        value=crossed, examples=crossed_ex))

    gap_status = FAIL if gap_fail else WARN if gap_warn else OK
    checks.append(Check(
        "book_gap", gap_status,
        f"Макс. пауза {max_gap:.1f}s; > {th.gap_warn_s:.0f}s: {gap_warn + gap_fail}, > {th.gap_fail_s:.0f}s: {gap_fail}",
        value=max_gap, examples=gap_ex))

    share = trades_outside / trades_checked if trades_checked else 0.0
    checks.append(Check(
        "trade_vs_book", WARN if share > th.trade_outside_warn else OK,
        f"{trades_outside} из {trades_checked} сделок вне спреда ({share:.2%})", value=share, examples=outside_ex))

    checks.append(Check(
        "price_spike", WARN if spikes else OK,
        f"{spikes} скачков > {th.spike_bps:.0f} б.п. за 1s с возвратом" if spikes else "Скачков с возвратом нет",
        value=spikes, examples=spike_ex))

    checks.append(Check(
        "non_positive", FAIL if non_pos else OK,
        f"{non_pos} записей с некорректными ценой/объемом" if non_pos else "Некорректных значений нет",
        value=non_pos, examples=non_pos_ex))

    price_rate = snap_price_mismatch / snap_checks if snap_checks else 0.0
    qty_rate = snap_qty_mismatch / snap_checks if snap_checks else 0.0
    checks.append(Check(
        "snapshot_drift", WARN if price_rate > th.snapshot_best_price_warn else OK,
        f"Сверка со снапшотами ({snap_checks} проверок): лучшая цена расходится в {price_rate:.1%}, "
        f"объем на лучшем уровне — в {qty_rate:.1%}",
        value=price_rate))

    worst = max((c.status for c in checks), key=lambda s: _RANK[s], default=OK)
    return DayReport(
        day=day, status=worst, events=events, book_messages=book_msgs, trades=trades, snapshots=snapshots,
        first_ts=_iso(first_ts) if first_ts is not None else None,
        last_ts=_iso(last_ts) if last_ts is not None else None,
        checks=[asdict(c) for c in checks],
        stats={
            "max_gap_s": max_gap, "trades_outside_share": share, "snapshot_best_price_mismatch": price_rate,
            "snapshot_best_qty_mismatch": qty_rate, "spikes": float(spikes), "crossed": float(crossed),
        },
    )
