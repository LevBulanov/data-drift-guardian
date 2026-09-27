# Adversarial Validation

Документ описывает, как работает adversarial validation (AV) в Data Drift Guardian: зачем она нужна, когда запускается, какие метрики публикует и как их интерпретировать.

Конфигурация — в [docs/configuration.md](configuration.md), полный Prometheus contract — в [docs/prometheus-contract.md](prometheus-contract.md).

---

## Зачем это нужно

Univariate drift-метрики (PSI, KS, JS divergence) смотрят на каждую feature по отдельности. Они не заметят, что изменилась **совместная** структура данных: корреляции, взаимодействия признаков, форма многомерного облака точек. Каждая feature по отдельности может выглядеть «как раньше», а датасет в целом — уже нет.

Adversarial validation решает это через простой трюк: обучить бинарный классификатор, который отличает reference-выборку от current-выборки. Reference получает метку `0`, current — метку `1`, выборки объединяются, модель учится предсказывать источник строки.

Логика интерпретации:

- **классификатор не справляется** (ROC AUC ≈ 0.5) — распределения неразличимы, drift'а на уровне совместной структуры нет;
- **классификатор справляется хорошо** (ROC AUC высокий) — между выборками есть систематическая разница, и модель её нашла.

Побочный полезный эффект: feature importance обученного классификатора показывает, **какие именно** признаки позволяют отличить current от reference. Это готовый ranking drift-drivers.

> AV — это не замена univariate метрикам, а дополнение. PSI скажет «income сдвинулся», AV скажет «изменилась связка income × country, хотя каждая по отдельности в норме».

---

## Реализация в проекте

Классификатор — **LightGBM**, оценка качества — stratified cross-validation. AV работает и в realtime (по расписанию), и в offline (явным вызовом).

### Конфигурация

```yaml
adversarial_validation:
  enabled: true
  interval_minutes: 30
  max_samples: 50000
  n_splits: 5
  random_state: 42
  missing_category: "__missing__"
```

| Параметр | Назначение                                                                               |
|---|------------------------------------------------------------------------------------------|
| `enabled` | включает AV; по умолчанию в генераторе конфига `false`                                   |
| `interval_minutes` | минимальный интервал **между повторными** AV runs; первый AV планируется на первом полном окне |
| `max_samples` | верхняя граница числа строк на выборку (защита от долгого обучения на больших reference) |
| `n_splits` | число CV folds                                                                           |
| `random_state` | seed для воспроизводимости                                                               |
| `missing_category` | placeholder для пропусков в categorical features                                         |

Thresholds для ROC AUC задаются **не в конфиге, а через environment** analyzer:

```env
AV_WARNING_THRESHOLD=0.60
AV_CRITICAL_THRESHOLD=0.75
```

Число публикуемых drivers:

```env
ADVERSARIAL_TOP_FEATURES=10
```

---

## Расписание в realtime

AV — дорогая операция (обучение модели с CV), поэтому она не выполняется на каждом окне.

**Правила:**

1. Первый AV запускается на **первом полном analysis window**, если AV включён и обе выборки содержат достаточно строк для `n_splits`.
2. Повторные AV — не чаще чем раз в `interval_minutes`.
3. Фактический повторный запуск происходит на первом полном окне **после** истечения интервала — не по таймеру, а привязанно к окну.
4. Обычный drift analysis выполняется на **каждом** полном окне независимо от AV schedule.

При `WINDOW_SIZE=1000` и `interval_minutes=30`:

```text
window #1       → drift + AV #1
window #2..N    → drift only, последний AV snapshot сохраняется в метриках
>= 30 минут     → ближайшее полное окно → drift + AV #2
```

Между AV runs метрики `drift_av_*` не обнуляются и не помечаются как stale — они продолжают отдавать последний snapshot. Актуальность определяется по `drift_av_last_run_timestamp_seconds`.

### Практическое следствие для demo

При локальном demo-producer'е (`PRODUCER_INTERVAL_SECONDS=0.2`, `WINDOW_SIZE=1000`) одно окно набирается примерно за 200 секунд. С `interval_minutes=30` второй AV произойдёт не раньше чем через полчаса — то есть примерно на девятом-десятом окне.

Если нужно увидеть несколько AV runs подряд за короткую demo-сессию, уменьшите `interval_minutes` в конфиге. При `interval_minutes` меньше времени набора окна AV будет выполняться на каждом окне.

---

## `drift_av_available` — результат, а не переключатель

Частая путаница. Эта метрика **не отражает** значение `adversarial_validation.enabled`:

```text
0 = успешного AV snapshot ещё нет
1 = AV snapshot опубликован
```

`drift_av_available = 0` означает одно из:

- AV выключен в конфиге;
- AV включён, но первое полное окно ещё не набралось;
- AV пытался запуститься, но упал или не прошёл проверку на минимальный размер выборки.

Различить эти случаи можно только по логам analyzer — отдельной метрики «AV включён» нет.

До первого успешного AV diagnostic gauges могут содержать `-1`, включая:

```text
drift_av_last_run_timestamp_seconds
```

Значение `-1` здесь — это sentinel «данных нет», а не Unix timestamp.

---

## Метрики

```text
drift_av_status
drift_av_available
drift_av_roc_auc
drift_av_roc_auc_cv_mean
drift_av_roc_auc_cv_min
drift_av_roc_auc_cv_max
drift_av_roc_auc_cv_std
drift_av_driver_consistency
drift_av_driver_similarity_previous
drift_av_top1_importance_share
drift_av_top3_importance_share
drift_av_feature_importance{feature,rank}
drift_av_timestamp_seconds
drift_av_last_run_timestamp_seconds
drift_av_dataset_size
drift_av_reference_rows
drift_av_current_rows
drift_av_features_evaluated
drift_av_sample_fraction{dataset}
drift_av_threshold{level}
```

### ROC AUC и статус

`drift_av_roc_auc` — основной сигнал. Интерпретация по умолчанию:

| Значение | Статус | Смысл |
|---|---|---|
| ~0.5 | ok | выборки неразличимы, drift'а нет |
| 0.5–0.60 | ok | слабая различимость |
| 0.60–0.75 | warning | модель уверенно отличает выборки |
| > 0.75 | critical | выборки сильно разошлись |

`drift_av_status` кодируется стандартно:

```text
-1 = insufficient_data / not configured
 0 = ok
 1 = warning
 2 = critical
```

> ROC AUC заметно ниже 0.5 — аномалия, а не «очень хорошо». Обычно это признак проблемы с разметкой или утечки в CV-схеме, и такой результат стоит проверять в логах, а не трактовать как отсутствие drift'а.

### CV diagnostics

Четыре метрики вокруг одного AV run:

```text
drift_av_roc_auc_cv_mean
drift_av_roc_auc_cv_min
drift_av_roc_auc_cv_max
drift_av_roc_auc_cv_std
```

Смысл их публикации — **отделить устойчивый drift от случайной удачи модели на одном split'е**.

- `cv_std` мал, `cv_min` близок к `cv_mean` → результат устойчив, доверять можно;
- `cv_std` велик, разброс `min`/`max` широк → модель нашла разницу только на части folds. Это может быть локальный артефакт подвыборки, а не системный drift.

Практический ориентир: если `cv_max − cv_min` сравнимо с отрывом `cv_mean` от 0.5, вывод о drift'е ненадёжен.

### Driver consistency

```text
drift_av_driver_consistency
```

Mean pairwise cosine similarity feature-importance векторов **между CV folds одного AV run**.

Отвечает на вопрос: «все folds показывают одних и тех же виновников, или каждый свой?»

- близко к `1` → folds согласны, ranking drift-drivers стабилен и им можно пользоваться;
- низкое значение → importance разъезжается между folds. Высокий ROC AUC при низком driver consistency означает «разница есть, но модель каждый раз ловит её через разные признаки» — доверять топ-10 в таком случае не стоит.

Эта метрика особенно полезна перед тем, как принимать решение по конкретной feature из ranking'а.

### Similarity к предыдущему run

```text
drift_av_driver_similarity_previous
```

Cosine similarity feature importance текущего AV к **предыдущему завершённому AV** — не к предыдущему окну.

Две важные особенности:

- на первом AV предыдущего snapshot не существует, поэтому в Grafana отображается `–`;
- метрика **не обновляется** на обычных drift windows между AV runs. Плоская линия между запусками — ожидаемое поведение, а не залипшее значение.

Содержательной метрика становится со второго AV run. Интерпретация:

- высокая similarity → drift развивается по тем же признакам, причина стабильна;
- резкое падение → набор виновников сменился, произошло качественно новое событие в данных, а не продолжение старого тренда.

### Размеры выборок и покрытие

```text
drift_av_dataset_size
drift_av_reference_rows
drift_av_current_rows
drift_av_features_evaluated
drift_av_sample_fraction{dataset}
```

Полезны для sanity check. Если `drift_av_features_evaluated` меньше, чем вы ожидаете по конфигу, часть признаков была отброшена — проверьте логи. `drift_av_reference_rows` показывает размер reference sample, доступного realtime Core, а `drift_av_current_rows` — размер current window.

Exporter выставляет `drift_av_dataset_size = min(reference_rows, current_rows)` и из него вычисляет `drift_av_sample_fraction` для `reference` и `current`. Сам классификатор внутри AV дополнительно применяет `max_samples`, то есть фактический training sample равен `min(len(reference), len(current), max_samples)`. В обычном realtime-сценарии с `WINDOW_SIZE` существенно меньше `max_samples` эти размеры совпадают.

Сильный дисбаланс между доступными `reference_rows` и `current_rows` устраняется перед обучением: AV семплирует одинаковое число строк из обеих выборок.

### Feature importance

```text
drift_av_feature_importance{feature,rank}
```

Публикуется top-`ADVERSARIAL_TOP_FEATURES` (по умолчанию 10) признаков. Label `rank` — позиция в ranking'е, `feature` — имя признака.

В Grafana dashboard это таблица top drivers. Читать её нужно **вместе с** `drift_av_driver_consistency`: при низкой consistency ranking нестабилен.

---

## Prediction column исключается из AV

Если в конфиге настроен prediction monitoring, realtime AV **исключает** prediction column из feature set.

Причина: prediction drift и multivariate input drift — разные явления с разными причинами. Если оставить `prediction_score` среди признаков AV, модель почти наверняка вытащит его в топ (предсказания обычно меняются вместе с входом), и ranking перестанет быть информативным про input features.

В offline API это делается явным аргументом:

```python
av_report = analyzer.run_av(
    current_df,
    prediction_col="prediction_score",
)
```

Подробнее про prediction drift — [docs/metrics.md](metrics.md).

---

## Alerting

AV участвует в alerting через streak-метрику:

```text
drift_av_status_streak
```

Ключевая особенность: streak увеличивается **только при новом опубликованном AV result**. Обычные drift windows между AV runs не считаются новыми AV observations.

Это означает, что AV streak накапливается в темпе AV runs, а не в темпе обычных окон. Первый успешный AV сразу даёт `streak=1`; при `interval_minutes=30` значение `streak=4` возможно не раньше чем примерно через **90 минут после первого AV**, плюс выравнивание по ближайшим полным analysis windows.

Детали alert rules — [docs/alerting.md](alerting.md).

---

## Offline использование

```python
import pandas as pd
from drift_guardian.analyzer.offline.offline_mode import OfflineWrapper

analyzer = OfflineWrapper(reference_df=pd.read_csv("data/reference.csv"))
current_df = pd.read_csv("data/current.csv")

av_report = analyzer.run_av(current_df, prediction_col="prediction_score")
```

Offline вызов не подчиняется `interval_minutes` — AV выполняется немедленно при каждом вызове `run_av()`.

Результат можно передать в HTML report:

```python
from drift_guardian.reporting import generate_html_report

generate_html_report(
    report=report,
    av_report=av_report,
    dataset_name="Offline drift demo",
    output_path="reports/offline_drift_report.html",
)
```

Секция `Adversarial Validation` в отчёте содержит ROC AUC и top-10 feature importance. Без переданного `av_report` секция не рендерится — в том числе при использовании CLI `drift-guardian-report`, у которого нет аргумента для AV.

Подробнее — [docs/offline.md](offline.md).

---

## Диагностика

**`drift_av_available = 0` дольше, чем время набора одного окна.** Проверьте по порядку:

```text
adversarial_validation.enabled  — включён ли AV вообще
WINDOW_SIZE >= n_splits         — хватает ли строк в окне на CV
reference rows >= n_splits      — хватает ли строк в reference
analyzer logs                   — не падает ли AV с исключением
```

При корректной конфигурации первый AV планируется уже на первом полном окне.

**`drift_av_last_run_timestamp_seconds = -1` при `drift_av_available = 1`.** Рассогласование: snapshot есть, а timestamp не выставлен. Это повод смотреть логи exporter'а и analyzer'а.

**`drift_av_driver_similarity_previous` не меняется.** Ожидаемо, если с последнего AV run не было нового. Сверьте с `drift_av_last_run_timestamp_seconds`.

**Высокий ROC AUC, но топ-драйверы выглядят бессмысленно.** Смотрите `drift_av_driver_consistency`. При низком значении ranking нестабилен между folds, и топ-10 не отражает реальных причин.

Остальные сценарии — [docs/troubleshooting.md](troubleshooting.md).

---

## Связанные документы

- [docs/metrics.md](metrics.md) — univariate drift-метрики, thresholds, prediction drift
- [docs/prometheus-contract.md](prometheus-contract.md) — полный список series и labels
- [docs/configuration.md](configuration.md) — генерация конфига, блок `adversarial_validation`
- [docs/realtime.md](realtime.md) — окна и schedule
- [docs/alerting.md](alerting.md) — Grafana alert rules и streak-логика