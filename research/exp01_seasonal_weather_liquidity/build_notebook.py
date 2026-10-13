"""Собирает notebook.ipynb (разбор результатов exp01). Запуск: python research/exp01_seasonal_weather_liquidity/build_notebook.py"""
from pathlib import Path
import nbformat as nbf

nb = nbf.v4.new_notebook()
nb.metadata["kernelspec"] = {"display_name": "backtest_strat (.venv)", "language": "python", "name": "backtest_strat"}
C = []
md = lambda s: C.append(nbf.v4.new_markdown_cell(s.strip("\n")))
code = lambda s: C.append(nbf.v4.new_code_cell(s.strip("\n")))

md("""
# exp01 — сезонность × погода × ликвидность: разбор результатов

Протокол и критерии зафиксированы в [README.md](README.md) до прогона. Журнал проблем — [PROBLEMS.md](PROBLEMS.md).
Ноутбук читает готовые результаты (`results/`), поэтому исполняется за секунды; пересчет — `run_study.py`.
""")

code("""
import json, sys, pathlib
ROOT = next(p for p in [pathlib.Path.cwd(), *pathlib.Path.cwd().parents] if (p / "src").is_dir())
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research/exp01_seasonal_weather_liquidity"))
import numpy as np, pandas as pd, matplotlib.pyplot as plt
pd.options.display.float_format = "{:,.2f}".format
pd.set_option("display.width", 200)
RES = ROOT / "research/exp01_seasonal_weather_liquidity/results"
sc = pd.read_csv(RES / "asset_scorecard.csv", index_col=0)
quality = pd.read_csv(RES / "data_quality.csv", index_col=0)
params = json.loads((RES / "params.json").read_text())
print("период:", params["start"], "→", params["end"], "| активов:", len(sc), "| часов на актив:", int(sc.hours.median()))
""")

md("## 1. Качество данных (до любых выводов)")
code("""
quality[["days_validated", "days_ok", "days_warn", "days_fail", "missing_days"]].T
""")
md("""
Все 16 активов × 181 день проверены, FAIL и пропущенных дней нет; WARN (0–5 дней на актив) — реальные события рынка
(например, обвал 3 января), а не дефекты данных. Данные пригодны для анализа.
""")

md("""
## 2. Калибровка тестов — обязательный шаг до интерпретации

На синтетике с известной истиной проверено, **умеет ли тест видеть эффект и не выдумывает ли его**.
Первая версия теста сезонности (циклический сдвиг сигнала) на этом шаге оказалась «слепой»: мощность 0 при эффекте 15 б.п.
(PROBLEMS.md, P12). Ниже — финальные тесты.
""")
code("""
cal = json.loads((RES / "calibration.json").read_text())
rows = []
for test, d in cal.items():
    for kind, scen in d.items():
        for name, r in scen.items():
            rows.append({"тест": test, "вид": kind, "сценарий": name, "p<0.05": r["rejects_p05"], "p<0.10": r["rejects_p10"]})
pd.DataFrame(rows).set_index(["тест", "вид", "сценарий"])
""")
md("""
**Как читать:** в «ложных срабатываниях» значения должны быть ≈ 0.05 / 0.10 (так и есть); в «мощности» — как можно ближе к 1.
Эффект в 15 б.п. в 4 часах суток обнаруживается в ~60% случаев, 30 б.п. — в ~100%: мощность достаточна, чтобы увидеть
эффект, способный покрыть издержки (≈ 20 б.п. на круг при taker 10 б.п.).
""")

md("## 3. Сезонность (M0): есть ли что-то у какого-то актива")
code("""
a = sc[["edge_bps", "edge_t", "edge_p", "edge_q", "M0_gross_bps_h1", "M0_gross_bps_h2", "active_hours"]].copy()
a["same_sign_Q1_Q2"] = np.sign(a.M0_gross_bps_h1) == np.sign(a.M0_gross_bps_h2)
a["passes"] = (a.edge_bps > 0) & (a.edge_q < 0.10) & a.same_sign_Q1_Q2
print("активов, прошедших критерий (edge > 0, q < 0.10, одинаковый знак в Q1 и Q2):", int(a.passes.sum()), "из", len(a))
a.sort_values("edge_p")
""")
code("""
fig, ax = plt.subplots(figsize=(9, 3.6))
s = sc.sort_values("edge_t")
ax.barh(s.index, s.edge_t, color=np.where(s.edge_t > 0, "#2a7", "#c53"))
for x in (-1.96, 1.96): ax.axvline(x, ls="--", c="gray", lw=.8)
ax.set_title("Сезонность: t-статистика валового edge по активам (пунктир — 5% без поправки)"); ax.grid(alpha=.3, axis="x")
""")
md("""
Ни один актив не проходит критерий. У 12 из 16 активов знак edge **отрицательный** (в среднем −1.9 б.п.): «повторять
профиль того же часа» хуже нуля. Но активы сильно коррелируют (среднее попарных корреляций дневных вкладов ≈ 0.37), поэтому
эффективно это не 12 независимых наблюдений; общий тест по активам — t ≈ −1.0, значимости нет. Это **наблюдение для новой,
заранее зафиксированной гипотезы на другом периоде**, а не находка.
""")

md("## 4. Погода и ликвидность: улучшают ли фильтры отбор часов")
code("""
b = sc[["weather_selectivity_bps", "weather_selectivity_p", "weather_selectivity_q", "reg_t_weather",
        "liq_selectivity_bps", "liq_selectivity_p", "liq_selectivity_q", "reg_t_liq"]]
print("сырых p<0.05 из 48 тестов (сезонность+погода+ликвидность):",
      int((sc.edge_p < .05).sum() + (sc.weather_selectivity_p < .05).sum() + (sc.liq_selectivity_p < .05).sum()),
      "| случайно ожидалось ≈", round(0.05 * 48, 1))
print("минимальные q:  погода %.2f | ликвидность %.2f" % (b.weather_selectivity_q.min(), b.liq_selectivity_q.min()))
b.sort_values("weather_selectivity_p")
""")
code("""
fig, ax = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
for a_, (col, title) in zip(ax, [("weather_selectivity_bps", "Погодный фильтр"), ("liq_selectivity_bps", "Фильтр ликвидности")]):
    s = sc.sort_values(col); a_.barh(s.index, s[col], color=np.where(s[col] > 0, "#2a7", "#c53"))
    a_.set_title(title + ": E[S·r | вкл] − E[S·r | выкл], б.п."); a_.grid(alpha=.3, axis="x")
""")
md("""
**Механика ложных находок — наглядно.** У BTC погодный фильтр «улучшает» отбор часов (+9.8 б.п., сырой p ≈ 0.02), а у XRP то же
самое с **обратным знаком** (−11.9 б.п., p ≈ 0.05). Поверхностно это два «значимых» результата; после поправки на 16 активов
(q ≈ 0.3) и с учетом противоположных знаков — шум. Без FDR-поправки и предрегистрации сюда легко дописать «погода работает на BTC».
""")

md("## 5. Издержки: что остается после комиссий")
code("""
v = ["M0", "M1", "M2", "M3"]
net10 = pd.DataFrame({k: sc[f"{k}_net_pnl_usd"] for k in v})
net2 = pd.DataFrame({k: sc[f"{k}_net_low_fee_pnl_usd"] for k in v})
gross = pd.DataFrame({k: sc[f"{k}_gross_bps_per_hour"] for k in v})
print("комбинаций актив×вариант:", net10.size)
print("PnL < 0 при taker 10 б.п.:", int((net10 < 0).sum().sum()), "| при 2 б.п.:", int((net2 < 0).sum().sum()))
pd.concat({"чистый PnL, USDT (10 б.п.)": net10, "чистый PnL, USDT (2 б.п.)": net2}, axis=1)
""")
code("""
fig, ax = plt.subplots(figsize=(8, 3.8))
x = np.arange(len(v)); w = 0.38
ax.bar(x - w/2, net10.median(), w, label="taker 10 б.п."); ax.bar(x + w/2, net2.median(), w, label="2 б.п.")
ax.set_xticks(x, v); ax.axhline(0, c="k", lw=.8); ax.legend(); ax.grid(alpha=.3, axis="y")
ax.set_title("Медианный по 16 активам чистый PnL на 1000 USDT за ~6 мес., USDT")
""")
md("""
При taker 10 б.п. **отрицательны все 64** комбинации, при 2 б.п. положительна одна — BTC×M1 (+163 USDT на 1000, 451 сделка),
у которой валовый результат статистически неотличим от нуля. Фильтры (M1–M3) «улучшают» PnL почти целиком тем, что реже
торгуют и платят меньше комиссий, а не тем, что лучше выбирают часы (см. селективность в разделе 4).
""")

md("## 6. Проверка механики движка: быстрая оценка против движка проекта")
code("""
d = pd.DataFrame({k: sc[f"{k}_engine_net_pnl_usd"] - sc[f"{k}_net_pnl_usd"] for k in v})
rel = pd.DataFrame({k: d[k].abs() / sc[f"{k}_engine_fees_usd"] for k in v})
print("макс |движок − быстрая оценка|: %.1f USDT; относительно комиссий: %.1f%%" % (d.abs().max().max(), rel.max().max() * 100))
d.describe().loc[["mean", "min", "max"]]
""")
md("""
Расхождение ≲ 5% комиссий. Оно систематическое (движок чуть пессимистичнее): постоянное количество вместо постоянной суммы
и округление котировок к тику. До учета тика расхождение достигало 10% (PROBLEMS.md, P11) — пример того, как независимая
вторая оценка находит упрощение в первой.
""")

md("""
## 7. Решение по критериям из README

| Критерий | Порог | Факт | Выполнен |
|---|---|---|---|
| Сезонность: edge > 0, q < 0.10, знак совпадает в Q1/Q2 | ≥ 1 актив | 0 из 16 (мин. q = 0.43) | нет |
| Погода: selectivity > 0, q < 0.10 | ≥ 1 актив | 0 из 16 (мин. q = 0.32) | нет |
| Ликвидность: selectivity > 0, q < 0.10 | ≥ 1 актив | 0 из 16 (мин. q = 0.29) | нет |
| Торгуемость: чистый PnL > 0 при 10 и 2 б.п. | ≥ 1 актив×вариант | 0 из 64 | нет |

**Вывод: гипотеза в заданной формулировке отклонена; активов, на которых она работает, не найдено.**

**Что это означает честно, а не оптимистично:**
- Это отрицательный результат на одном периоде (янв–июн 2024), 16 активах, одной спецификации сигнала. Он не доказывает отсутствие
  сезонности вообще: часовые эффекты размером менее ~15 б.п. дизайн не обнаружит (раздел 2), а на таких размерах они и не торгуемы
  при комиссии taker.
- Погодный фактор не показал ничего, но и априорное обоснование у него было самым слабым звеном.
- Единственная нестабильность, похожая на сигнал (TRX: t ≈ 1.7, весь эффект в январе–феврале), не проходит критерий устойчивости.

**Что дальше (каждый пункт — отдельный эксперимент со своей предрегистрацией):**
1. Проверить наблюдение «знак сезонного сигнала отрицателен у 12/16» на **другом периоде** (июль–декабрь 2024) как новую гипотезу.
2. Для торгуемости нужны либо другие классы стратегий (maker, меньший оборот), либо инструменты с нулевой комиссией — иначе издержки
   доминируют над любым часовым эффектом такого масштаба.
3. Если погода интересна как фактор — для товарных/энергетических активов, где канал причинности физический.
""")

nb.cells = C
nbf.write(nb, Path(__file__).with_name("notebook.ipynb"))
print("written")
