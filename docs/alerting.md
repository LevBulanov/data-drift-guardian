# Alerting

Документ описывает provisioned-конфигурацию Grafana Alerting в Data Drift Guardian: набор alert rules, логику подавления шума через streak-счётчики и выдержку `for`, маршрутизацию по severity, шаблон Telegram-сообщений и time intervals.

Все файлы лежат в `monitoring/grafana/provisioning/alerting/` и загружаются Grafana при старте (`apiVersion: 1`). Изменения в UI, сделанные поверх provisioned-правил, не сохраняются – правьте YAML и перезапускайте Grafana.

---

## 1. Состав конфигурации

| Файл | Что определяет |
|------|----------------|
| `alert-rules.yaml` | 14 alert rules в 5 группах, папка `Drift Guardian` |
| `contact-points.yaml` | Telegram receiver `Drift Guardian Alerts Telegram` |
| `policies.yaml` | Notification policy tree: routes по `severity` |
| `templates.yaml` | Шаблон сообщения `drift_guardian.message` |
| `mute-timings.yaml` | Time intervals `working-hours-morning` and `working-hours` |

Все Prometheus-запросы используют datasource с UID `cfxnbdacrd728d`. Если вы подключаете свой Prometheus, этот UID нужно заменить в соответствующих `datasourceUid` и вложенных `model.datasource.uid`. Служебный UID `__expr__` у expression-запросов `C` менять нельзя.

---

## 2. Группы правил

Все пять групп живут в folder `Drift Guardian` с `interval: 10s` – Grafana пересчитывает условия каждые 10 секунд.

### 2.1 Window Drift

| Rule | UID | Severity | Scope |
|------|-----|----------|-------|
| `Input Data Drift – Critical` | `cfz1chowchhq8c` | critical | overall |
| `Input Data Drift – Warning` | `dfz3caqbwckqob` | warning | overall |
| `Feature Drift – Critical` | `efz3e3h2h3gn4b` | critical | list |
| `Feature Drift – Warning` | `cfz45ainayk1sc` | warning | list |

`Input Data Drift` – агрегированный сигнал по всему окну: срабатывает от `drift_overall_status`. `Feature Drift` – per-feature, `max by (feature)` даёт отдельный alert instance на каждую задрифтившую фичу, отсюда `scope: list` в labels.

### 2.2 Prediction Drift

| Rule | UID | Severity | Scope |
|------|-----|----------|-------|
| `Prediction Drift Status – Warning` | `afz3v51xspjpca` | warning | overall |
| `Prediction Drift Status – Critical` | `dfz3xmztsy8lcd` | critical | overall |
| `Prediction Drift Metrics – Warning` | `efz3u94rcw6bke` | warning | list |
| `Prediction Drift Metrics – Critical` | `cfz3xhezruigwd` | critical | list |

Prediction-правила отфильтрованы селектором `{feature="prediction"}`, а все input-правила – `{feature!="prediction"}`. Это важное разделение: `prediction` технически лежит в тех же метриках, что и обычные фичи, и без `feature!="prediction"` попадал бы в input-алерты дважды.

`Status` – правило на уровне фичи `prediction` целиком (`drift_status_feature`), `Metrics` – на уровне отдельных метрик (`max by (feature, metric)`), поэтому список в сообщении разворачивается по `metric`.

### 2.3 Adversarial Validation

| Rule | UID | Severity |
|------|-----|----------|
| `AV Drift – Warning` | `dfz3zg8qn3oxse` | warning |
| `AV Drift – Critical` | `cfz40uwii8a9sd` | critical |

Оба правила выполняют четыре Prometheus-запроса: `av_status` плюс три информационных – `drift_av_roc_auc`, `drift_av_roc_auc_cv_min`, `drift_av_roc_auc_cv_std`. Служебный expression-запрос `C` сравнивает `av_status` с нужным уровнем, а три информационных значения подставляются в `description` через `$values.<refId>.Value`.

### 2.4 Stream Health

| Rule | UID | Severity | Выдержка |
|------|-----|----------|----------|
| `Stream Health – Warning` | `streamwarn01` | warning | `for: 5m` |
| `Stream Health – Critical` | `streamcrit01` | critical | `for: 1m` |

Оба правила умеют контролировать пять метрик event-time конвейера: `drift_event_time_lag_seconds`, `drift_window_time_span_seconds`, `drift_max_event_gap_seconds`, `drift_invalid_event_time_rate` и `drift_late_event_rate`. В расчёт входят только метрики, для которых exporter публикует `drift_stream_threshold`. В стандартном `config/config.yaml` настроены `drift_event_time_lag_seconds` и `drift_late_event_rate`.

Через `label_replace` каждой серии добавляется label `metric`, после чего значение сравнивается с warning- и critical-порогами того же `(instance, metric)`. Это позволяет безопасно работать с несколькими exporter targets без many-to-many matching.

Результат `stream_status` принимает значение `0`, `1` или `2`. Warning должен непрерывно держаться 5 минут. При переходе в critical его PromQL-ветка остаётся активной, пока значение не пробыло выше critical-порога одну минуту; затем warning закрывается одновременно с готовностью critical.  В сообщение выводятся instance, имя метрики, текущее значение и оба порога.

### 2.5 System Health

| Rule | UID | Severity | Выдержка |
|------|-----|----------|----------|
| `Drift Exporter – Unavailable` | `exporterdown01` | critical | `for: 1m` |
| `Drift Analysis – Stale` | `analysisstale01` | critical | `for: 1m` |

Первое правило срабатывает, если Prometheus не может scrape'ить ни один target с `job="drift-exporter"`. Второе сравнивает текущее время с `drift_report_timestamp_seconds` и срабатывает, если новый drift report не появлялся более 400 секунд. Вместе они различают недоступный exporter и работающий exporter с остановившимся анализом.

---

## 3. Streak и `for`: как гасится шум

Для десяти drift-правил условие – не просто «статус == critical», а «статус == critical **и** это состояние держится минимум 4 результата подряд».

Пример из `Input Data Drift – Critical`:

```promql
max(drift_overall_status)
  and on()
  (max(drift_overall_status_streak{status="critical"}) >= 4)
```

Левая часть возвращает числовое значение статуса (`0` / `1` / `2`), правая работает как фильтр: если streak-счётчик меньше 4, `and` не даст ни одного сэмпла и правило останется в Normal. Само значение alert берёт из левой части – поэтому threshold-условие `C` сравнивает результат с `1` (warning) или `2` (critical) через evaluator `eq`.

Для per-feature правил `and on()` заменяется на `and on(feature, type)`, для per-metric – на `and on(feature, type, metric)`, чтобы streak матчился с тем же самым инстансом, а не с любым:

```promql
max by (feature, metric) (
  drift_status{feature="prediction"}
  and on(feature, type, metric)
  (drift_status_streak{feature="prediction",status="warning"} >= 4)
)
```

**Порог streak = 4 захардкожен в PromQL-выражениях**, он не выносится в env. Чтобы изменить чувствительность drift-алертов, нужно править `expr` в десяти правилах. Streak растёт только при появлении нового результата анализа, а не при каждом 10-секундном evaluation Grafana.

Window Drift и Prediction Drift дополнительно требуют, чтобы статус непрерывно сохранялся не менее 5 минут. Их фактическая задержка определяется обоими условиями: нужны и четыре результата подряд, и пять минут непрерывного статуса. AV обновляется раз в 30 минут и использует только streak ≥ 4 – дополнительная пятиминутная выдержка для него не применяется.

> Следствие: при `WINDOW_SIZE=1000` и неспешном producer'е от фактического начала дрифта до Telegram-сообщения проходит несколько минут. Это by design – иначе на коротких окнах алерты флапают.

Warning-выражения содержат handoff-ветку: когда статус уже стал critical, warning остаётся активным, пока соответствующий critical не набрал собственный streak и выдержку. В момент готовности critical warning закрывается, поэтому последовательности `WARNING → тишина → CRITICAL` нет. Текст закрытия говорит, что завершилось условие конкретной severity, а не утверждает, что система обязательно вернулась в Normal.

Stream Health не использует streak-счётчики. Там шум подавляется настройкой Grafana `for`: warning становится Firing после 5 минут непрерывного выполнения условия, critical – после 1 минуты. Если состояние нормализовалось раньше, отсчёт начинается заново.

---

## 4. No-data и error handling

Drift- и Stream Health правила сохраняют последнее состояние, а System Health fail-safe переходит в Alerting при отсутствии данных или ошибке datasource:

| Rule group | `noDataState` | `execErrState` |
|-----------|---------------|----------------|
| Window Drift (4 правила) | `KeepLast` | `KeepLast` |
| Prediction Drift (4 правила) | `KeepLast` | `KeepLast` |
| AV Drift (2 правила) | `KeepLast` | `KeepLast` |
| Stream Health (2 правила) | `KeepLast` | `KeepLast` |
| System Health (2 правила) | `Alerting` | `Alerting` |

`KeepLast` защищает от ложных «всё восстановилось», когда analyzer или Prometheus временно перестал отдавать метрики: последнее известное состояние сохраняется как при отсутствии данных, так и при ошибке выполнения запроса.

Потерю основных drift-серий контролируют отдельные System Health правила: недоступность exporter определяется через `up`, а остановка новых результатов – через возраст `drift_report_timestamp_seconds`. Если сам Prometheus не может выполнить health-запрос, `noDataState: Alerting` и `execErrState: Alerting` переводят эти правила в alerting-состояние.

---

## 5. Labels

Каждое правило проставляет набор labels, на которых потом строится маршрутизация:

| Label | Значения | Зачем |
|-------|----------|-------|
| `service` | `drift_guardian` | matcher в notification policy – обязателен |
| `severity` | `warning` / `critical` | выбор route и mute timing |
| `domain` | `input_drift` / `prediction_drift` / `adversarial_validation` / `stream_health` / `system_health` | группировка, фильтры в UI |
| `owner` | `ml_monitoring` / `data_engineering` | ownership |
| `scope` | `list` (на per-feature/per-metric и Stream Health) | переключает ветку шаблона |

Drift-правила принадлежат `ml_monitoring`; техническое состояние ingestion, exporter и свежесть анализа – `data_engineering`. Label `owner` не участвует в текущей маршрутизации и не меняет Telegram-канал: policies используют только `service` и `severity`.

---

## 6. Маршрутизация

`policies.yaml` – единственный источник маршрутизации. Per-rule `notification_settings` отсутствуют, поэтому все 14 правил проходят через это дерево и приходят в один receiver. Дочерние routes имеют разные matcher по severity; их тайминги одинаковы, а warning-route дополнительно ограничен `working-hours`:

```
root (receiver: Drift Guardian Alerts Telegram)
├── group_by: [grafana_folder, alertname]
├── group_wait: 30s / group_interval: 5m / repeat_interval: 4h
│
├── route: service=drift_guardian AND severity=critical
│     group_wait: 1m, group_interval: 1m, repeat_interval: 4h
│
└── route: service=drift_guardian AND severity=warning
      active_time_intervals: [working-hours]
      group_wait: 1m, group_interval: 1m, repeat_interval: 4h
```

`group_by: [grafana_folder, alertname]` – все инстансы одного правила попадают в одно сообщение. Именно это делает возможным list-шаблон: `Feature Drift – Critical` с пятью задрифтившими фичами придёт одним сообщением со списком, а не пятью отдельными.

`repeat_interval: 4h` на обоих дочерних routes ограничивает повтор одного и того же продолжающегося алерта: если между уведомлениями не было Resolve или существенного изменения группы, напоминание придёт не раньше чем через четыре часа.

---

## 7. Telegram contact point

```yaml
name: Drift Guardian Alerts Telegram
type: telegram
settings:
  bottoken: $TELEGRAM_BOT_TOKEN
  chatid: "-5433199058"
  parse_mode: HTML
  message: '{{ template "drift_guardian.message" . }}'
disableResolveMessage: false
```

Что нужно поменять под себя:

1. **`TELEGRAM_BOT_TOKEN`** – переменная окружения контейнера Grafana. Grafana раскрывает `$VAR` в provisioning-файлах при загрузке. Без неё contact point создастся, но отправка будет падать с 401.
2. **`chatid`** – жёстко задан. Замените на свой (для групп значение отрицательное, строкой). Получить: добавьте бота в чат и вызовите `getUpdates`.
3. **`parse_mode: HTML`** менять нельзя без переписывания шаблона – он весь построен на `<b>`, `<code>`.

`disableResolveMessage: false` включает сообщения о закрытии severity condition. Это может означать как восстановление, так и переход warning → critical; шаблон не утверждает, что сигнал обязательно вернулся в Normal.

---

## 8. Шаблон сообщения

`templates.yaml` определяет единственный шаблон `drift_guardian.message` под именем набора `drift_guardian`. Он ветвится на три случая.

### Ветка 1 – `DatasourceNoData`

Проверка `eq (index .CommonLabels "alertname") "DatasourceNoData"`. Итерируется по `.Alerts`, для firing отдаёт «⚪️ NO DATA» с именем правила из label `rulename` и подсказкой проверить Prometheus и realtime exporter; для resolved – «✅ DATA RESTORED».

#### Пример алерта `DatasourceNoData`

> ⚪️ **NO DATA**
>
> No current data was returned for:  
> **Input Data Drift – Warning**
>
> Check Prometheus and the realtime exporter.

> ✅ **DATA RESTORED**
>
> Data is available again for:  
> **Input Data Drift – Warning**

### Ветка 2 – `scope=list`

Срабатывает при `eq (index .CommonLabels "scope") "list"`. Читает три аннотации правила:

| Аннотация | Роль | Пример значения |
|-----------|------|-----------------|
| `list_label` | имя label, по которому разворачивается список | `feature` или `metric` |
| `list_title` | заголовок блока | `Critical metrics by feature` |
| `list_value` | значение рядом с элементом | `{{ $values.metric_count.Value }}` |

Шаблон рендерит `.Alerts.Firing` как bullet-список `• <code>{label}</code>: <code>{value}</code>`, ниже – `action`. Если в группе есть закрытые инстансы, отдельным блоком печатается «✅ SEVERITY CONDITION CLEARED» с их перечнем. Firing и resolved могут прийти в одном сообщении.

Эмодзи по severity: 🔴 critical, 🟠 warning, 🔔 иначе.  
⚪️ – для no data и ✅ для resolved.

Механика `list_value` заслуживает пояснения: значение вычисляется **на каждый alert instance** отдельно. В Feature Drift `metric_count` – это `count by (feature)`, поэтому рядом с каждой фичей стоит своё число задрифтивших метрик. Оба Feature-правила добавляют `or on (feature) (0 * max by (feature) (...))` – это гарантирует `0` вместо пропуска элемента, если `count` не вернул серию. Prediction Drift выводит текущее значение метрики, а Stream Health – текущее значение вместе с warning- и critical-порогами.

#### Пример алерта `scope=list`

> 🔴 **CRITICAL ALERT**  
> **Feature Drift – Critical**
>
> The following features have reached critical drift status compared with the reference data.
>
> **Critical metrics by feature:**  
> • `age`: `2`  
> • `income`: `1`
>
> **Action:** Inspect the affected features in the Feature-level Drift section.

#### Пример алерта Stream Health

> 🔴 **CRITICAL ALERT**  
> **Stream Health – Critical**
>
> The following stream metrics have remained in critical status for at least 1 minute.
>
> **Stream metrics above critical threshold:**  
> • `drift_event_time_lag_seconds`: `value=145.3200, warning=30.0000, critical=120.0000`
>
> **Action:** Inspect Stream Health and the producer/consumer event-time pipeline immediately.


### Ветка 3 – обычный алерт

Fallback для правил без `scope: list` (overall, AV и System Health). Итерируется по `.Alerts`, для каждого печатает `alertname`, затем `summary`, `description`, `action` – каждое через `with`, так что отсутствующие аннотации просто не выводятся. Для resolved сообщает, что условие этой severity больше не выполняется и сигнал мог восстановиться либо перейти на другой уровень.

Именно сюда попадают AV-правила со своим многострочным `description`:

```
<b>Adversarial Validation Summary</b>
• ROC AUC: <code>0.847</code>
• Worst-fold AUC: <code>0.812</code>
• CV std: <code>0.019</code>
```

Форматирование через `printf "%.3f"` – три знака после запятой.

#### Пример обычного алерта

> 🟠 **WARNING**  
> **Input Data Drift – Warning**
>
> Potential input data drift was detected in the latest analysis window.
>
> **Input Data Drift Summary**  
> • Features with warning status: `1 / 3`  
> • Warning metric alerts: `1`
>
> **Action:** Review the affected features and metric trends. Investigate if the warning persists across consecutive analysis windows.

#### Пример Adversarial Validation alert

> 🟠 **WARNING**  
> **AV Drift – Warning**
>
> Potential dataset shift was detected by adversarial validation.
>
> **Adversarial Validation Summary**  
> • ROC AUC: `0.687`  
> • Worst-fold AUC: `0.641`  
> • CV std: `0.028`
>
> **Action:** Inspect the drift drivers and analysis context in the Adversarial Validation section.


---

## 9. Time intervals

```yaml
muteTimes:
  - orgId: 1
    name: working-hours-morning
    time_intervals:
      - location: Europe/Moscow
        times:
          - start_time: "09:00"
            end_time: "13:00"
        weekdays:
          - monday:friday
  - orgId: 1
    name: working-hours
    time_intervals:
      - location: Europe/Moscow
        times:
          - start_time: "10:00"
            end_time: "18:00"
        weekdays:
          - monday:friday
```

`working-hours` используется warning-route и задаёт рабочее окно 10:00–18:00 МСК по будням. `working-hours-morning` – резервный интервал 09:00–13:00 МСК для возможных правил, которым требуется только утреннее окно. Сейчас он не подключён к notification policies. Critical-route не ссылается на time interval и активен постоянно.

---

## 10. Проверка и отладка

**Правила загрузились?**
Grafana → Alerting → Alert rules → folder `Drift Guardian`. Должно быть 14 правил в 5 группах. Если пусто – смотрите логи Grafana на ошибки парсинга YAML.

**Правило не переходит в Firing.** Проверьте условие вручную в Explore:

```promql
max(drift_overall_status_streak{status="critical"})
```

Если значение `< 4` – streak просто не дозрел. Если серии нет вообще – analyzer не публикует streak-метрики, проверьте `/metrics` на порту exporter'а.

**Правило Firing, но Telegram молчит.**
1. Alerting → Contact points → Test на `Drift Guardian Alerts Telegram`.
2. Проверьте, что `TELEGRAM_BOT_TOKEN` доехал в контейнер: `docker compose exec grafana env | grep TELEGRAM`.
3. Для warning-правил – вспомните §9: если вы заполнили `time_intervals`, вне окна уведомления не уйдут, хотя алерт будет Firing.

**Сообщение приходит с `{{ ... }}` или сломанной вёрсткой.** Шаблон не загрузился (проверьте `name: drift_guardian` в `templates.yaml`) либо HTML-теги не закрыты – Telegram Bot API отклоняет такие сообщения целиком с ошибкой `can't parse entities`, она видна в Grafana → Alerting → Notifications.

**Слишком много сообщений.** Повторы продолжающегося алерта ограничивает `repeat_interval: 4h` в дочерних routes. Частоту новых срабатываний также регулируют порог streak в drift-правилах (сейчас `>= 4`) и выдержка `for` в Stream Health (`5m` для warning, `1m` для critical).

**Проверить provisioning автоматически.** Запустите `uv run pytest tests/test_alerting_provisioning.py -q`. Тесты проверяют состав правил, owners, policy tree, time intervals, handoff warning → critical, instance-aware Stream Health, fallback для `count(...)` и System Health.

---

## 11. Связанные документы

- `docs/prometheus-contract.md` – семантика `drift_status`, `drift_status_streak`, `drift_overall_status` и их labels
- `docs/metrics.md` – как считаются значения, попадающие в `drift_metric_value`
- `docs/configuration.md` – thresholds, из которых выводится `warning` / `critical`
- `docs/realtime.md` – интервал анализа, определяющий реальную задержку алертов
- `docs/troubleshooting.md` – общие проблемы запуска стека
