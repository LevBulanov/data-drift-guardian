# Realtime с внешней Kafka

Profile `local-kafka` предназначен только для demo. В production-like сценарии analyzer подключается напрямую к внешнему Kafka cluster, а события в topic пишет ваш собственный сервис.

Документ описывает требования к кластеру, конфигурацию подключения и текущие ограничения реализации.

---

## Конфигурация

Минимальный набор в `.env`:

```env
KAFKA_BOOTSTRAP_SERVERS=broker1:9092
KAFKA_TOPIC=features-stream
KAFKA_GROUP_ID=drift-consumer
```

Запускается **только** profile `realtime` — локальные `kafka` и `drift-producer` при этом не поднимаются:

```bash
docker compose --profile realtime up -d --build
```

### Дополнительные параметры

```env
KAFKA_STARTUP_TIMEOUT_SECONDS=60
KAFKA_STARTUP_RETRY_SECONDS=1
WINDOW_SIZE=1000
LATE_EVENT_THRESHOLD_SECONDS=60
```

| Переменная | Default | Назначение |
|---|---:|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:19092` | адреса broker'ов |
| `KAFKA_TOPIC` | `features-stream` | input topic |
| `KAFKA_GROUP_ID` | `drift-consumer` | consumer group |
| `KAFKA_STARTUP_TIMEOUT_SECONDS` | `60` | сколько ждать доступности topic при старте |
| `KAFKA_STARTUP_RETRY_SECONDS` | `1` | интервал между попытками |

---

## Требования к кластеру

**Сетевая доступность.** Broker должен быть доступен **из контейнера** `analyzer`, а не только с хоста. Это разные вещи: `localhost:9092` внутри контейнера указывает на сам контейнер. Для broker'а на хост-машине используйте `host.docker.internal` (Docker Desktop) или адрес хоста в bridge-сети.

**Topic должен существовать заранее.** Topic из `KAFKA_TOPIC` подготавливает владелец кластера — проект не содержит `kafka-init` и сам topic не создаёт.

Если на broker'е разрешён auto-create, topic может появиться по политике самого Kafka. Но рассчитывать на это не стоит: поведение зависит от конфигурации кластера, а при auto-create число партиций и replication factor окажутся дефолтными для broker'а, а не осознанно выбранными.

**Consumer group.** `KAFKA_GROUP_ID` определяет, откуда analyzer продолжит чтение при рестарте. При переиспользовании существующей group analyzer подхватит её committed offsets — учитывайте это, если group уже использовалась другим сервисом.

---

## Ограничения текущей реализации

### SASL/SSL не прокинуты

Consumer параметризует только три свойства:

```text
bootstrap.servers
group.id
topic
```

SASL/SSL credentials и прочие Kafka client properties через Compose **пока не передаются**. Подключение к защищённому кластеру потребует отдельной конфигурации клиента.

Практически это означает, что «из коробки» работает только кластер с `PLAINTEXT` listener, доступный analyzer'у без аутентификации.

### Одна topic, без partition-специфичной логики

Analyzer читает единственный topic. Consumer не реализует собственного распределения партиций — это делает стандартный rebalance Kafka.

Следствие для масштабирования: несколько реплик analyzer'а в одной consumer group разделят партиции между собой, и **каждая будет считать drift по своему подмножеству событий**. Окна получатся разными у разных реплик, метрики в Prometheus начнут конфликтовать по `instance`. Для корректной работы держите один экземпляр analyzer'а на topic.

### Offset коммитится после попытки анализа

При падении анализа окна offset всё равно подтверждается, и окно теряется безвозвратно. Это сознательный выбор в пользу resilience, но означает отсутствие гарантии обработки каждого события.

Подробнее — [docs/realtime.md](realtime.md#поведение-при-плохом-окне).

---

## Формат событий

Analyzer ожидает JSON-сообщения. До попадания в `WindowBuffer` выполняется event-level validation: payload должен быть JSON-объектом, `event_id` — строкой/целым, `event_time` — timezone-aware ISO-8601, а feature values — JSON-скалярами с конечными `float`-значениями. Такие структурно некорректные сообщения отбрасываются по одному.

Reference-based schema validation выполняется **после набора полного окна**: `OfflineWrapper.analyze_df()` вызывает `SchemaChecker.check_df()`. Поэтому required fields из `config/config.yaml` должны присутствовать в current DataFrame и иметь dtype, совместимый с reference. Несовпадение имени или типа не даёт «просто отсутствующую метрику» — анализ полного окна падает, окно отбрасывается и consumer продолжает со следующим.

Порядок действий при подключении своего потока:

```text
1. подготовить reference.csv с теми же колонками, что в событиях
2. сгенерировать config: uv run python tools/build_config.py
3. задать KAFKA_* в .env
4. поднять profile realtime
```

См. [docs/reference-data.md](reference-data.md) и [docs/configuration.md](configuration.md).

---

## Проверка подключения

```bash
docker compose --profile realtime ps -a
docker compose --profile realtime logs --tail=200 analyzer
```

Первый успешный признак — analyzer не упал по таймауту ожидания topic. Дальше смотрите наполнение окна:

```bash
curl -s http://localhost:8000/metrics | grep -E 'drift_current_window_events|drift_events_processed_total'
```

<details>
<summary>PowerShell</summary>

```powershell
curl.exe -s http://localhost:8000/metrics | Select-String "drift_current_window_events|drift_events_processed_total"
```

</details>

`drift_events_processed_total` растёт → события проходят event-level parsing и добавляются в окно. Это ещё не гарантирует успешную reference-based schema validation: она выполняется только после набора полного `WINDOW_SIZE`. Первый drift-отчёт появится после первого успешно проанализированного полного окна.

---

## Диагностика

**Analyzer падает при старте с таймаутом.** Истёк `KAFKA_STARTUP_TIMEOUT_SECONDS` в ожидании broker или topic. Проверьте по порядку:

```text
1. доступность broker из контейнера, а не с хоста
2. topic существует на кластере
3. KAFKA_BOOTSTRAP_SERVERS указывает на advertised listeners broker'а
4. нет ли на кластере обязательной аутентификации — SASL/SSL не поддерживается
```

Для медленно поднимающегося кластера увеличьте таймаут:

```env
KAFKA_STARTUP_TIMEOUT_SECONDS=180
```

**Analyzer подключился, но `drift_events_processed_total` не растёт.** Consumer подписан, но сообщений нет либо они отбрасываются на event-level parsing. Ненулевой `drift_invalid_event_time_rate` указывает именно на проблемы `event_time`; другие структурные ошибки (`event_id`, JSON shape, не-скалярные values) нужно смотреть в логах consumer. Если rate равна нулю и счётчик стоит, по одним метрикам нельзя отличить пустой topic от потока только с такими структурными ошибками — проверьте логи и committed offsets.

При переиспользовании старой group проверьте, не вычитаны ли offsets до конца — analyzer начнёт с committed позиции, а не с начала topic.

**События идут, но метрики не появляются.** Окно ещё не заполнено — partial window не анализируется. Сверьте `drift_current_window_events` с `drift_window_size`.

**События принимаются, но новые drift reports не появляются.** Проверьте логи на `analysis failed`: отсутствие required field или несовместимый dtype в полном окне приводит к ошибке `SchemaChecker`, после чего окно отбрасывается. Имена и типы monitored fields должны совпадать с reference и конфигом.

Остальные сценарии — [docs/troubleshooting.md](troubleshooting.md).

---

## Переход между demo и внешней Kafka

`analyzer` и `drift-mock-exporter` публикуют один host port `8000` и одновременно работать не могут. Перед сменой режима удалите предыдущие сервисы:

```bash
docker compose --profile mock --profile realtime --profile local-kafka rm -sf \
  analyzer drift-mock-exporter drift-producer kafka
```

Prometheus и Grafana не принадлежат ни одному profile и остаются поднятыми между режимами — дашборды и alert rules переживают переключение без правок за счёт общего `job="drift-exporter"`.

См. [docs/prometheus-contract.md](prometheus-contract.md#scrape-configuration).

---

## Связанные документы

- [docs/realtime.md](realtime.md) — окна, offsets, stream health
- [docs/reference-data.md](reference-data.md) — подготовка baseline
- [docs/configuration.md](configuration.md) — генерация конфига под свои колонки
- [docs/prometheus-contract.md](prometheus-contract.md) — метрики и scrape config
- [docs/demo-producer.md](demo-producer.md) — локальный demo вместо внешнего кластера