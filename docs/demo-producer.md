# Demo producer

`tools/demo_producer/` — локальный Kafka producer для demo-режима. Генерирует события на основе reference dataset и умеет постепенно вносить в них контролируемый drift, чтобы можно было наблюдать переходы `OK → Warning → Critical` в Grafana.

Producer входит в profile `local-kafka` и не предназначен для production — в реальном сценарии события в topic пишет ваш собственный сервис. См. [docs/external-kafka.md](external-kafka.md).

---

## Как это работает

Producer читает reference dataset, семплирует из него строки и отправляет их в Kafka как JSON-события с заданной частотой. Пока drift-правила не активированы, поток статистически совпадает с reference — analyzer видит `OK`.

Дальше вступают в силу правила из `drift_config.yaml`: начиная с указанного шага producer начинает искажать значения features, постепенно наращивая силу искажения. Analyzer фиксирует растущие drift-метрики.

```text
data/reference.csv
        │
        ▼
  сэмплирование строки
        │
        ▼
 применение drift-правил   ← tools/demo_producer/drift_config.yaml
   (если step >= start_step)
        │
        ▼
      JSON event
        │
        ▼
   Kafka topic
```

---

## Запуск

Producer поднимается вместе с локальным broker:

```bash
docker compose --profile realtime --profile local-kafka up -d --build
```

Логи:

```bash
docker compose --profile realtime --profile local-kafka logs --tail=100 drift-producer
```

Параметры работы:

```env
PRODUCER_INTERVAL_SECONDS=0.2
PRODUCER_RANDOM_SEED=42
```

`PRODUCER_RANDOM_SEED` фиксирует генерацию — при одинаковом seed и одинаковом `drift_config.yaml` последовательность событий воспроизводима. Это удобно, когда нужно повторить конкретную картину на дашборде.

---

## Конфигурация drift

```text
tools/demo_producer/drift_config.yaml
```

### Типы drift

| Тип | Применим к | Что делает |
|---|---|---|
| `shift` | numeric | аддитивный сдвиг значения |
| `scale` | numeric | изменение масштаба |
| `noise` | numeric | добавление Gaussian noise |
| `categorical_swap` | categorical | замена части значений на целевую категорию |

### Общие параметры расписания

```yaml
start_step: 1000   # с какого сообщения начинается drift
ramp_steps: 1000   # за сколько сообщений он доходит до полной силы
```

`start_step` отсчитывается в сообщениях, а не в секундах и не в окнах. При `PRODUCER_INTERVAL_SECONDS=0.2` шаг 1000 наступает примерно через 200 секунд после старта producer'а.

`ramp_steps` задаёт плавность: в интервале от `start_step` до `start_step + ramp_steps` искажение нарастает от нуля до полной величины. В коде значение ограничивается минимумом `1`, поэтому `ramp_steps: 0` и `ramp_steps: 1` эквивалентны: на событии `start_step` сила ещё `0`, на следующем шаге — уже полная.

### Параметры по типам

```yaml
# shift
type: shift
magnitude: 10          # величина сдвига в единицах feature

# scale
type: scale
magnitude: 0.35        # относительное изменение масштаба

# noise
type: noise
magnitude: <sigma>     # масштаб добавляемого шума

# categorical_swap
type: categorical_swap
probability: 0.5              # доля заменяемых значений
target_category: drift_country  # на что заменять
```

> `magnitude` для `shift` указывается **в исходных единицах feature**. Для `age` сдвиг на `10` значителен, для `income` в рублях — незаметен. Это же соображение применимо к threshold'ам `wasserstein_distance` — см. [docs/metrics.md](metrics.md).

---

## Правила применяются только к features из reference

Самая частая причина «producer работает, а drift не появляется».

Правило применяется **только если имя feature присутствует в reference dataset**. Если `drift_config.yaml` содержит `feature_1`, а датасет содержит `age` — правило будет **молча пропущено**: ни ошибки, ни предупреждения.

Проверка перед запуском:

```bash
head -n 1 data/reference.csv
```

<details>
<summary>PowerShell</summary>

```powershell
Get-Content .\data\reference.csv -TotalCount 1
```

</details>

Имена в `drift_config.yaml` должны совпадать с заголовком CSV до символа. Дополнительно стоит сверить их с `config/config.yaml` — искажать feature, которая не мониторится analyzer'ом, бессмысленно.

---

## Checked-in сценарий

Текущий `tools/demo_producer/drift_config.yaml` начинает drift **внутри первого окна** (`WINDOW_SIZE=1000`):

```yaml
features:
  age:
    type: shift
    magnitude: 2.5
    start_step: 500
    ramp_steps: 200

  income:
    type: scale
    magnitude: 0.8
    start_step: 500
    ramp_steps: 200

  country:
    type: categorical_swap
    probability: 0.6
    target_category: rare_value
    start_step: 800
    ramp_steps: 1
```

В checked-in YAML также есть правило с ключом `prediction_metrics`. Producer матчится **по имени колонки reference**, поэтому для стандартной колонки `prediction_score` такое правило молча пропускается. Если нужно симулировать prediction drift, ключ должен называться `prediction_score`:

```yaml
  prediction_score:
    type: noise
    magnitude: 1.2
    start_step: 300
    ramp_steps: 50
```

При текущих `start_step` чистого первого окна нет: `age`/`income` начинают сдвигаться с шага 500, `country` — с 800. Первый realtime AV поэтому сравнивает reference уже с частично искажённым первым current window.

### Вариант с чистым первым окном

Если для демонстрации нужен явный baseline `OK` до начала drift, используйте расписание со `start_step >= WINDOW_SIZE`, например:

```yaml
features:
  age:
    type: shift
    magnitude: 10
    start_step: 1000
    ramp_steps: 1000

  income:
    type: scale
    magnitude: 0.35
    start_step: 1000
    ramp_steps: 1000

  country:
    type: categorical_swap
    probability: 0.5
    target_category: drift_country
    start_step: 1500
    ramp_steps: 500
```

Тогда окно `#1` (сообщения `0–999`) остаётся близким к reference, а drift начинает нарастать со второго окна.

---

## Подгонка сценария под свои цели

### Быстрее увидеть drift

```yaml
start_step: 200
ramp_steps: 200
```

При `WINDOW_SIZE=1000` drift начнётся внутри первого окна. Это ещё быстрее checked-in сценария, но чистого baseline-окна не будет, и первый AV run обучится уже на частично искажённых данных.

### Резкий скачок вместо тренда

```yaml
start_step: 1000
ramp_steps: 0
```

Полезно для проверки alert-логики: streak-условие требует 4 последовательных окна со статусом `warning`/`critical` , и при мгновенном скачке момент срабатывания alert'а предсказуем. См. [docs/alerting.md](alerting.md).

### Довести метрику до `critical`

Если drift виден, но статус остаётся `OK` или `warning`, увеличивайте `magnitude` (или `probability` для categorical). Ориентируйтесь на фактические значения в Prometheus:

```bash
curl -s http://localhost:8000/metrics | grep drift_metric_value
```

<details>
<summary>PowerShell</summary>

```powershell
curl.exe -s http://localhost:8000/metrics | Select-String "drift_metric_value"
```

</details>

Сравните с `drift_resolved_threshold` — именно эти пороги фактически применяются к конкретной feature .

### Разнести features по времени

Разные `start_step` для разных features показывают, как `drift_active_alerts` растёт постепенно, а `drift_overall_status` переключается по worst-case логике. Для демонстрации того, что overall-статус ухудшает одна единственная feature, это наглядно.

### Применение изменений

Producer читает конфиг при старте, поэтому после правки YAML:

```bash
docker compose --profile realtime --profile local-kafka restart drift-producer
```

Счётчик шагов при этом начинается заново, а окна analyzer'а продолжают набираться со своего места — первые окна после рестарта могут оказаться смешанными. Для чистого прогона перезапустите и analyzer.

---

## Диагностика

**Producer запущен, но drift не появляется.** Порядок проверки:

```text
1. имена features в drift_config.yaml совпадают с заголовком reference.csv
2. те же features присутствуют в config/config.yaml
3. текущий шаг producer'а уже превысил start_step
4. magnitude достаточна, чтобы метрика перешла порог
```

Пункт 1 — самая частая причина: правило для отсутствующей feature пропускается без сообщения .

**`PSI` меняется, но статус остаётся `OK`.** Ожидаемо, если окно всё ещё статистически близко к reference . Увеличьте `magnitude` или уменьшите `ramp_steps`.

**Analyzer не видит событий.** Это уже не про producer — проверьте, что оба сервиса поднялись и analyzer подключился к broker:

```bash
docker compose --profile realtime --profile local-kafka ps -a
docker compose --profile realtime --profile local-kafka logs --tail=200 analyzer
```

**Метрики появились, но не растут.** Проверьте `drift_current_window_events` — если счётчик стоит, producer перестал отправлять события, и стоит смотреть его логи.

Остальные сценарии — [docs/troubleshooting.md](troubleshooting.md).

---

## Связанные документы

- [docs/reference-data.md](reference-data.md) — подготовка reference dataset
- [docs/metrics.md](metrics.md) — drift-метрики и thresholds
- [docs/realtime.md](realtime.md) — окна и обработка событий
- [docs/alerting.md](alerting.md) — streak-логика alert rules
- [docs/external-kafka.md](external-kafka.md) — переход от demo к внешнему broker