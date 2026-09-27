# Offline analysis и HTML report

Offline-режим позволяет сравнить два готовых DataFrame без Kafka, Prometheus и Docker: прогнать drift-анализ, выполнить adversarial validation и получить самодостаточный HTML-отчёт.

Типичные сценарии — ретроспективный анализ, разовая проверка нового датасета, включение drift-отчёта в CI или передача результата коллегам, у которых нет доступа к Grafana.

---

## Быстрый пример

```python
import pandas as pd

from drift_guardian.analyzer.offline.offline_mode import OfflineWrapper
from drift_guardian.config_handler.auto_config_builder import ConfigBuildOptions
from drift_guardian.reporting import generate_html_report

analyzer = OfflineWrapper(
    reference_df=pd.read_csv("data/reference.csv"),
    config_options=ConfigBuildOptions(
        prediction_enabled=True,
        prediction_score_column="prediction_score",
    ),
)

current_df = pd.read_csv("data/current.csv")

report = analyzer.analyze_df(current_df)
av_report = analyzer.run_av(current_df, prediction_col="prediction_score")

generate_html_report(
    report=report,
    av_report=av_report,
    dataset_name="Offline drift demo",
    output_path="reports/offline_drift_report.html",
)
```

Установка зависимостей:

```bash
uv sync --frozen
```

---

## `OfflineWrapper`

Обёртка над Core, принимающая reference как DataFrame вместо чтения CSV по `REFERENCE_DATA_PATH`.

```python
analyzer = OfflineWrapper(
    reference_df=reference_df,
    config_options=ConfigBuildOptions(...),
)
```

Конфиг строится автоматически по переданному reference — отдельный `config/config.yaml` для offline не требуется. Это отличие от realtime, где analyzer читает checked-in конфиг.

### `ConfigBuildOptions`

Управляет тем, что попадёт в автоматически построенный конфиг:

```python
ConfigBuildOptions(
    prediction_enabled=True,
    prediction_score_column="prediction_score",
)
```

По умолчанию prediction monitoring выключен — чтобы получить в отчёте блок Prediction Drift, нужно передать оба параметра явно.

Остальные опции те же, что у генератора конфига для realtime — см. [docs/configuration.md](configuration.md).

---

## `analyze_df()`

```python
report = analyzer.analyze_df(current_df)
```

Считает drift-метрики между reference и переданным DataFrame. Возвращает report-объект, пригодный для передачи в `generate_html_report()` или для сериализации в JSON.

Ограничений на размер `current_df` нет: в отличие от realtime здесь нет понятия `WINDOW_SIZE`, и весь переданный набор трактуется как одно окно. Соответственно, чем он больше, тем стабильнее метрики — на малых выборках PSI и KS шумят.

Набор метрик и thresholds — [docs/metrics.md](metrics.md).

---

## `run_av()`

```python
av_report = analyzer.run_av(current_df, prediction_col="prediction_score")
```

Выполняет adversarial validation: обучает LightGBM-классификатор, различающий reference и current, с cross-validation.

Два отличия от realtime:

**Нет расписания.** `interval_minutes` не действует — AV выполняется немедленно при каждом вызове.

**Prediction column исключается явно.** В realtime это происходит автоматически по конфигу, здесь нужен аргумент `prediction_col`. Без него `prediction_score` попадёт в feature set AV и почти наверняка окажется в топе drivers, обессмыслив ranking.

Интерпретация ROC AUC, CV diagnostics и driver consistency — [docs/adversarial-validation.md](adversarial-validation.md).

---

## `generate_html_report()`

```python
generate_html_report(
    report=report,
    av_report=av_report,
    dataset_name="Offline drift demo",
    output_path="reports/offline_drift_report.html",
)
```

| Параметр | Обязателен | Назначение |
|---|:---:|---|
| `report` | да | результат `analyze_df()` |
| `av_report` | нет | результат `run_av()` |
| `dataset_name` | нет | заголовок отчёта |
| `output_path` | да | путь для записи HTML |

Без `av_report` секция Adversarial Validation просто не рендерится — остальной отчёт формируется нормально.

### Состав отчёта

**Input drift summary** — сводный статус и общая картина по входным признакам.

**Feature-level таблицы** — значения метрик по каждой feature с применёнными thresholds. Для `chi2` подпись `χ² p-value`, чтобы значение не читалось по шкале «больше = хуже» — направление у этой метрики обратное.

**Prediction Drift** — отдельный блок со status-card и карточками prediction-метрик. Появляется только при `prediction_enabled=True`. Не смешивается с таблицами input features, поскольку input drift и prediction drift означают разные вещи — см. [docs/metrics.md](metrics.md#prediction-drift).

**Adversarial Validation** — ROC AUC и top-10 drift drivers.

Отчёт самодостаточен: один HTML-файл без внешних зависимостей, открывается в браузере и пересылается как обычный файл.

---

## CLI

Для рендера отчёта из ранее сохранённого JSON:

```bash
uv run drift-guardian-report reports/mock_drift_report.json reports/offline_drift_report.html \
  --dataset-name "Offline drift demo"
```

Аргументы позиционные: входной JSON, затем выходной HTML.

> **CLI не принимает `av_report`.** Секция Adversarial Validation будет отсутствовать. Для отчёта с AV используйте Python API или notebook.

Основное применение CLI — перерендерить отчёт из сохранённого результата, не пересчитывая метрики. Например, если нужно поменять `dataset_name` или отдать отчёт в другом оформлении.

---

## Notebook

```text
notebooks/02_offline_report.ipynb
```

Runnable end-to-end пример: подготовка данных, `analyze_df()`, `run_av()`, генерация отчёта.

Требует отдельной dependency group:

```bash
uv sync --frozen --group notebooks
```

---

## Как получить `current_df`

Offline-режим ничего не знает о том, откуда взялись данные — нужен любой DataFrame с теми же колонками, что в reference.

**Из CSV:**

```python
current_df = pd.read_csv("data/current.csv")
```

**Срез по времени из одной таблицы.** Частый случай — reference и current выделяются из одного исторического датасета:

```python
df = pd.read_csv("data/history.csv", parse_dates=["event_time"])

reference_df = df[df["event_time"] < "2026-08-01"]
current_df = df[df["event_time"] >= "2026-09-01"]
```

Колонку с временем стоит исключить из features конфига — иначе она сама будет выглядеть как drift по определению.

**Из Parquet, БД, любого источника.** Ограничение только одно: совпадение имён колонок с reference. Несовпадение проявится не ошибкой, а отсутствующей метрикой.

---

## Связь с realtime

Оба режима используют один analyzer/Core, поэтому drift-метрики и thresholds считаются идентично. Различия:

| | Offline | Realtime |
|---|---|---|
| источник reference | `reference_df` в конструкторе | CSV по `REFERENCE_DATA_PATH` |
| конфиг | строится из `ConfigBuildOptions` | `config/config.yaml` |
| источник current | любой DataFrame | Kafka window |
| размер окна | весь `current_df` | `WINDOW_SIZE` |
| расписание AV | нет, по вызову | `interval_minutes` |
| исключение prediction из AV | аргумент `prediction_col` | автоматически по конфигу |
| вывод | HTML-файл | Prometheus → Grafana |

Практическое следствие: offline-режим удобен для подбора thresholds. Прогоните несколько исторических периодов, посмотрите фактические значения метрик и уже осознанно задайте пороги в `config/config.yaml` для realtime.

---

## Диагностика

**Секция Adversarial Validation отсутствует в отчёте.** Либо `av_report` не передан в `generate_html_report()`, либо использовался CLI, который этот аргумент не поддерживает.

**Блок Prediction Drift не появился.** Проверьте, что в `ConfigBuildOptions` переданы оба параметра — `prediction_enabled=True` и `prediction_score_column`. По умолчанию prediction monitoring выключен.

**Метрики только для части колонок.** Конфиг строится по reference: колонки, отсутствующие в `reference_df`, не мониторятся. Сверьте наборы колонок обоих DataFrame.

**`run_av()` падает или даёт неинформативный результат.** Проверьте размер выборок — для cross-validation нужно достаточно строк в обеих. Требования — [docs/adversarial-validation.md](adversarial-validation.md).

**ROC AUC близок к 1.0, в топе drivers — одна колонка.** Признак утечки: в feature set попал идентификатор, timestamp или сама prediction column. Исключите такие колонки или передайте `prediction_col`.

Остальные сценарии — [docs/troubleshooting.md](troubleshooting.md).

---

## Связанные документы

- [docs/metrics.md](metrics.md) — drift-метрики, thresholds, prediction drift
- [docs/adversarial-validation.md](adversarial-validation.md) — AV, интерпретация метрик
- [docs/configuration.md](configuration.md) — `ConfigBuildOptions` и генерация конфига
- [docs/reference-data.md](reference-data.md) — подготовка reference
- [docs/realtime.md](realtime.md) — realtime-эквивалент