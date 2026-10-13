"""
Монитор процесса: состояние пайплайна и исследований по файлам на диске.

  streamlit run backtest_strat/ui/monitor.py

Ничего не требует от API и не зависит от того, как запущена работа (CLI, cron, дашборд, ноутбук):
прогресс считается по результатам на диске. Обновляется каждые 2 секунды.
"""
import json
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path.cwd()   # папка проекта, из которой запущен монитор

from backtest_strat.paths import PROCESSED_DIR, RAW_DIR  # noqa: E402
from backtest_strat.utils.logging_setup import read_logs  # noqa: E402

st.set_page_config(page_title="Монитор пайплайна", layout="wide")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _dates(paths) -> set:
    out = set()
    for p in paths:
        m = DATE_RE.search(p.name)
        if m:
            out.add(date.fromisoformat(m.group(1)))
    return out


@st.cache_data(ttl=2, show_spinner=False)
def scan_symbol(raw_dir: str, processed_dir: str, exchange: str, symbol: str, start: str, end: str) -> dict:
    lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    in_range = lambda s: {d for d in s if lo <= d <= hi + timedelta(days=1)}
    raw = Path(raw_dir) / exchange / symbol
    proc = Path(processed_dir) / exchange / symbol

    def count(sub: str, pattern: str) -> set:
        return in_range(_dates((raw if sub.startswith("raw/") else proc).glob(pattern)))

    raw_trades = in_range(_dates((raw / "trades").glob("*.zip"))) if (raw / "trades").exists() else set()
    raw_ob = in_range(_dates((raw / "orderbook").glob("*.tar.gz"))) if (raw / "orderbook").exists() else set()
    parts = list((raw / "trades").glob("*.part")) + list((raw / "orderbook").glob("*.part"))
    norm_trades = in_range(_dates(proc.glob("trades/year=*/month=*/*.parquet")))
    bars = {d for d in _dates(proc.glob("bars_1m/year=*/month=*/*.parquet")) if lo <= d <= hi}
    l1 = {d for d in _dates(proc.glob("l1/year=*/month=*/*.parquet")) if lo <= d <= hi}

    status = {"OK": 0, "WARN": 0, "FAIL": 0}
    for f in (proc / "validation").glob("*.json"):
        d = DATE_RE.search(f.name)
        if d and lo <= date.fromisoformat(d.group(1)) <= hi:
            try:
                status[json.loads(f.read_text())["report"]["status"]] += 1
            except (json.JSONDecodeError, KeyError):
                pass
    return {
        "raw_trades": len(raw_trades), "raw_orderbook": len(raw_ob), "downloading": len(parts),
        "normalized": len(norm_trades), "bars_1m": len(bars), "l1": len(l1), **{f"val_{k}": v for k, v in status.items()},
    }


def running_processes() -> pd.DataFrame:
    out = subprocess.run(["ps", "-eo", "pid,etime,command"], capture_output=True, text=True).stdout.splitlines()[1:]
    keys = ("backtest_strat.cli", "run_study", "backtest_strat.scheduler", "uvicorn", "run_backtest")
    rows = []
    for line in out:
        if any(k in line for k in keys) and "monitor.py" not in line and "grep" not in line:
            pid, etime, cmd = line.strip().split(None, 2)
            rows.append({"pid": pid, "время": etime, "команда": cmd[:140]})
    return pd.DataFrame(rows)


with st.sidebar:
    st.header("Что смотреть")
    exchange = st.text_input("Биржа", "okx")
    raw_dir = st.text_input("Сырые данные", str(ROOT / RAW_DIR))
    processed_dir = st.text_input("Обработанные", str(ROOT / PROCESSED_DIR))
    c1, c2 = st.columns(2)
    start = c1.date_input("С", date(2024, 1, 1))
    end = c2.date_input("По", date(2024, 6, 30))
    known = sorted(p.name for p in (Path(raw_dir) / exchange).glob("*") if p.is_dir())
    symbols = st.multiselect("Тикеры", known, default=known)
    refresh = st.toggle("Автообновление (2 с)", True)

expected = (end - start).days + 1
st.title("Монитор пайплайна")
st.caption(f"Ожидается {expected} суток на тикер · данные читаются с диска, источник запуска не важен")


@st.fragment(run_every=2 if refresh else None)
def panel() -> None:
    rows = [{"symbol": s, **scan_symbol(raw_dir, processed_dir, exchange, s, start.isoformat(), end.isoformat())} for s in symbols]
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("Нет данных за выбранный период")
        return

    tot = lambda c: int(df[c].sum())
    n = max(len(df), 1)
    m = st.columns(5)
    m[0].metric("Скачано сделок", f"{tot('raw_trades')} / {expected * n}")
    m[1].metric("Нормализовано", f"{tot('normalized')} / {expected * n}")
    m[2].metric("Баров 1m (дней)", f"{tot('bars_1m')} / {expected * n}")
    m[3].metric("Проверено OK/WARN/FAIL", f"{tot('val_OK')}/{tot('val_WARN')}/{tot('val_FAIL')}")
    m[4].metric("Файлов в загрузке", tot("downloading"))

    overall = (tot("raw_trades") + tot("normalized") + tot("bars_1m")) / (3 * expected * n)
    st.progress(min(overall, 1.0), text=f"Общий прогресс данных: {overall:.0%}")

    view = df.set_index("symbol").copy()
    for c in ("raw_trades", "normalized", "bars_1m"):
        view[c] = view[c] / expected
    st.dataframe(
        view[["raw_trades", "normalized", "bars_1m", "val_OK", "val_WARN", "val_FAIL", "downloading", "raw_orderbook", "l1"]],
        width="stretch",
        column_config={
            "raw_trades": st.column_config.ProgressColumn("скачано", min_value=0, max_value=1, format="percent"),
            "normalized": st.column_config.ProgressColumn("нормализовано", min_value=0, max_value=1, format="percent"),
            "bars_1m": st.column_config.ProgressColumn("бары 1m", min_value=0, max_value=1, format="percent"),
            "val_OK": st.column_config.NumberColumn("🟢"), "val_WARN": st.column_config.NumberColumn("🟡"),
            "val_FAIL": st.column_config.NumberColumn("🔴"),
            "downloading": st.column_config.NumberColumn("в загрузке"),
            "raw_orderbook": st.column_config.NumberColumn("стакан, дн."),
            "l1": st.column_config.NumberColumn("L1, дн."),
        },
    )

    st.subheader("Исследования")
    found = False
    for pf in sorted(ROOT.glob("research/*/results/progress.json")):
        found = True
        pr = json.loads(pf.read_text())
        st.progress(pr.get("fraction", 0.0), text=f"{pf.parents[1].name}: {pr.get('stage', '')} — {pr.get('message', '')}")
    if not found:
        st.caption("Нет запущенных исследований (results/progress.json)")

    st.subheader("Процессы")
    procs = running_processes()
    st.dataframe(procs, hide_index=True, width="stretch") if len(procs) else st.caption("Нет активных процессов пайплайна")

    st.subheader("Последние события (WARNING/ERROR)")
    logs = read_logs(days=1, levels={"WARNING", "ERROR"}, limit=12)
    if logs:
        st.dataframe(pd.DataFrame(logs)[["time", "level", "source", "message"]], hide_index=True, width="stretch")
    st.caption(f"Обновлено {time.strftime('%H:%M:%S')}")


panel()
