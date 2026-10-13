"""
Web UI пайплайна данных. Работает поверх HTTP API (src/api/app.py).

  uvicorn src.api.app:app --port 8000
  streamlit run src/ui/dashboard.py
"""
import os
from datetime import date, timedelta

import httpx
import pandas as pd
import streamlit as st

API_URL = os.getenv("PIPELINE_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Data Pipeline", layout="wide")


def api(method: str, path: str, **kwargs):
    try:
        r = httpx.request(method, f"{API_URL}{path}", timeout=30, headers={"X-Client": "ui"}, **kwargs)
    except httpx.HTTPError as e:
        st.error(f"API недоступен ({API_URL}): {e}")
        st.stop()
    if r.status_code >= 400:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise RuntimeError(f"{r.status_code}: {detail}")
    return r.json()


@st.cache_data(ttl=60)
def load_meta() -> dict:
    return api("GET", "/meta")


@st.cache_data(ttl=3600, show_spinner="Загружаю список тикеров...")
def load_symbols(exchange: str, inst_type: str) -> list:
    try:
        return api("GET", f"/exchanges/{exchange}/symbols", params={"inst_type": inst_type})["symbols"]
    except RuntimeError:
        return []


meta = load_meta()
st.title("Data Pipeline")

# --- Форма запуска ---
with st.sidebar:
    st.header("Новая загрузка")
    exchange = st.selectbox("Биржа", meta["exchanges"])
    inst_type = st.selectbox("Тип инструмента", ["SPOT", "SWAP", "FUTURES"])
    all_symbols = load_symbols(exchange, inst_type)
    if all_symbols:
        default = [s for s in ("BTC-USDT", "BTC-USDT-SWAP") if s in all_symbols][:1]
        symbols = st.multiselect("Тикеры", all_symbols, default=default)
    else:
        st.caption("Список тикеров недоступен — введите вручную")
        symbols = [s.strip() for s in st.text_input("Тикеры (через запятую)", "BTC-USDT").split(",") if s.strip()]

    data_types = st.multiselect("Данные", meta["data_types"], default=meta["data_types"])
    mode = st.radio("Режим", ["backfill", "daily"], horizontal=True)

    yesterday = date.today() - timedelta(days=1)
    if mode == "backfill":
        rng = st.date_input("Период", (yesterday - timedelta(days=6), yesterday), max_value=yesterday)
        start, end = (rng if isinstance(rng, tuple) and len(rng) == 2 else (None, None))
    else:
        start, end = None, None
        st.caption("Скачает последний полный день (UTC)")

    stage = st.selectbox("Этапы", ["all", "ingest", "normalize"],
                         format_func={"all": "Скачать + нормализовать + проверить", "ingest": "Только скачать",
                                      "normalize": "Только нормализовать", "validate": "Только проверить"}.get)
    with st.expander("Дополнительно"):
        raw_dir = st.text_input("Папка сырых данных", meta["raw_dir"])
        processed_dir = st.text_input("Папка обработанных данных", meta["processed_dir"])
        concurrency = st.slider("Параллельных загрузок", 1, 16, 5)
        force = st.checkbox("Пересобрать нормализацию (force)")

    if st.button("Запустить", type="primary", width="stretch",
                 disabled=not symbols or not data_types or (mode == "backfill" and not start)):
        payload = {
            "exchange": exchange, "symbols": symbols, "mode": mode, "data_types": data_types, "stage": stage,
            "raw_dir": raw_dir, "processed_dir": processed_dir, "max_concurrent": concurrency, "force": force,
            "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
        }
        try:
            job = api("POST", "/jobs", json=payload)
            st.success(f"Задача {job['id']} запущена")
        except RuntimeError as e:
            st.error(str(e))

tab_jobs, tab_coverage, tab_quality, tab_schedule, tab_logs = st.tabs(
    ["Задачи", "Покрытие данных", "Качество данных", "Расписание", "Логи"])


# --- Задачи: автообновление только этого блока, страница не перерисовывается целиком ---
@st.fragment(run_every=2)
def jobs_panel():
    jobs = api("GET", "/jobs", params={"limit": 30})
    if not jobs:
        st.info("Задач пока нет")
        return

    for job in jobs:
        p = job["params"]
        period = p["start"] + " → " + p["end"] if p.get("start") else p["mode"]
        title = f"`{job['id']}` · {p['exchange']} {', '.join(p['symbols'])} · {', '.join(p['data_types'])} · {period}"
        icon = {"queued": "⏳", "running": "🔄", "done": "✅", "failed": "❌"}[job["status"]]
        with st.container(border=True):
            st.markdown(f"{icon} {title}")
            st.progress(job["progress"], text=f"{job['progress'] * 100:.0f}% · {job['message'] or ''}")
            if job["result"]:
                with st.expander("Отчет"):
                    st.code("\n".join(job["result"]["summary"]))
                    if job["result"]["failed"]:
                        st.dataframe(pd.DataFrame(job["result"]["failed"]), hide_index=True)


with tab_jobs:
    jobs_panel()

with tab_coverage:
    c1, c2 = st.columns([1, 3])
    cov_symbol = c1.text_input("Тикер", symbols[0] if symbols else "BTC-USDT")
    cov = api("GET", f"/coverage/{exchange}/{cov_symbol}")
    days = sorted(set(cov["raw"]["trades"]) | set(cov["raw"]["orderbook"]) | set(cov["events"]))
    if not days:
        c2.info("Данных нет")
    else:
        df = pd.DataFrame({
            "день": days,
            "trades (raw)": [d in cov["raw"]["trades"] for d in days],
            "orderbook (raw)": [d in cov["raw"]["orderbook"] for d in days],
            "events": [d in cov["events"] for d in days],
        })
        c2.dataframe(df, hide_index=True, width="stretch")

with tab_quality:
    q_symbol = st.text_input("Тикер", symbols[0] if symbols else "BTC-USDT", key="q_symbol")
    val = api("GET", f"/validation/{exchange}/{q_symbol}")
    val = {"days": [], "missing_days": [], "not_validated": [], **(val if isinstance(val, dict) else {})}
    icon = {"OK": "🟢", "WARN": "🟡", "FAIL": "🔴"}
    if not val["days"] and not val["not_validated"]:
        st.info("Данных нет. Запустите задачу со стадией «Только проверить» или «Скачать + нормализовать + проверить».")
    else:
        counts = {s: sum(1 for d in val["days"] if d["status"] == s) for s in ("OK", "WARN", "FAIL")}
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🟢 OK", counts["OK"])
        m2.metric("🟡 WARN", counts["WARN"])
        m3.metric("🔴 FAIL", counts["FAIL"])
        m4.metric("Пропущено дней", len(val["missing_days"]))
        if val["missing_days"]:
            st.error("Нет событий за дни внутри собранного диапазона: " + ", ".join(val["missing_days"]))
        if val["not_validated"]:
            st.warning("Не проверены: " + ", ".join(val["not_validated"]))
        for d in reversed(val["days"]):
            with st.expander(f"{icon[d['status']]} {d['day']} · {d['events']:,} событий · {d['snapshots']} снапшотов",
                             expanded=d["status"] != "OK"):
                st.dataframe(
                    pd.DataFrame([{"": icon[c["status"]], "проверка": c["name"], "результат": c["detail"]} for c in d["checks"]]),
                    hide_index=True, width="stretch")
                for c in d["checks"]:
                    if c["examples"]:
                        st.caption(f"Примеры — {c['name']}")
                        st.code("\n".join(c["examples"]))

with tab_schedule:
    sched = api("GET", "/schedule")
    cfg = sched["config"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Флаг enabled", "вкл" if sched["enabled"] else "выкл")
    c2.metric("Строка в crontab", "установлена" if sched["cron_installed"] else "нет")
    c3.metric("Ночной запуск", "будет выполняться" if sched["will_run"] else "не выполняется")
    if sched.get("cron_error"):
        st.warning(sched["cron_error"])
    if not sched["cron_installed"]:
        st.caption("Строка cron ставится на сервере вручную: `python -m src.scheduler install` "
                   "(из UI crontab намеренно не меняется).")

    with st.form("schedule"):
        enabled = st.toggle("Расписание включено", value=cfg["enabled"])
        f1, f2 = st.columns(2)
        sched_time = f1.text_input("Время запуска (HH:MM, время сервера)", cfg["time"],
                                   help="Применяется к crontab только после `python -m src.scheduler install`")
        lookback = f2.number_input("Перепроверять последних дней", 1, 30, int(cfg["lookback_days"]))
        sched_symbols = st.text_input("Тикеры (через запятую)", ", ".join(cfg["symbols"]))
        sched_types = st.multiselect("Данные", meta["data_types"], default=cfg["data_types"])
        if st.form_submit_button("Сохранить"):
            try:
                api("PATCH", "/schedule", json={
                    "enabled": enabled, "time": sched_time, "lookback_days": int(lookback),
                    "symbols": [x.strip() for x in sched_symbols.split(",") if x.strip()],
                    "data_types": sched_types,
                })
                st.success("Сохранено")
                st.rerun()
            except RuntimeError as e:
                st.error(str(e))

with tab_logs:
    f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
    log_q = f1.text_input("Поиск", placeholder="BTC-USDT, 429, job id...")
    log_levels = f2.multiselect("Уровень", ["INFO", "WARNING", "ERROR"], default=["WARNING", "ERROR"])
    log_source = f3.selectbox("Источник", ["", "cli", "cron", "api", "ui"], format_func=lambda x: x or "все")
    log_days = f4.number_input("Дней", 1, 30, 1)
    rows = api("GET", "/logs", params={"q": log_q or None, "level": log_levels, "source": log_source or None,
                                       "days": int(log_days), "limit": 1000})
    if not rows:
        st.info("Записей нет")
    else:
        df = pd.DataFrame(rows)[["time", "level", "source", "logger", "message"]]
        st.dataframe(df, hide_index=True, width="stretch", height=500)
