# Prometheus contract

Документ описывает полный набор metric series, публикуемых exporter'ом, их labels, семантику значений и scrape-конфигурацию.

Contract одинаков для realtime analyzer и mock exporter — это позволяет проверять дашборды и alert rules без Kafka и Core. См. [Mock-режим](../README.md#mock-режим-mock).

---

## Общие соглашения

**Префикс.** Все series начинаются с `drift_`.

**Status codes.** Любая метрика со словом `status` в имени кодируется одинаково:

```text
-1 = insufficient_data / not configured
 0 = ok
 1 = warning
 2 = critical
```

Значение `-1` — не ошибка. Оно означает «оценить невозможно»: данных ещё нет либо thresholds не заданы.

**Sentinel `-1` у timestamp'ов.** `drift_av_last_run_timestamp_seconds` до первого успешного AV содержит `-1`, а не Unix timestamp. При построении панелей это стоит фильтровать.

**Endpoint.** Метрики отдаются по `http://localhost:8000/metrics` в plaintext exposition format.

```bash
curl -s http://localhost:8000/metrics | grep drift_
```

<details>
<summary>PowerShell</summary>

```powershell
curl.exe -s http://localhost:8000/metrics | Select-String "drift_"
```

</details>

---

## Drift series

### Агрегированное состояние

| Series | Labels | Значение |
|---|---|---|
| `drift_overall_status` | — | худший статус по всем input features |
| `drift_active_alerts` | — | число input features в `critical` |

`drift_overall_status` работает по worst-case: одна critical-метрика на одной feature делает critical весь отчёт. Поэтому сам по себе он не говорит о масштабе — для этого есть `drift_active_alerts`.

**Prediction в `drift_active_alerts` не входит.** Счётчик считает только input features; prediction status публикуется отдельной series с `feature="prediction"`. См. [docs/metrics.md](metrics.md).

### Feature-level

| Series | Labels | Значение |
|---|---|---|
| `drift_status_feature` | `feature`, `type` | худший статус среди метрик feature |
| `drift_metric_value` | `feature`, `type`, `metric` | вычисленное значение метрики |
| `drift_status` | `feature`, `type`, `metric` | статус конкретной метрики |

Label `type` — `numeric` или `categorical`. Для prediction используется `feature="prediction"`.

Три уровня детализации соотносятся так:

```text
drift_status{feature,type,metric}   → статус одной метрики
drift_status_feature{feature,type}  → max по метрикам этой feature
drift_overall_status                → max по всем features
```

### Thresholds

| Series | Labels | Значение |
|---|---|---|
| `drift_threshold` | `metric`, `level` | global threshold |
| `drift_resolved_threshold` | `feature`, `type`, `metric`, `level` | фактически применённый threshold |

Label `level` — `warning` или `critical`.

Две series нужны потому, что feature-level override переопределяет global threshold. `drift_resolved_threshold` показывает значение **после** применения приоритета `feature local > global`, поэтому на графике конкретной feature линию порога надо строить именно по ней.

Для диагностики конфига полезно сравнить обе: расхождение показывает, какие override фактически подхватились.

> Для `chi2` направление обратное — `warning > critical`. Панель с линиями порогов для этой метрики читается инвертированно. См. [docs/metrics.md](metrics.md#chi2-и-обратное-направление).

---

## Window и runtime state

| Series | Тип | Значение |
|---|---|---|
| `drift_window_size` | gauge | настроенный `WINDOW_SIZE` |
| `drift_current_window_events` | gauge | событий в текущем незакрытом окне |
| `drift_events_processed_total` | counter | всего обработанных событий |
| `drift_analysis_runs_total` | counter | успешных анализов |
| `drift_last_analysis_age_seconds` | gauge | секунд с последнего успешного анализа |
| `drift_report_timestamp_seconds` | gauge | timestamp последнего отчёта |

**Прогресс окна:**

```promql
drift_current_window_events / drift_window_size
```

**Проверка живости.** `drift_last_analysis_age_seconds` — самый прямой сигнал «analyzer обрабатывает поток». Ожидаемый порядок величины:

```text
WINDOW_SIZE × средний интервал между событиями
```

Для локального demo (`WINDOW_SIZE=1000`, `PRODUCER_INTERVAL_SECONDS=0.2`) это около 200 секунд.

**Обнаружение потерянных окон.** Расхождение счётчиков указывает на bad windows:

```promql
# события идут, но анализы не завершаются
rate(drift_events_processed_total[10m]) > 0
  and rate(drift_analysis_runs_total[10m]) == 0
```

При bad window offset коммитится, а `drift_analysis_runs_total` не увеличивается — см. [docs/realtime.md](realtime.md#поведение-при-плохом-окне).

---

## Stream health

| Series | Тип | Значение |
|---|---|---|
| `drift_stream_status` | gauge | агрегированный статус потока |
| `drift_event_time_lag_seconds` | gauge | отставание event time от wall clock |
| `drift_window_time_span_seconds` | gauge | временной охват окна |
| `drift_max_event_gap_seconds` | gauge | максимальный разрыв между событиями в окне |
| `drift_invalid_event_time_rate` | gauge | доля событий с некорректным event time |
| `drift_late_event_rate` | gauge | доля late-событий в окне |
| `drift_late_events_total` | counter | late-события за весь срок жизни процесса |
| `drift_out_of_order_events_total` | counter | out-of-order события, lifetime |

Gauge-метрики — **window-local**: относятся к текущему или последнему закрытому окну.

Counter-метрики — **lifetime** и не являются текущим health signal. Использовать их напрямую в alert-условии не стоит: они монотонно растут. Берите производную:

```promql
rate(drift_late_events_total[5m])
```

Либо используйте window-local `drift_late_event_rate`.

### `drift_stream_status = -1`

Статус вычисляется **только** по тем stream-метрикам, для которых в блоке `stream_drift` конфига заданы thresholds. Если блок пуст, статус остаётся `-1` при полностью работающем потоке.

Checked-in конфиг покрывает только две метрики:

```yaml
stream_drift:
  drift_event_time_lag_seconds:
    warning: 30
    critical: 120
  drift_late_event_rate:
    warning: 0.01
    critical: 0.05
```

`window_time_span`, `max_event_gap` и `invalid_event_time_rate` публикуются, но в статус не входят.

> Отдельного Grafana alert rule для `drift_stream_status` в текущем provisioning нет — см. [docs/alerting.md](alerting.md).

---

## Adversarial validation

| Series | Labels | Значение |
|---|---|---|
| `drift_av_status` | — | статус AV по ROC AUC |
| `drift_av_available` | — | `0` — snapshot'а нет, `1` — опубликован |
| `drift_av_roc_auc` | — | основной AV-сигнал |
| `drift_av_roc_auc_cv_mean` | — | среднее по CV folds |
| `drift_av_roc_auc_cv_min` | — | минимум по folds |
| `drift_av_roc_auc_cv_max` | — | максимум по folds |
| `drift_av_roc_auc_cv_std` | — | стандартное отклонение по folds |
| `drift_av_driver_consistency` | — | mean pairwise cosine similarity importance между folds |
| `drift_av_driver_similarity_previous` | — | cosine similarity к предыдущему завершённому AV |
| `drift_av_feature_importance` | `feature`, `rank` | importance top-N drivers |
| `drift_av_last_run_timestamp_seconds` | — | timestamp последнего AV, `-1` если не было |
| `drift_av_reference_rows` | — | строк reference в AV |
| `drift_av_current_rows` | — | строк current в AV |
| `drift_av_features_evaluated` | — | признаков в AV feature set |

Thresholds для `drift_av_status` берутся из environment, а не из конфига:

```env
AV_WARNING_THRESHOLD=0.60
AV_CRITICAL_THRESHOLD=0.75
```

Число публикуемых drivers:

```env
ADVERSARIAL_TOP_FEATURES=10
```

### Особенности поведения

**`drift_av_available` — результат, не переключатель.** Значение `0` не означает «AV выключен»: это может быть выключенный AV, ненабранное первое окно или упавший AV run.

**AV-метрики обновляются реже остальных.** Между AV runs они отдают последний snapshot и не помечаются как stale. Плоская линия — ожидаемое поведение. Актуальность определяется по `drift_av_last_run_timestamp_seconds`.

**`drift_av_driver_similarity_previous` на первом AV пуста** — предыдущего snapshot'а не существует, в Grafana отображается `–`.

Интерпретация — [docs/adversarial-validation.md](adversarial-validation.md).

---

## Streak series для alerting

```text
drift_overall_status_streak
drift_status_feature_streak
drift_status_streak
drift_av_status_streak
```

Каждая streak-метрика — число последовательных наблюдений, в которых соответствующий статус был `warning` или `critical`. Labels повторяют базовую series: `drift_status_streak` несёт `feature`, `type`, `metric`, и так далее.

Назначение — не отправлять notification по единичному всплеску. Alert rule смотрит на длину серии, а не на мгновенное значение:

```promql
drift_overall_status_streak >= 4
```

Для input и prediction drift текущие правила требуют `warning`/`critical` на **4 последовательных analysis windows**.

### AV streak считается в другом темпе

`drift_av_status_streak` увеличивается **только при новом опубликованном AV result**. Обычные drift windows между AV runs новыми AV observations не являются.

Практическое следствие: при `interval_minutes=30` streak длиной 4 означает два часа наблюдений, а не четыре окна. Пороги для AV streak нельзя назначать по аналогии с drift streak.

Детали правил — [docs/alerting.md](alerting.md).

---

## Scrape configuration

```text
monitoring/prometheus/
```

Prometheus скрейпит два отдельных targets:

```text
analyzer:8000
mock exporter:8000
```

Оба relabel'ятся в общий публичный job:

```text
job="drift-exporter"
```

Источники различаются стандартным label `instance`. Custom `mode` label **не используется** — в запросах не стоит на него рассчитывать.

Такая схема даёт одно практическое свойство: дашборды и alert rules, написанные под `job="drift-exporter"`, работают одинаково в mock- и realtime-режиме без правок. Это и есть причина существования mock exporter'а.

> Оба сервиса публикуют host port `8000` и одновременно работать не могут. См. [Смена режима](../README.md#смена-режима).

---

## Примеры запросов

**Текущее состояние:**

```promql
drift_overall_status
drift_active_alerts
```

**Метрика конкретной feature с её порогами:**

```promql
drift_metric_value{feature="age", metric="psi"}
drift_resolved_threshold{feature="age", metric="psi", level="warning"}
drift_resolved_threshold{feature="age", metric="psi", level="critical"}
```

**Features в critical:**

```promql
drift_status_feature == 2
```

**Свежесть данных:**

```promql
drift_last_analysis_age_seconds > 400
```

Порог зависит от `WINDOW_SIZE` и темпа потока — 400 подобрано для локального demo с запасом к ожидаемым ~200 секундам.

**Заполнение окна в процентах:**

```promql
100 * drift_current_window_events / drift_window_size
```

**Top AV drivers:**

```promql
topk(5, drift_av_feature_importance)
```

**Устойчивость AV-результата:**

```promql
drift_av_roc_auc_cv_max - drift_av_roc_auc_cv_min
```

Широкий разброс при высоком `cv_mean` означает, что drift виден не на всех folds.

**Исключить «данных нет» из панели:**

```promql
drift_overall_status != -1
```

---

## Интеграция со своим стеком

Contract стабилен и не требует Grafana — метрики можно скрейпить любым Prometheus-совместимым сборщиком.

Минимальный набор для собственного дашборда:

```text
drift_overall_status               # светофор
drift_active_alerts                # масштаб проблемы
drift_status_feature               # какие features затронуты
drift_last_analysis_age_seconds    # живость сервиса
drift_stream_status                # здоровье потока
```

Для alerting добавьте streak-серии вместо мгновенных статусов.

При построении панелей учитывайте три sentinel-поведения:

1. `-1` в status-метриках — «нечего оценивать», не критичность;
2. `-1` в `drift_av_last_run_timestamp_seconds` — отсутствие AV, не дата 1969 года;
3. AV-метрики обновляются в темпе `interval_minutes`, остальные — в темпе окон.

---

## Связанные документы

- [docs/metrics.md](metrics.md) — семантика drift-метрик и thresholds
- [docs/realtime.md](realtime.md) — окна, offsets, stream health
- [docs/adversarial-validation.md](adversarial-validation.md) — интерпретация AV-метрик
- [docs/alerting.md](alerting.md) — Grafana alert rules и streak-логика
- [docs/troubleshooting.md](troubleshooting.md) — диагностика