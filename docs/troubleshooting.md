# Troubleshooting

Диагностика типичных проблем. Кейсы сгруппированы по симптому — начните с того раздела, который соответствует наблюдаемой картине.

Для большинства проверок понадобятся логи и метрики:

```bash
docker compose --profile realtime --profile local-kafka ps -a
docker compose --profile realtime --profile local-kafka logs --tail=200 analyzer
curl -s http://localhost:8000/metrics | grep drift_
```

<details>
<summary>PowerShell</summary>

```powershell
curl.exe -s http://localhost:8000/metrics | Select-String "drift_"
```

</details>

---

## Analyzer не стартует

### `reference dataset not found`

Файла нет по ожидаемому пути. Reference не входит в репозиторий — папка `data/` в `.gitignore`.

```bash
ls -l data/reference.csv
```

<details>
<summary>PowerShell</summary>

```powershell
Test-Path .\data\reference.csv
```

</details>

Решение — положить свой CSV или скачать по URL:

```bash
cp /path/to/your.csv data/reference.csv
# или
uv run python tools/get_demo_data.py
```

Если файл на месте, а analyzer его не видит — проверьте `REFERENCE_DATA_PATH` и volume-маппинг `data/` в Compose. Путь в переменной указывается **внутри контейнера** (`/app/data/reference.csv`), а не на хосте.

См. [docs/reference-data.md](reference-data.md).

### Таймаут ожидания Kafka

Истёк `KAFKA_STARTUP_TIMEOUT_SECONDS` (по умолчанию 60 секунд) в ожидании broker или topic.

Порядок проверки:

```text
1. broker доступен из контейнера analyzer, а не только с хоста
2. topic из KAFKA_TOPIC существует на кластере
3. KAFKA_BOOTSTRAP_SERVERS указывает на advertised listeners broker'а
4. на кластере нет обязательной аутентификации — SASL/SSL не поддерживается
```

Пункт 1 — самая частая причина при внешней Kafka: `localhost:9092` внутри контейнера указывает на сам контейнер. Используйте `host.docker.internal` или адрес хоста в bridge-сети.

Для медленно поднимающегося кластера увеличьте таймаут:

```env
KAFKA_STARTUP_TIMEOUT_SECONDS=180
```

См. [docs/external-kafka.md](external-kafka.md).

### Порт 8000 занят

`analyzer` и `drift-mock-exporter` публикуют один host port и одновременно работать не могут. Перед сменой режима удалите предыдущие сервисы:

```bash
docker compose --profile mock --profile realtime --profile local-kafka rm -sf \
  analyzer drift-mock-exporter drift-producer kafka
```

Prometheus и Grafana не принадлежат ни одному profile и могут оставаться поднятыми.

---

## Метрик нет или они не растут

### `drift_events_processed_total` стоит на нуле

Consumer подписался, но события не поступают либо отбрасываются валидацией. Разделить случаи помогает `drift_invalid_event_time_rate`:

```bash
curl -s http://localhost:8000/metrics | grep -E 'drift_events_processed_total|drift_invalid_event_time_rate'
```

- метрика не нулевая → проблема в формате event time у событий;
- метрика на нуле, счётчик стоит → в topic нет новых сообщений для этой consumer group.

При переиспользовании существующей `KAFKA_GROUP_ID` analyzer начинает с committed offsets, а не с начала topic — возможно, всё уже вычитано.

Для локального demo проверьте, жив ли producer:

```bash
docker compose --profile realtime --profile local-kafka logs --tail=100 drift-producer
```

### События идут, но drift-метрик нет

Окно ещё не заполнено — **partial window не анализируется**. Сравните:

```promql
drift_current_window_events
drift_window_size
```

При `WINDOW_SIZE=1000` и локальном producer'е с `PRODUCER_INTERVAL_SECONDS=0.2` первое окно набирается примерно за 200 секунд плюс время старта контейнеров.

### Метрики есть только для части features

Имена колонок должны совпадать **до символа** в трёх местах: reference CSV, `config/config.yaml` и события Kafka. Несовпадение проявляется не ошибкой, а отсутствующей метрикой.

```bash
head -n 1 data/reference.csv
```

Checked-in конфиг рассчитан на `age`, `income`, `country`, `prediction_score`. Для другого датасета перегенерируйте:

```bash
uv run python tools/build_config.py
docker compose --profile realtime restart analyzer
```

### `drift_current_window_events` перестал расти

Producer или внешний источник прекратил отправку. Счётчик `drift_events_processed_total` при этом тоже замирает. Смотрите логи источника событий.

---

## Статусы ведут себя не так, как ожидалось

### PSI меняется, но статус остаётся `OK`

Ожидаемое поведение, если окно всё ещё статистически близко к reference. Метрика растёт, но порог не перейден.

Сверьте фактические значения с применёнными порогами:

```promql
drift_metric_value{feature="age", metric="psi"}
drift_resolved_threshold{feature="age", metric="psi", level="warning"}
```

Именно `drift_resolved_threshold` показывает порог **после** применения приоритета `feature local > global` — сравнивать надо с ним, а не с `drift_threshold`.

Для demo-режима усильте drift: увеличьте `magnitude` или уменьшите `ramp_steps` в `tools/demo_producer/drift_config.yaml`. См. [docs/demo-producer.md](demo-producer.md).

### Drift-сценарий producer'а не работает

Правило применяется **только если имя feature присутствует в reference dataset**. Правило для отсутствующей колонки пропускается **молча** — без ошибки и предупреждения.

Если в `drift_config.yaml` указан `feature_1`, а в датасете `age` — ничего не произойдёт.

```text
1. имена в drift_config.yaml совпадают с заголовком reference.csv
2. те же features присутствуют в config/config.yaml
3. текущий шаг producer'а превысил start_step
4. magnitude достаточна для перехода порога
```

Producer читает конфиг при старте:

```bash
docker compose --profile realtime --profile local-kafka restart drift-producer
```

### `drift_overall_status = 2`, но всё выглядит нормально

Overall-статус работает по worst-case: одна critical-метрика на одной feature делает critical весь отчёт. Это может быть единственная шумная метрика.

Масштаб показывает `drift_active_alerts` — число input features в critical. Дальше локализуйте:

```promql
drift_status_feature == 2
drift_status == 2
```

### `chi2` постоянно в critical

Два возможных объяснения.

**Вырождение на больших окнах.** `chi2` — это p-value, и при `WINDOW_SIZE=1000` даже незначительное отличие в распределении категорий даёт значение близкое к нулю. Если `chi2` красный, а `cramer_v` и `psi` спокойны — это артефакт размера выборки. Для realtime-мониторинга `cramer_v` устойчивее.

**Перепутано направление порогов.** У `chi2` меньше = хуже, поэтому `warning > critical`:

```yaml
chi2:
  warning: 0.05
  critical: 0.01
```

Обратный порядок даст противоположное поведение. См. [docs/metrics.md](metrics.md#chi2-и-обратное-направление).

### Prediction drift есть, а `drift_active_alerts` не растёт

Так и задумано: счётчик считает **только input features**. Prediction status публикуется отдельной series с `feature="prediction"`.

```promql
drift_status_feature{feature="prediction"}
```

---

## Stream health

### `drift_stream_status = -1` при работающем потоке

Не ошибка. Статус вычисляется только по тем метрикам, для которых в блоке `stream_drift` конфига заданы thresholds. Пустой блок → `-1` («нечего проверять»).

Checked-in конфиг покрывает две метрики:

```yaml
stream_drift:
  drift_event_time_lag_seconds:
    warning: 30
    critical: 120
  drift_late_event_rate:
    warning: 0.01
    critical: 0.05
```

`window_time_span`, `max_event_gap` и `invalid_event_time_rate` публикуются, но в статус не входят, пока для них не заданы пороги.

### `drift_late_events_total` растёт непрерывно

Это lifetime counter — он монотонно растёт по определению и **не является текущим health signal**. В alert-условиях используйте производную или window-local gauge:

```promql
rate(drift_late_events_total[5m])
drift_late_event_rate
```

То же относится к `drift_out_of_order_events_total`.

### `drift_last_analysis_age_seconds` растёт монотонно

Анализы не завершаются. Ожидаемый порядок величины:

```text
WINDOW_SIZE × средний интервал между событиями
```

Для локального demo — около 200 секунд, поэтому значение выше ~400 заслуживает внимания.

Проверьте, не падают ли окна на анализе:

```promql
rate(drift_events_processed_total[10m]) > 0
  and rate(drift_analysis_runs_total[10m]) == 0
```

События идут, а успешных анализов нет → bad windows. Смотрите логи analyzer.

### Окна теряются

При падении анализа окно отбрасывается, offset **всё равно коммитится**, обработка продолжается со следующего окна. Это сознательный выбор в пользу resilience — иначе одно некорректное окно зациклило бы сервис.

Цена: окно теряется безвозвратно. Расхождение `drift_events_processed_total` и `drift_analysis_runs_total` показывает масштаб потерь.

См. [docs/realtime.md](realtime.md#поведение-при-плохом-окне).

---

## Adversarial validation

### `drift_av_available = 0` не меняется

Метрика — **результат, а не переключатель**: `0` означает «успешного snapshot ещё нет», и причин три.

```text
1. adversarial_validation.enabled = false в конфиге
2. первое полное окно ещё не набралось
3. AV упал или не прошёл проверку минимального размера выборки
```

Отдельной метрики «AV включён» нет — различить случаи можно только по логам analyzer.

Проверьте требования к размеру:

```text
WINDOW_SIZE >= n_splits
reference rows >= n_splits
```

При корректной конфигурации первый AV планируется уже на первом полном окне. См. [docs/adversarial-validation.md](adversarial-validation.md).

### `drift_av_last_run_timestamp_seconds = -1`

До первого успешного AV это sentinel «данных нет», а не Unix timestamp. Если при этом `drift_av_available = 1` — рассогласование, повод смотреть логи exporter'а и analyzer'а.

При построении Grafana-панелей такие значения стоит фильтровать.

### AV-метрики не обновляются

Ожидаемо между AV runs. Метрики отдают последний snapshot и не помечаются как stale — плоская линия нормальна.

Актуальность определяется по `drift_av_last_run_timestamp_seconds`. При `interval_minutes=30` и локальном demo (окно ~200 секунд) второй AV произойдёт примерно на девятом-десятом окне.

Для demo-сессии уменьшите `interval_minutes` в конфиге.

### `drift_av_driver_similarity_previous` пуста

На первом AV предыдущего snapshot'а не существует — в Grafana отображается `–`. Метрика становится содержательной со второго AV run.

### Высокий ROC AUC, но топ-драйверы бессмысленны

Смотрите `drift_av_driver_consistency` — mean pairwise cosine similarity importance-векторов между CV folds. Низкое значение означает, что модель на каждом fold ловит разницу через разные признаки, и ranking нестабилен.

Дополнительно проверьте разброс по folds:

```promql
drift_av_roc_auc_cv_max - drift_av_roc_auc_cv_min
```

Если разброс сравним с отрывом `cv_mean` от 0.5, вывод о drift'е ненадёжен.

### ROC AUC близок к 1.0, в топе одна колонка

Признак утечки: в feature set попал идентификатор, timestamp или сама prediction column.

В realtime prediction исключается автоматически по конфигу. В offline нужен явный аргумент:

```python
av_report = analyzer.run_av(current_df, prediction_col="prediction_score")
```

### ROC AUC заметно ниже 0.5

Не «очень хорошо», а аномалия. Обычно указывает на проблему с разметкой или CV-схемой. Стоит проверить логи, а не трактовать как отсутствие drift'а.

---

## Offline report

### Секция Adversarial Validation отсутствует

Либо `av_report` не передан в `generate_html_report()`, либо использовался CLI — у `drift-guardian-report` нет аргумента для AV. Для отчёта с AV нужен Python API или notebook.

### Блок Prediction Drift не появился

В `ConfigBuildOptions` должны быть переданы **оба** параметра:

```python
ConfigBuildOptions(
    prediction_enabled=True,
    prediction_score_column="prediction_score",
)
```

По умолчанию prediction monitoring выключен.

### Метрики только для части колонок

Конфиг в offline строится по `reference_df`: колонки, отсутствующие в reference, не мониторятся. Сверьте наборы колонок обоих DataFrame.

### `run_av()` падает

Проверьте размер выборок — для cross-validation нужно достаточно строк в обеих. Требования — [docs/adversarial-validation.md](adversarial-validation.md).

---

## Grafana и Prometheus

### Дашборд пустой

Проверьте по цепочке от источника к потребителю:

```bash
# 1. exporter отдаёт метрики
curl -s http://localhost:8000/metrics | grep drift_overall_status

# 2. Prometheus видит target
open http://localhost:9090/targets
```

Затем в Prometheus выполните `drift_overall_status`. Если данных нет — проблема в scrape config; если есть, а в Grafana пусто — в datasource provisioning.

### Панели показывают «No data» вместо `-1`

Значение `-1` — это «insufficient_data / not configured», и оно валидно. Если панель его скрывает, проверьте фильтры запроса. Для исключения таких точек:

```promql
drift_overall_status != -1
```

### Метрики не различаются между mock и realtime

Так и задумано. Оба exporter'а relabel'ятся в общий `job="drift-exporter"` — именно это позволяет дашбордам работать в обоих режимах без правок. Различаются они стандартным label `instance`.

Custom `mode` label **не используется** — не стоит на него рассчитывать в запросах.

См. [docs/prometheus-contract.md](prometheus-contract.md#scrape-configuration).

### Telegram-уведомления не приходят

`contact-points.yaml` ссылается на `$TELEGRAM_BOT_TOKEN` — переменная должна быть в `.env` до старта Grafana.

> Не заменяйте ссылку на переменную literal-значением в Git. Если token когда-либо попадал в репозиторий, перевыпустите его.

### Alert не срабатывает при видимом critical

Правила используют streak-метрики, а не мгновенные статусы: требуется `warning`/`critical` на **4 последовательных analysis windows**. Для input drift при `WINDOW_SIZE=1000` и локальном producer'е это порядка 13 минут.

```promql
drift_overall_status_streak
```

Для AV логика другая: `drift_av_status_streak` увеличивается **только при новом AV result**. При `interval_minutes=30` streak длиной 4 означает два часа наблюдений, а не четыре окна.

См. [docs/alerting.md](alerting.md).

---

## Полный сброс

Когда состояние запуталось и проще начать заново:

```bash
docker compose --profile mock --profile realtime --profile local-kafka down --remove-orphans

# вместе с volume Grafana
docker compose --profile mock --profile realtime --profile local-kafka down --remove-orphans --volumes
```

Затем чистый старт:

```bash
docker compose --profile realtime --profile local-kafka up -d --build
```

Флаг `--volumes` удалит сохранённое состояние Grafana. Provisioned datasources, дашборды и alert rules восстановятся автоматически, а вручную созданные объекты — нет.

---

## Связанные документы

- [docs/realtime.md](realtime.md) — окна, offsets, stream health
- [docs/adversarial-validation.md](adversarial-validation.md) — AV и интерпретация
- [docs/metrics.md](metrics.md) — метрики и thresholds
- [docs/prometheus-contract.md](prometheus-contract.md) — series и scrape config
- [docs/external-kafka.md](external-kafka.md) — подключение к внешнему кластеру
- [docs/demo-producer.md](demo-producer.md) — drift-сценарии
- [docs/offline.md](offline.md) — offline API и HTML report