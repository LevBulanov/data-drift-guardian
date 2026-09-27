# Realtime: окна, offsets и stream health

Документ описывает поведение realtime-слоя: как формируются analysis windows, что происходит с Kafka offsets, как обрабатываются сбои и какие метрики отражают здоровье потока.

Обзор архитектуры и команды запуска — в [README](../README.md).

---

## Analysis windows

Размер окна задаётся переменной окружения:

```env
WINDOW_SIZE=1000
```

Consumer формирует **полные непересекающиеся окна**. Partial window не анализируется: если в буфере меньше `WINDOW_SIZE` валидных событий, drift-анализ не запускается.

Это означает две вещи на практике:

- после старта analyzer первый отчёт появится только когда наберётся полное окно. При локальном demo-producer'е с `PRODUCER_INTERVAL_SECONDS=0.2` и `WINDOW_SIZE=1000` это порядка 200 секунд плюс время старта контейнеров;
- окна не скользящие. Окно `#2` не содержит ни одного события из окна `#1`, поэтому соседние отчёты статистически независимы, а не сглажены перекрытием.

Текущее заполнение окна видно через:

```text
drift_current_window_events
drift_window_size
```

---

## Порядок обработки события

Для каждого валидного события consumer выполняет следующее:

1. событие добавляется в `WindowBuffer`;
2. обновляются stream metrics;
3. Kafka offset сохраняется локально через `store_offsets`.

Когда окно заполнилось до `WINDOW_SIZE`:

4. выполняется Core analysis;
5. report экспортируется в Prometheus;
6. при успешном анализе увеличивается `drift_analysis_runs_total`;
7. **после попытки анализа** выполняется synchronous Kafka commit;
8. окно очищается, начинается следующее.

Ключевой момент — шаг 7. Commit происходит после попытки анализа, а не после успеха. Из этого следует поведение при сбоях, описанное ниже.

---

## Поведение при плохом окне

Если анализ полного окна падает с исключением, consumer **не завершается**. Вместо этого:

- ошибка логируется;
- окно отбрасывается;
- offset всё равно подтверждается;
- обработка продолжается со следующего окна.

Это сознательное решение в пользу resilience: одно некорректное окно не должно зациклить сервис на одном и том же наборе сообщений. Альтернатива — не коммитить offset и повторить окно — привела бы к бесконечному retry на данных, которые analyzer в принципе не может обработать.

**Цена этого решения:** окно теряется безвозвратно. Для мониторинга drift это приемлемо (следующее окно придёт через минуты), но если вам нужна гарантия обработки каждого события, realtime-слой в текущем виде такой гарантии не даёт.

Диагностировать потерянные окна можно по расхождению счётчиков:

```promql
# сколько событий прошло через consumer
drift_events_processed_total

# сколько анализов реально завершилось успешно
drift_analysis_runs_total
```

Если `drift_events_processed_total` растёт, а `drift_analysis_runs_total` стоит на месте дольше, чем время набора одного окна — смотрите логи analyzer.

---

## Некорректные отдельные сообщения

Отдельные Kafka messages отбрасываются, не доходя до окна, если у них:

- невалидный JSON;
- не проходит schema validation;
- отсутствует или некорректен event time.

Такие сообщения **не добавляются в analysis window** и не влияют на drift-метрики. Их доля отражается в stream-health метриках, в частности `drift_invalid_event_time_rate`.

Отличие от bad window: здесь отбрасывается одно сообщение, окно продолжает набираться. При bad window теряется весь набор из `WINDOW_SIZE` событий.

---

## Stream health

Помимо drift-метрик exporter публикует характеристики самого потока, основанные на event time.

### Gauges (window-local)

| Метрика | Смысл |
|---|---|
| `drift_event_time_lag_seconds` | отставание event time от wall clock |
| `drift_window_time_span_seconds` | временной охват окна: от самого раннего до самого позднего event time |
| `drift_max_event_gap_seconds` | максимальный разрыв между последовательными событиями внутри окна |
| `drift_invalid_event_time_rate` | доля событий с некорректным event time |
| `drift_late_event_rate` | доля late-событий в текущем окне |

Событие считается late, если его lag превышает:

```env
LATE_EVENT_THRESHOLD_SECONDS=60
```

### Counters (lifetime)

```text
drift_late_events_total
drift_out_of_order_events_total
```

Это накопительные счётчики за весь срок жизни процесса. **Они не являются текущим health signal** — использовать их напрямую в alert-условии не стоит, поскольку они монотонно растут. Для alerting и дашбордов берите `rate()`/`increase()` или window-local gauge `drift_late_event_rate`.

---

## `drift_stream_status`

Агрегированный статус потока. Вычисляется **только** по тем stream-метрикам, для которых в конфиге заданы thresholds в блоке `stream_drift`.

Если thresholds не настроены, статус остаётся:

```text
-1 = insufficient_data / not configured
```

Это не ошибка — это означает «нечего проверять». Частая причина недоумения: stream-метрики публикуются и видны в Prometheus, но `drift_stream_status` показывает `-1`, потому что в `stream_drift` пусто.

Checked-in конфиг использует:

```yaml
stream_drift:
  drift_event_time_lag_seconds:
    warning: 30
    critical: 120
  drift_late_event_rate:
    warning: 0.01
    critical: 0.05
```

Остальные stream-метрики (`window_time_span`, `max_event_gap`, `invalid_event_time_rate`) публикуются, но в статус не входят, пока для них не заданы thresholds.

> Отдельного Grafana alert rule для `drift_stream_status` в текущем provisioning нет — см. [docs/alerting.md](alerting.md).

---

## Свежесть данных

```text
drift_last_analysis_age_seconds
drift_report_timestamp_seconds
```

`drift_last_analysis_age_seconds` — время с момента последнего успешного анализа. Это самый прямой индикатор «analyzer жив и обрабатывает поток»: если значение растёт монотонно и превышает ожидаемое время набора окна с запасом, поток либо остановился, либо окна падают на анализе.

Ожидаемый порядок величины:

```text
WINDOW_SIZE × средний интервал между событиями
```

Для локального demo — около 200 секунд, поэтому значение выше ~400 секунд заслуживает внимания.

---

## Связанные документы

- [docs/prometheus-contract.md](prometheus-contract.md) — полный список series и labels
- [docs/metrics.md](metrics.md) — drift-метрики и thresholds
- [docs/external-kafka.md](external-kafka.md) — подключение к внешнему кластеру
- [docs/troubleshooting.md](troubleshooting.md) — диагностика