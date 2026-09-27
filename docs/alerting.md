# Alerting

Документ описывает provisioned-конфигурацию Grafana Alerting в Data Drift Guardian: набор alert rules, логику подавления шума через streak-счётчики, маршрутизацию по severity, шаблон Telegram-сообщений и mute timing.

Все файлы лежат в `monitoring/grafana/provisioning/alerting/` и загружаются Grafana при старте (`apiVersion: 1`). Изменения в UI, сделанные поверх provisioned-правил, не сохраняются — правьте YAML и перезапускайте Grafana.

---

## 1. Состав конфигурации

| Файл | Что определяет |
|------|----------------|
| `alert-rules.yaml` | 8 alert rules в 3 группах, папка `Drift Guardian` |
| `contact-points.yaml` | Telegram receiver `Drift Guardian Alerts Telegram` |
| `policies.yaml` | Notification policy tree: routes по `severity` |
| `templates.yaml` | Шаблон сообщения `drift_guardian.message` |
| `mute-timings.yaml` | Time interval `working-hours-morning` |

Все правила используют Prometheus datasource с UID `cfxnbdacrd728d`. Если вы подключаете свой Prometheus, UID нужно заменить во всех блоках `datasourceUid` и во вложенных `model.datasource.uid`.

---

## 2. Группы правил

Все три группы живут в folder `Drift Guardian` с `interval: 10s` — Grafana пересчитывает условия каждые 10 секунд.

### 2.1 Window Drift (1m)

| Rule | UID | Severity | Scope |
|------|-----|----------|-------|
| `Input Data Drift – Critical` | `cfz1chowchhq8c` | critical | overall |
| `Input Data Drift – Warning` | `dfz3caqbwckqob` | warning | overall |
| `Feature Drift – Critical` | `efz3e3h2h3gn4b` | critical | list |
| `Feature Drift – Warning` | `cfz45ainayk1sc` | warning | list |

`Input Data Drift` — агрегированный сигнал по всему окну: срабатывает от `drift_overall_status`. `Feature Drift` — per-feature, `max by (feature)` даёт отдельный alert instance на каждую задрифтившую фичу, отсюда `scope: list` в labels.

### 2.2 Prediction Drift (1m)

| Rule | UID | Severity | Scope |
|------|-----|----------|-------|
| `Prediction Drift Status – Warning` | `afz3v51xspjpca` | warning | overall |
| `Prediction Drift Status – Critical` | `dfz3xmztsy8lcd` | critical | overall |
| `Prediction Drift Metrics – Warning` | `efz3u94rcw6bke` | warning | list |
| `Prediction Drift Metrics – Critical` | `cfz3xhezruigwd` | critical | list |

Prediction-правила отфильтрованы селектором `{feature="prediction"}`, а все input-правила — `{feature!="prediction"}`. Это важное разделение: `prediction` технически лежит в тех же метриках, что и обычные фичи, и без `feature!="prediction"` попадал бы в input-алерты дважды.

`Status` — правило на уровне фичи `prediction` целиком (`drift_status_feature`), `Metrics` — на уровне отдельных метрик (`max by (feature, metric)`), поэтому список в сообщении разворачивается по `metric`.

### 2.3 Adversarial Validation

| Rule | UID | Severity |
|------|-----|----------|
| `AV Drift – Warning` | `dfz3zg8qn3oxse` | warning |
| `AV Drift – Critical` | `cfz40uwii8a9sd` | critical |

Оба правила тянут четыре запроса: `av_status` (условие) плюс три информационных — `drift_av_roc_auc`, `drift_av_roc_auc_cv_min`, `drift_av_roc_auc_cv_std`. Последние три не участвуют в condition, а подставляются в `description` через `$values.<refId>.Value`.

---

## 3. Streak-логика: как гасится шум

Ключевой приём всей конфигурации. Условие правила — не просто «статус == critical», а «статус == critical **и** это состояние держится минимум 4 окна подряд».

Пример из `Input Data Drift – Critical`:

```promql
max(drift_overall_status)
  and on()
  (max(drift_overall_status_streak{status="critical"}) >= 4)
```

Левая часть возвращает числовое значение статуса (`0` / `1` / `2`), правая работает как фильтр: если streak-счётчик меньше 4, `and` не даст ни одного сэмпла и правило останется в Normal. Само значение alert берёт из левой части — поэтому threshold-условие `C` сравнивает результат с `1` (warning) или `2` (critical) через evaluator `eq`.

Для per-feature правил `and on()` заменяется на `and on(feature, type)`, для per-metric — на `and on(feature, type, metric)`, чтобы streak матчился с тем же самым инстансом, а не с любым:

```promql
max by (feature, metric) (
  drift_status{feature="prediction"}
  and on(feature, type, metric)
  (drift_status_streak{feature="prediction",status="warning"} >= 4)
)
```

**Порог streak = 4 захардкожен в PromQL-выражениях**, он не выносится в env. Чтобы изменить чувствительность, нужно править `expr` во всех восьми правилах. Реальное время до нотификации = `4 × (интервал анализа)`, а не `4 × 10s` — Grafana опрашивает Prometheus каждые 10 секунд, но streak растёт только при появлении нового окна анализа.

> Следствие: при `WINDOW_SIZE=1000` и неспешном producer'е от фактического начала дрифта до Telegram-сообщения проходит несколько минут. Это by design — иначе на коротких окнах алерты флапают.

Evaluator `eq` вместо `gt` означает строгое соответствие: правило `warning` **не** сработает при critical-статусе. Warning и critical — взаимоисключающие, а не вложенные. Плюс: нет дублирующих нотификаций при эскалации. Минус: в момент перехода warning → critical warning-алерт резолвится и в Telegram уходит «✅ RESOLVED» сразу перед «🔴 CRITICAL». Ожидаемое поведение, не баг.

---

## 4. No-data и error handling

Настройки различаются между правилами — это осознанно:

| Rule group | `noDataState` | `execErrState` |
|-----------|---------------|----------------|
| Input Data Drift (overall) | `KeepLast` | `KeepLast` |
| Feature Drift (list) | `NoData` | `Error` |
| Prediction Drift (все) | `NoData` | `Error` |
| AV Drift (оба) | `KeepLast` | `Error` |

`KeepLast` на overall-правилах защищает от ложных «всё восстановилось», когда analyzer на пару секций перестал отдавать метрики: последнее известное состояние сохраняется. `NoData` на list-правилах, наоборот, нужен — исчезновение фичи из выдачи это сигнал, а не повод держать старый алерт.

Правила с `noDataState: NoData` при отсутствии данных генерируют встроенный алерт `DatasourceNoData`. Шаблон обрабатывает его отдельной ветвью (см. §7).

---

## 5. Labels

Каждое правило проставляет набор labels, на которых потом строится маршрутизация:

| Label | Значения | Зачем |
|-------|----------|-------|
| `service` | `drift_guardian` | matcher в notification policy — обязателен |
| `severity` | `warning` / `critical` | выбор route и mute timing |
| `domain` | `input_drift` / `prediction_drift` / `adversarial_validation` | группировка, фильтры в UI |
| `owner` | `ml_monitoring` | ownership |
| `scope` | `list` (только на per-feature/per-metric) | переключает ветку шаблона |
| `method` | `statistical` | есть только у `Input Data Drift – Critical` |

Две неконсистентности в текущем YAML, на которые стоит обратить внимание:

- `Input Data Drift – Critical` имеет `owner: ml-monitoring` (дефис), все остальные — `ml_monitoring` (подчёркивание). Для группировки по owner это два разных значения.
- `method: statistical` присутствует только у одного правила из восьми.

Ни то, ни другое не влияет на маршрутизацию (в matchers участвуют только `service` и `severity`), но при добавлении новых route по `owner` или `method` приведёт к неожиданным результатам.

---

## 6. Маршрутизация и notification settings

### 6.1 Notification policy tree

`policies.yaml` определяет root-политику с двумя дочерними routes. Оба ведут в один receiver — различаются только таймингами и time interval:

```
root (receiver: Drift Guardian Alerts Telegram)
├── group_by: [grafana_folder, alertname]
├── group_wait: 30s / group_interval: 5m / repeat_interval: 4h
│
├── route: service=drift_guardian AND severity=critical
│     group_wait: 10s, group_interval: 1m, repeat_interval: 1m
│
└── route: service=drift_guardian AND severity=warning
      active_time_intervals: [working-hours-morning]
      group_wait: 10s, group_interval: 1m, repeat_interval: 1m
```

`group_by: [grafana_folder, alertname]` — все инстансы одного правила попадают в одно сообщение. Именно это делает возможным list-шаблон: `Feature Drift – Critical` с пятью задрифтившими фичами придёт одним сообщением со списком, а не пятью отдельными.

`repeat_interval: 1m` на обоих дочерних routes — очень агрессивно. Пока дрифт не ушёл, Telegram будет получать напоминание каждую минуту. Для демо это удобно (видно, что система живая), для продакшена поднимайте до 30m–4h.

### 6.2 Per-rule notification_settings

Часть правил дополнительно переопределяет маршрутизацию в собственном блоке `notification_settings`. Это Grafana-функция «simplified routing»: правило идёт напрямую в указанный receiver, **минуя дерево политик**.

Все восемь правил указывают `receiver: Drift Guardian Alerts Telegram`. Warning-правила дополнительно дублируют `active_time_intervals: [working-hours-morning]`, критические — нет.

Особый случай — `Input Data Drift – Warning`, где переопределены ещё и тайминги:

```yaml
notification_settings:
  receiver: Drift Guardian Alerts Telegram
  group_wait: 5s
  group_interval: 5s
  active_time_intervals:
    - working-hours-morning
```

`group_interval: 5s` означает почти мгновенную отправку обновлений группы. Единственное правило с такими настройками — вероятно, остаток отладки. Если шум от warning-алертов раздражает, начните с этого блока.

> Важно: пока в правиле задан `notification_settings`, routes из `policies.yaml` для него не применяются. Матчеры в `policies.yaml` останутся рабочими только если убрать `notification_settings` из правил. Сейчас конфигурация задаёт маршрутизацию дважды, и побеждает per-rule версия.

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

1. **`TELEGRAM_BOT_TOKEN`** — переменная окружения контейнера Grafana. Grafana раскрывает `$VAR` в provisioning-файлах при загрузке. Без неё contact point создастся, но отправка будет падать с 401.
2. **`chatid`** — жёстко задан. Замените на свой (для групп значение отрицательное, строкой). Получить: добавьте бота в чат и вызовите `getUpdates`.
3. **`parse_mode: HTML`** менять нельзя без переписывания шаблона — он весь построен на `<b>`, `<code>`.

`disableResolveMessage: false` включает отправку RESOLVED-сообщений. Учитывая `eq`-логику из §3, это даёт заметный трафик при эскалациях.

---

## 8. Шаблон сообщения

`templates.yaml` определяет единственный шаблон `drift_guardian.message` под именем набора `drift_guardian`. Он ветвится на три случая.

### Ветка 1 — `DatasourceNoData`

Проверка `eq (index .CommonLabels "alertname") "DatasourceNoData"`. Итерируется по `.Alerts`, для firing отдаёт «⚪️ NO DATA» с именем правила из label `rulename` и подсказкой проверить Prometheus и realtime exporter; для resolved — «✅ DATA RESTORED».

### Ветка 2 — `scope=list`

Срабатывает при `eq (index .CommonLabels "scope") "list"`. Читает три аннотации правила:

| Аннотация | Роль | Пример значения |
|-----------|------|-----------------|
| `list_label` | имя label, по которому разворачивается список | `feature` или `metric` |
| `list_title` | заголовок блока | `Critical metrics by feature` |
| `list_value` | значение рядом с элементом | `{{ $values.metric_count.Value }}` |

Шаблон рендерит `.Alerts.Firing` как bullet-список `• <code>{label}</code>: <code>{value}</code>`, ниже — `action`. Если в группе есть resolved-инстансы, отдельным блоком печатается «✅ RESOLVED» с их перечнем. Firing и resolved могут прийти в одном сообщении.

Эмодзи по severity: 🔴 critical, 🟠 warning, 🔔 иначе.

Механика `list_value` заслуживает пояснения: значение вычисляется **на каждый alert instance** отдельно, потому что `metric_count` — это `count by (feature)`. Поэтому в списке фич рядом с каждой стоит своё число задрифтивших метрик. Оба Feature-правила добавляют `or on (feature) (0 * max by (feature) (...))` — это гарантирует `0` вместо пропуска элемента, если `count` не вернул серию.

### Ветка 3 — обычный алерт

Fallback для правил без `scope: list` (все overall-правила и AV). Итерируется по `.Alerts`, для каждого печатает `alertname`, затем `summary`, `description`, `action` — каждое через `with`, так что отсутствующие аннотации просто не выводятся. Для resolved показывает фиксированный текст «The alert condition is no longer met…» и, если есть, label `feature`.

Именно сюда попадают AV-правила со своим многострочным `description`:

```
<b>Adversarial Validation Summary</b>
• ROC AUC: <code>0.847</code>
• Worst-fold AUC: <code>0.812</code>
• CV std: <code>0.019</code>
```

Форматирование через `printf "%.3f"` — три знака после запятой.

> ⚠️ В текущем `templates.yaml` эмодзи сохранены в повреждённой кодировке (`‚ö™Ô∏è`, `üî¥`, `‚úÖ`) — это mojibake от UTF-8, прочитанного как CP1252/MacRoman. В Telegram они придут набором мусорных символов. Перед использованием файл нужно перезаписать в UTF-8 с корректными ⚪️ 🔴 🟠 🔔 ✅.

---

## 9. Mute timing `working-hours-morning`

```yaml
muteTimes:
  - orgId: 1
    name: working-hours-morning
    time_intervals:
      - location: Europe/Moscow
```

Интервал объявлен, но **не заполнен**: задана только таймзона, без `times`, `weekdays`, `days_of_month` или `months`.

Пустой time interval в Grafana матчит **любое** время. Поскольку он используется как `active_time_intervals` (а не `mute_time_intervals`), warning-алерты в итоге активны круглосуточно — ограничение де-факто выключено.

Чтобы оно заработало как задумано (утренние часы рабочих дней), нужно дополнить:

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
```

С такой конфигурацией warning уходит в Telegram только с 9:00 до 13:00 МСК в будни, а critical — всегда, поскольку critical-route не ссылается на time interval.

---

## 10. Проверка и отладка

**Правила загрузились?**
Grafana → Alerting → Alert rules → folder `Drift Guardian`. Должно быть 8 правил в 3 группах. Если пусто — смотрите логи Grafana на ошибки парсинга YAML.

**Правило не переходит в Firing.** Проверьте условие вручную в Explore:

```promql
max(drift_overall_status_streak{status="critical"})
```

Если значение `< 4` — streak просто не дозрел. Если серии нет вообще — analyzer не публикует streak-метрики, проверьте `/metrics` на порту exporter'а.

**Правило Firing, но Telegram молчит.**
1. Alerting → Contact points → Test на `Drift Guardian Alerts Telegram`.
2. Проверьте, что `TELEGRAM_BOT_TOKEN` доехал в контейнер: `docker compose exec grafana env | grep TELEGRAM`.
3. Для warning-правил — вспомните §9: если вы заполнили `time_intervals`, вне окна уведомления не уйдут, хотя алерт будет Firing.

**Сообщение приходит с `{{ ... }}` или сломанной вёрсткой.** Шаблон не загрузился (проверьте `name: drift_guardian` в `templates.yaml`) либо HTML-теги не закрыты — Telegram Bot API отклоняет такие сообщения целиком с ошибкой `can't parse entities`, она видна в Grafana → Alerting → Notifications.

**Слишком много сообщений.** Три рычага по убыванию эффекта: `repeat_interval` в `policies.yaml` (сейчас `1m`), `notification_settings` у `Input Data Drift – Warning` (сейчас `group_interval: 5s`), порог streak в `expr` (сейчас `>= 4`).

---

## 11. Связанные документы

- `docs/prometheus-contract.md` — семантика `drift_status`, `drift_status_streak`, `drift_overall_status` и их labels
- `docs/metrics.md` — как считаются значения, попадающие в `drift_metric_value`
- `docs/configuration.md` — thresholds, из которых выводится `warning` / `critical`
- `docs/realtime.md` — интервал анализа, определяющий реальную задержку алертов
- `docs/troubleshooting.md` — общие проблемы запуска стека