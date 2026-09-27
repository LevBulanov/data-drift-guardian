# Configuration

Drift Guardian управляется одним YAML-файлом. Он описывает **что мониторить**
(фичи и их типы), **какими метриками** и **при каких значениях считать это
дрейфом** (thresholds).

Есть два способа получить такой файл:

| Способ | Когда использовать |
|---|---|
| Написать `config.yaml` руками | Мало фичей, пороги известны заранее |
| Сгенерировать через `tools/build_config.py` | Много фичей, пороги нужно откалибровать по reference-данным |

Второй способ — рекомендуемый. Билдер сам определит типы колонок, подберёт
набор метрик и посчитает пороги по квантилям распределения метрик на
reference-данных. Рекомендуем воспользоваться инструкциями в самом build_config.py

---

## Содержание

- [Структура config.yaml](#структура-configyaml)
  - [features](#features)
  - [thresholds — глобальные пороги](#thresholds--глобальные-пороги)
  - [Локальные пороги фичи](#локальные-пороги-фичи)
  - [prediction_metrics](#prediction_metrics)
  - [stream_drift](#stream_drift)
  - [adversarial_validation](#adversarial_validation)
- [Доступные метрики](#доступные-метрики)
- [Reversed-метрики](#reversed-метрики)
- [Приоритет порогов](#приоритет-порогов)
- [Генерация конфига: build_config.py](#генерация-конфига-build_configpy)
  - [Вход и выход](#вход-и-выход)
  - [1. Какие колонки мониторить](#1-какие-колонки-мониторить)
  - [2. Какие метрики считать](#2-какие-метрики-считать)
  - [3. Пороги вручную](#3-пороги-вручную)
  - [4. Автокалибровка порогов](#4-автокалибровка-порогов)
  - [5. Мониторинг предсказаний](#5-мониторинг-предсказаний)
  - [6. Stream drift](#6-stream-drift)
  - [7. Adversarial validation](#7-adversarial-validation)
  - [8. Profiler](#8-profiler)
  - [9. Прочее и strict-флаги](#9-прочее-и-strict-флаги)
- [Вывод скрипта](#вывод-скрипта)
- [Типовые ошибки](#типовые-ошибки)
- [Рецепты](#рецепты)

---

## Структура config.yaml

Полный пример со всеми блоками:

```yaml
features:
  age:
    type: numeric
    metrics:
      - missing_rate
      - psi
    thresholds:            # Локальный override только для этой feature.
      psi:
        warning: 0.05
        critical: 0.12

  income:
    type: numeric
    metrics:
      - missing_rate
      - psi
    # Локальные thresholds не заданы: используются глобальные значения.

  country:
    type: categorical
    metrics:
      - missing_rate
      - psi
      - unseen_category_rate
      - cardinality_ratio

prediction_metrics:
  enabled: true
  score_column: prediction_score
  type: numeric
  metrics:
    - psi

thresholds:               # глобальные дефолты
  missing_rate:
    warning: 0.02
    critical: 0.05
  psi:
    warning: 0.1
    critical: 0.25
  unseen_category_rate:
    warning: 0.05
    critical: 0.1
  cardinality_ratio:
    warning: 0.3
    critical: 0.6

stream_drift:
  drift_event_time_lag_seconds:
    warning: 30
    critical: 120
  # Window-local rate. Lifetime counter late_events_total не используется
  # как текущий health signal.
  drift_late_event_rate:
    warning: 0.01
    critical: 0.05
  # Остальные поля не заданы и не участвуют в расчёте stream_status.

adversarial_validation:
  enabled: true
  interval_minutes: 30
  # Остальные параметры опциональны.
  max_samples: 50000
  n_splits: 5
  random_state: 42
  missing_category: "__missing__"
  lightgbm:
    n_estimators: 200
    learning_rate: 0.03
    max_depth: 4
    num_leaves: 15
    subsample: 0.8
    colsample_bytree: 0.8
    reg_alpha: 1.0
    reg_lambda: 1.0
    random_state: 42
```

---

### `features`

Словарь: имя колонки → описание.

| Поле | Обязательно | Описание |
|---|:---:|---|
| `type` | да | `numeric` или `categorical` |
| `metrics` | да | Список метрик, совместимых с типом |
| `thresholds` | нет | Локальный override порогов для этой фичи |

```yaml
features:
  age:
    type: numeric
    metrics: [missing_rate, psi]
```

Если у фичи не остаётся ни одной метрики, фича бессмысленна — билдер по
умолчанию выкидывает её из конфига (см. `drop_features_without_metrics`).

> **Внимание.** Фича, указанная в конфиге, но отсутствующая в
> reference-профиле, приведёт к тому, что правило будет молча пропущено.
> Держите `config.yaml` и `reference.csv` согласованными.

---

### `thresholds` — глобальные пороги

Пороги на **метрику**, применяемые ко всем фичам, у которых нет локального
override.

```yaml
thresholds:
  psi:
    warning: 0.1
    critical: 0.25
```

Формат значения — всегда пара `warning` / `critical`.

---

### Локальные пороги фичи

Блок `thresholds` внутри фичи перекрывает глобальный для этой пары
(фича, метрика):

```yaml
features:
  age:
    type: numeric
    metrics: [missing_rate, psi]
    thresholds:
      psi:                 # только для age
        warning: 0.05
        critical: 0.12
    # missing_rate возьмёт глобальные пороги
```

---

### `prediction_metrics`

Отдельный блок для колонки со скором или меткой класса модели. Дрейф
предсказаний часто заметен раньше, чем дрейф отдельных фичей.

| Поле | Описание |
|---|---|
| `enabled` | Включает блок |
| `score_column` | Имя колонки с предсказанием. Обязательно при `enabled: true` |
| `type` | `numeric` (вероятность, регрессия) или `categorical` (метка класса) |
| `metrics` | Список метрик |
| `thresholds` | Пороги (опционально) |

```yaml
prediction_metrics:
  enabled: true
  score_column: prediction_score
  type: numeric
  metrics: [psi]
```

Колонка предсказания **автоматически исключается из обычных фичей**, чтобы
метрики по ней не считались дважды.

---

### `stream_drift`

Пороги для потоковых (realtime) метрик. Блок опционален: если его нет,
stream-health просто не оценивается.

```yaml
stream_drift:
  drift_event_time_lag_seconds:
    warning: 30
    critical: 120
  drift_late_event_rate:
    warning: 0.01
    critical: 0.05
```

Два важных нюанса:

- `drift_late_event_rate` — это **window-local rate**, а не lifetime-счётчик.
  Lifetime counter `late_events_total` намеренно **не** используется как
  текущий health signal: он только растёт и не отражает состояние «сейчас».
- Поля, которых нет в блоке, **не участвуют** в расчёте `stream_status`.
  То есть блок работает как whitelist: добавили порог — метрика начала влиять
  на статус.

---

### `adversarial_validation`

Обучает LightGBM отличать reference от current. Высокий ROC-AUC = дрейф;
feature importance показывает, какие именно фичи «сдвинулись». Ловит
многомерный дрейф, который покомпонентные метрики вроде PSI пропускают.

| Поле | Обязательно | Описание |
|---|:---:|---|
| `enabled` | да | Включает AV |
| `interval_minutes` | да при `enabled: true` | Периодичность запуска |
| `max_samples` | нет | Максимум строк на обучение |
| `n_splits` | нет | Число фолдов CV (минимум 2) |
| `random_state` | нет | Seed сплитов |
| `missing_category` | нет | Заполнитель пропусков в категориальных фичах |
| `lightgbm` | нет | Гиперпараметры модели |

```yaml
adversarial_validation:
  enabled: true
  interval_minutes: 30
  max_samples: 50000
  n_splits: 5
  random_state: 42
  missing_category: "__missing__"
  lightgbm:
    n_estimators: 200
    learning_rate: 0.03
    max_depth: 4
    num_leaves: 15
    subsample: 0.8
    colsample_bytree: 0.8
    reg_alpha: 1.0
    reg_lambda: 1.0
    random_state: 42
```

Гиперпараметры по умолчанию рассчитаны на **регуляризованную** модель: цель —
не выжать максимум качества, а получить честный сигнал о дрейфе и осмысленные
feature importances. Отсюда небольшая `max_depth` и `num_leaves`.

---

## Доступные метрики

**Для `numeric`:**

```
missing_rate, psi, js_divergence, wasserstein_distance, kstest
```

**Для `categorical`:**

```
missing_rate, psi, js_divergence, unseen_category_rate,
cardinality_ratio, chi2, cramer_v, category_churn
```

**Только numeric:** `wasserstein_distance`, `kstest`
**Только categorical:** `unseen_category_rate`, `cardinality_ratio`, `chi2`,
`cramer_v`, `category_churn`

Общие для обоих типов: `missing_rate`, `psi`, `js_divergence`.

---

## Reversed-метрики

Для большинства метрик «больше = хуже», поэтому `warning < critical`.

Исключение — **reversed**-метрики, где «меньше = хуже». Например `chi2` —
это p-value:

```yaml
thresholds:
  chi2:
    warning: 0.05
    critical: 0.01     # critical < warning — это корректно
```

Билдер проверяет порядок и **падает с ошибкой** при неверном.

> `kstest` возвращает D-statistic, а **не** p-value, поэтому он не reversed:
> для него `warning < critical`.

---

## Приоритет порогов

Правила разрешения порога для пары (фича, метрика):

```
локальный порог фичи   >   глобальный порог метрики
```

При генерации через билдер добавляется ещё один уровень:

```
вручную заданные пороги   >   авто-пороги
```

Автокалибровка заполняет **только то, что вы не задали вручную**.

Итоговый порядок:

1. `feature_thresholds` (вручную, для конкретной фичи)
2. `global_thresholds` (вручную, глобально)
3. авто-порог по квантилям + floor
4. ошибка `Missing thresholds after auto-generation`, если ничего не нашлось

---

## Генерация конфига: `build_config.py`

Скрипт лежит в `tools/` и настраивается редактированием переменных в самом
файле — CLI-аргументов у него нет.

Порядок работы:

1. Указать путь к reference-данным в блоке **ВХОД / ВЫХОД**.
2. При желании раскомментировать и поправить нужные настройки в `OPTIONS`.
   Всё, что не тронуто, останется дефолтом билдера.
3. Запустить:

```bash
uv run python tools/build_config.py
```

Reference-данные — это «эталонный» срез, относительно которого в дальнейшем
измеряется дрейф. Обычно это train-выборка или недавний период стабильной
работы модели.

### Вход и выход

```python
DATA_PATH = "data/reference.csv"  # .csv или .parquet
OUTPUT_PATH = "config/config.yaml"
NROWS = None  # None = читать всё
```

`NROWS` удобен для быстрой проверки на большом файле. Для `.parquet` файл
читается целиком, а затем берётся `head(nrows)`.

Неподдерживаемое расширение → `ValueError`. Отсутствующий файл →
`FileNotFoundError` с подсказкой проверить `DATA_PATH`.

---

### 1. Какие колонки мониторить

```python
include_columns = None  # None = все колонки датафрейма
exclude_columns = []  # технические поля: id, timestamp, флаги
disabled_features = []  # ещё один способ выключить фичи
feature_types = {}  # override автоопределения типа
```

- `include_columns` — белый список. Если задан, мониторятся **только** эти
  колонки.
- `exclude_columns` и `disabled_features` работают одинаково: обе коллекции
  **объединяются** при отборе. Разделение чисто организационное — «постоянные
  исключения» против «временно выключенных».
- `feature_types` — переопределение типа. По умолчанию: числовой dtype
  (кроме `bool`) → `numeric`, всё остальное → `categorical`. Типичный случай —
  числовые коды категорий:

```python
feature_types = {"region_code": "categorical", "rating": "numeric"}
```

---

### 2. Какие метрики считать

```python
numeric_metrics = None  # None = дефолт билдера
categorical_metrics = None  # None = дефолт билдера
feature_metrics = {}  # полная замена набора у фичи
disabled_metrics = []  # выключить метрику везде
feature_disabled_metrics = {}  # выключить метрику у одной фичи
```

Дефолт билдера для numeric:

```python
["missing_rate", "psi", "js_divergence", "wasserstein_distance", "kstest"]
```

Дефолт билдера для categorical:

```python
[
    "missing_rate",
    "psi",
    "js_divergence",
    "unseen_category_rate",
    "cardinality_ratio",
    "chi2",
    "cramer_v",
    "category_churn",
]
```

Примеры:

```python
feature_metrics = {"age": ["missing_rate", "psi", "kstest"]}
disabled_metrics = ["wasserstein_distance"]  # мешает разный масштаб фичей
feature_disabled_metrics = {"country": ["chi2", "cramer_v"]}
```

---

### 3. Пороги вручную

Формат: `{"warning": x, "critical": y}` либо кортеж `(x, y)`.

```python
global_thresholds = {
    "psi": {"warning": 0.1, "critical": 0.25},
    "kstest": (0.02, 0.05),
    "chi2": {"warning": 0.05, "critical": 0.01},  # reversed!
}

feature_thresholds = {
    "age": {"psi": {"warning": 0.05, "critical": 0.12}},
    "amount": {"psi": (0.15, 0.3)},
}
```

Заданные здесь пороги **имеют приоритет** над авто-порогами.

---

### 4. Автокалибровка порогов

Как работает: из reference-данных нарезаются калибровочные окна, на каждом
считаются все метрики. Получается распределение значений метрики «при
отсутствии дрейфа». Пороги берутся как его квантили.

```python
auto_thresholds = AutoThresholdSettings(
    enabled=True,
    method="bootstrap",
    window_size=None,
    n_windows=100,
    warning_quantile=0.95,
    critical_quantile=0.99,
    per_feature=False,
    per_prediction=False,
    # floors={...},
    eps=1e-12,
    random_state=42,
    ignore_metric_errors=True,
)
```

| Параметр | Значение |
|---|---|
| `enabled` | `False` → все пороги нужно задать вручную в `global_thresholds`, иначе ошибка `Missing thresholds after auto-generation` |
| `method` | `"bootstrap"` — случайная выборка с возвращением, окна независимы; подходит, если порядок строк не важен. `"rolling"` — последовательные окна вдоль датафрейма; подходит для временных данных, учитывает автокорреляцию и сезонность |
| `window_size` | `None` → берётся `profiler_window_size`. Должен примерно соответствовать размеру продакшн-батча: многие метрики зависят от размера выборки |
| `n_windows` | Больше окон = стабильнее квантили, но дольше генерация. 100 — разумный компромисс. Для оценки 0.99-квантиля меньше 100 брать не стоит |
| `warning_quantile` | `0.95` → warning сработает примерно на 5% нормальных батчей |
| `critical_quantile` | `0.99` → critical сработает примерно на 1% нормальных батчей |
| `per_feature` | `True` = отдельные пороги для каждой пары (фича, метрика): точнее, но конфиг разрастается в разы. `False` = только глобальные пороги на метрику |
| `per_prediction` | То же самое для блока `prediction_metrics` |
| `floors` | Минимальные значения порогов (см. ниже) |
| `eps` | Минимальный зазор между warning и critical, если квантили совпали |
| `random_state` | Seed для bootstrap. Фиксируйте для воспроизводимости |
| `ignore_metric_errors` | `True` = предупреждение в stderr, окно пропускается. `False` = сразу `RuntimeError` |

Шумные алерты? Поднимите квантили до `0.98` / `0.995`.

#### Floors — минимальные значения порогов

**Зачем.** Если reference-данные очень однородны, bootstrap может дать почти
нулевое распределение метрики. Тогда авто-порог получится вида
`warning=0.0` / `critical=1e-12`, и алерт будет срабатывать на любом шуме.
Floors это предотвращают.

Логика применения:

```
обычные метрики   ->  порог = max(квантиль, floor)
reversed-метрики  ->  порог = min(квантиль, floor)
```

Дефолтные floors билдера:

| Метрика | warning | critical |
|---|---:|---:|
| `missing_rate` | 0.005 | 0.02 |
| `unseen_category_rate` | 0.005 | 0.02 |
| `category_churn` | 0.005 | 0.02 |
| `psi` | 0.1 | 0.25 |
| `js_divergence` | 0.005 | 0.02 |
| `kstest` | 0.02 | 0.05 |
| `chi2` *(reversed)* | 0.05 | 0.01 |
| `cramer_v` | 0.02 | 0.05 |
| `cardinality_ratio` | 0.02 | 0.05 |

> **Важно.** Если передать свой `dict`, он **заменит** дефолтный целиком,
> а не дополнит его. Хотите поправить одну метрику — скопируйте всю таблицу
> и измените нужную строку.

Для `wasserstein_distance` floor намеренно не задан: метрика зависит от
масштаба фичи, универсального значения не существует.

---

### 5. Мониторинг предсказаний

```python
prediction_enabled = False
prediction_score_column = None
prediction_type = None
prediction_metrics = None
prediction_thresholds = {}
```

| Параметр | Значение |
|---|---|
| `prediction_enabled` | Включает блок `prediction_metrics` |
| `prediction_score_column` | **Обязателен** при `prediction_enabled=True` |
| `prediction_type` | `None` = определить по dtype. Варианты: `"numeric"`, `"categorical"` |
| `prediction_metrics` | `None` = дефолт по типу: numeric → `["psi", "kstest"]`, categorical → `["psi"]` |
| `prediction_thresholds` | Например `{"psi": {"warning": 0.1, "critical": 0.2}}` |

---

### 6. Stream drift

```python
stream_drift = {
    "drift_event_time_lag_seconds": {"warning": 30, "critical": 120},
    "drift_late_event_rate": {"warning": 0.01, "critical": 0.05},
}
```

Пороги пишутся в конфиг **как есть** — билдер их не калибрует и не проверяет
по reference-данным. `None` → блок не добавляется в YAML.
Можно указать следующие метрики: 
- drift_stream_status 
- drift_event_time_lag_seconds 
- drift_window_time_span_seconds 
- drift_max_event_gap_seconds 
- drift_invalid_event_time_rate 
- drift_late_event_rate 
- drift_late_events_total 
- drift_out_of_order_events_total

---

### 7. Adversarial validation

```python
adversarial_validation = AdversarialValidationBuildOptions(
    enabled=False,
    interval_minutes=None,
    max_samples=100_000,
    n_splits=3,
    random_state=42,
    missing_category="__missing__",
    emit_when_disabled=False,
    lightgbm=LightGBMBuildOptions(...),
)
```

| Параметр | Значение |
|---|---|
| `enabled` | Включает AV |
| `interval_minutes` | **Обязателен** при `enabled=True`, иначе `ValueError`. Например `60` (раз в час), `1440` (раз в сутки) |
| `max_samples` | Положительное число. Ограничивайте на больших данных: обучение идёт по расписанию |
| `n_splits` | Минимум 2, дефолт 3 |
| `random_state` | Seed сплитов |
| `missing_category` | Заполнитель пропусков в категориальных фичах перед обучением |
| `emit_when_disabled` | `True` → в конфиг попадёт `adversarial_validation: {enabled: false}`. `False` → блок не пишется вообще, применится default из схемы `Config` |

Гиперпараметры LightGBM (`LightGBMBuildOptions`):

| Параметр | Дефолт | Комментарий |
|---|---:|---|
| `n_estimators` | 1000 | |
| `learning_rate` | 0.05 | |
| `max_depth` | 4 | Намеренно небольшая глубина |
| `num_leaves` | 15 | |
| `importance_type` | `"gain"` | `"gain"` \| `"split"` |
| `min_child_samples` | 20 | |
| `subsample` | 1.0 | |
| `subsample_freq` | 0 | `0` = subsample отключён |
| `colsample_bytree` | 1.0 | |
| `reg_alpha` | 0.0 | |
| `reg_lambda` | 0.0 | |
| `n_jobs` | -1 | `-1` = все ядра |
| `random_state` | `None` | |
| `class_weight` | `None` | `"balanced"` при сильном дисбалансе |
| `objective` | `None` | |
| `boosting_type` | `"gbdt"` | |
| `verbosity` | -1 | `-1` = тихий режим |

> Если задаёте `subsample < 1.0`, не забудьте выставить `subsample_freq > 0` —
> иначе LightGBM проигнорирует сабсэмплинг.

---

### 8. Profiler

Профайлер строит «эталонный слепок» reference-данных: гистограммы числовых
фичей, частоты категорий. Относительно него потом считаются метрики дрейфа.

```python
profiler_window_size = 1000
merge_threshold = 5
low_cardinality_threshold = 15
profiler_take_sample = True
sample_float_dtype = "float32"
random_state = 42
```

| Параметр | Дефолт | Значение |
|---|---:|---|
| `profiler_window_size` | `1000` | Ожидаемый размер окна данных в продакшене. Влияет на биннинг гистограмм и на размер калибровочных окон, если `auto_thresholds.window_size` не задан явно  |
| `merge_threshold` | `5` | Категории с частотой ниже этого значения объединяются в «прочее». Защищает от шума на редких категориях  |
| `low_cardinality_threshold` | `15` | Если уникальных значений не больше этого числа, фича считается низкокардинальной и обрабатывается без биннинга  |
| `profiler_take_sample` | `True` | Хранить ли сэмпл reference-данных в профиле. Нужен метрикам, которым требуются сырые значения (например `kstest`)  |
| `sample_float_dtype` | `"float32"` | dtype сохранённого сэмпла. `float32` экономит память, `float64` — если важна точность на больших значениях  |
| `random_state` | `42` | Seed для сэмплирования в профайлере  |

> **Связка с автокалибровкой.** `profiler_window_size` — это дефолт для
> `auto_thresholds.window_size` . Если в продакшене окно 5000 строк,
> меняйте `profiler_window_size`, а не только пороги: иначе калибровочные окна
> будут меньше реальных батчей, и метрики окажутся смещёнными.

> **Если отключить `profiler_take_sample`**, метрики, требующие сырых значений,
> работать не смогут . Практически это означает: `kstest` из набора
> придётся убрать.

---

### 9. Прочее и strict-флаги

```python
metric_kwargs = {}
strict_metric_compatibility = True
strict_metric_registry = True
drop_time_columns = True
drop_all_missing_columns = True
drop_features_without_metrics = True
```

| Параметр | Дефолт | `True` | `False` |
|---|:---:|---|---|
| `strict_metric_compatibility` | `True` | Ошибка, если метрика несовместима с типом фичи (например `kstest` на категориальной) | Такие метрики молча выбрасываются с предупреждением |
| `strict_metric_registry` | `True` | Ошибка, если метрики нет в `METRIC_REGISTRY` (опечатка в названии) | Метрика выбрасывается с предупреждением |
| `drop_time_columns` | `True` | `datetime` / `timedelta` / `period` колонки молча пропускаются (профайлер их не поддерживает) | Падение с ошибкой |
| `drop_all_missing_columns` | `True` | Колонки, где все значения пустые, пропускаются | Падение с ошибкой |
| `drop_features_without_metrics` | `True` | Фича без метрик убирается из конфига | Падение с ошибкой |

`metric_kwargs` — дополнительные аргументы для функций метрик :

```python
metric_kwargs = {"psi": {"eps": 1e-6}}
```

> **Рекомендация.** Держите оба `strict_*` флага в `True`, пока настраиваете
> конфиг, — так опечатки видны сразу . Переводить их в `False` осмысленно
> только в автоматизированных пайплайнах, где падение генерации нежелательно.

---

## Вывод скрипта

Перед генерацией печатается сводка по данным и режим калибровки:

```
Данные : data/reference.csv
Строк  : 50,000
Колонок: 12

Автокалибровка порогов: bootstrap, 100 окон. Это может занять время.
Генерация конфига...
```

Если автокалибровка выключена, вместо этой строки будет
`Автокалибровка порогов отключена.` 

После генерации:

```
Готово: /abs/path/config/config.yaml

  Фичей в конфиге       : 9
  Пар (фича, метрика)   : 41
  Глобальных порогов    : 8
  prediction_metrics    : включён

  Не попали в конфиг (исключены / время / полностью пустые /
  колонка предсказания):
    - user_id  (dtype=int64)
    - created_at  (dtype=datetime64[ns])
```

Последний блок — **частый источник сюрпризов** . Всегда проверяйте его:
если колонка, которую вы ожидали мониторить, оказалась в списке, значит она
попала под `exclude_columns` / `disabled_features`, оказалась временной,
полностью пустой или была назначена колонкой предсказания.

`Пар (фича, метрика)` считается как сумма длин `metrics` по всем фичам  —
это прямая оценка того, сколько правил будет проверяться на каждом окне.

---

## Типовые ошибки

| Сообщение / симптом | Причина | Что делать |
|---|---|---|
| `FileNotFoundError` с подсказкой про `DATA_PATH` | Неверный путь к reference-данным | Проверить `DATA_PATH` в начале скрипта  |
| `ValueError: Неподдерживаемый формат '<suffix>'` | Формат не `.csv` / `.parquet` / `.pq` | Сконвертировать данные или заменить функцию `load_data`  |
| `Missing thresholds after auto-generation` | `auto_thresholds.enabled=False`, а пороги вручную не заданы | Включить автокалибровку либо заполнить `global_thresholds`  |
| `ValueError` про `interval_minutes` | `adversarial_validation.enabled=True` без `interval_minutes` | Задать `interval_minutes`  |
| Ошибка про порядок warning/critical | Неверный порядок для обычной или reversed-метрики | Для обычных `warning < critical`, для `chi2` — наоборот  |
| Ошибка совместимости метрики | `strict_metric_compatibility=True` и метрика не подходит типу фичи | Убрать метрику или сменить `type` фичи  |
| Ошибка про `METRIC_REGISTRY` | Опечатка в названии метрики при `strict_metric_registry=True` | Сверить название со списком доступных метрик  |
| Предупреждения в stderr при калибровке | Метрика упала на отдельном окне, `ignore_metric_errors=True` | Окно пропущено. Если предупреждений много — проверить качество reference-данных  |
| `RuntimeError` во время калибровки | Метрика упала при `ignore_metric_errors=False` | Разобраться с причиной или временно выставить `True`  |
| Фича пропала из конфига без ошибки | Не осталось метрик, `drop_features_without_metrics=True` | Проверить `disabled_metrics` / `feature_disabled_metrics`  |
| Правило по фиче молча не срабатывает | Фичи нет в reference-профиле | Пересобрать профиль или убрать фичу из конфига |

---

## Рецепты

### Минимальный конфиг вручную

Мало фичей, пороги известны:

```yaml
features:
  age:
    type: numeric
    metrics: [missing_rate, psi]
  country:
    type: categorical
    metrics: [missing_rate, psi, unseen_category_rate]

thresholds:
  missing_rate:
    warning: 0.02
    critical: 0.05
  psi:
    warning: 0.1
    critical: 0.25
  unseen_category_rate:
    warning: 0.05
    critical: 0.1
```

### Быстрая проверка на большом датасете

```python
NROWS = 20_000

OPTIONS = ConfigBuildOptions(
    auto_thresholds=AutoThresholdSettings(
        enabled=True,
        n_windows=20,  # вместо 100 — быстрее, но квантили грубее
    ),
)
```

### Временные данные

Для данных с сезонностью и автокорреляцией `rolling` честнее `bootstrap` :

```python
auto_thresholds = AutoThresholdSettings(
    enabled=True,
    method="rolling",
    window_size=1000,
    n_windows=100,
)
```

### Только табличные фичи, без тяжёлых метрик

Фичи в разном масштабе — `wasserstein_distance` мешает :

```python
OPTIONS = ConfigBuildOptions(
    exclude_columns=["user_id", "request_id", "created_at"],
    disabled_metrics=["wasserstein_distance"],
)
```

### Мониторинг предсказаний + AV

```python
OPTIONS = ConfigBuildOptions(
    prediction_enabled=True,
    prediction_score_column="prediction_score",
    prediction_type="numeric",
    prediction_metrics=["psi"],
    prediction_thresholds={"psi": {"warning": 0.1, "critical": 0.2}},
    adversarial_validation=AdversarialValidationBuildOptions(
        enabled=True,
        interval_minutes=30,
        max_samples=50_000,
        n_splits=5,
        lightgbm=LightGBMBuildOptions(
            n_estimators=200,
            learning_rate=0.03,
            subsample=0.8,
            subsample_freq=1,  # иначе subsample не применится
            colsample_bytree=0.8,
            reg_alpha=1.0,
            reg_lambda=1.0,
            random_state=42,
        ),
    ),
)
```

### Снижение шума алертов

```python
auto_thresholds = AutoThresholdSettings(
    enabled=True,
    warning_quantile=0.98,
    critical_quantile=0.995,
)
```

### Точные пороги под каждую фичу

Конфиг разрастётся в разы, но пороги учтут специфику каждой фичи :

```python
auto_thresholds = AutoThresholdSettings(
    enabled=True,
    per_feature=True,
    per_prediction=True,
)
```

---

## См. также

- [`docs/reference-data.md`](reference-data.md) — подготовка reference-датасета
- [`docs/metrics.md`](metrics.md) — как считается каждая метрика
- [`docs/adversarial-validation.md`](adversarial-validation.md) — детали AV
- [`docs/realtime.md`](realtime.md) — stream-метрики и `stream_status`
- [`docs/alerting.md`](alerting.md) — статусы и правила алертинга