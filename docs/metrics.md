# Drift metrics и thresholds

Документ описывает поддерживаемые drift-метрики, их семантику и направление, механику global/local thresholds и мониторинг prediction drift.

Prometheus series — в [docs/prometheus-contract.md](prometheus-contract.md), генерация конфига — в [docs/configuration.md](configuration.md).

---

## Поддерживаемые feature metrics

| Metric | Numeric | Categorical | Направление |
|---|:---:|:---:|---|
| `missing_rate` | ✓ | ✓ | больше = хуже |
| `psi` | ✓ | ✓ | больше = хуже |
| `js_divergence` | ✓ | ✓ | больше = хуже |
| `wasserstein_distance` | ✓ | — | больше = хуже |
| `kstest` | ✓ | — | больше = хуже |
| `unseen_category_rate` | — | ✓ | больше = хуже |
| `cardinality_ratio` | — | ✓ | больше = хуже |
| `chi2` | — | ✓ | **меньше = хуже** |
| `cramer_v` | — | ✓ | больше = хуже |
| `category_churn` | — | ✓ | больше = хуже |

Тип feature (`numeric` / `categorical`) определяет доступный набор метрик. Попытка задать `kstest` для categorical feature или `chi2` для numeric не имеет смысла — генератор конфига подбирает набор метрик по типу автоматически.

---

## Семантика метрик

### Общие для обоих типов

**`missing_rate`** — доля пропусков в current window. Не является drift-метрикой в строгом смысле: это data quality signal. Резкий рост часто означает поломку upstream-пайплайна, а не изменение распределения.

**`psi` (Population Stability Index)** — классическая метрика стабильности из кредитного скоринга. Сравнивает распределения по бинам: для numeric — по квантильным границам reference, для categorical — по категориям.

Общепринятые ориентиры в индустрии:

```text
< 0.10   изменений практически нет
0.10–0.25 умеренный сдвиг, стоит посмотреть
> 0.25   существенный сдвиг
```

Checked-in конфиг использует именно эти границы как global thresholds. PSI чувствителен к выбору биннинга и к малым выборкам — на окнах в сотни строк значения шумные.

**`js_divergence` (Jensen-Shannon divergence)** — симметричная ограниченная мера расхождения распределений. В отличие от KL-divergence симметрична и не уходит в бесконечность при нулевых вероятностях, что делает её устойчивее на разреженных категориях.

Значение ограничено сверху (при использовании log base 2 — единицей), поэтому thresholds переносимы между features без привязки к масштабу.

### Только numeric

**`wasserstein_distance`** (earth mover's distance) — «сколько работы нужно, чтобы превратить одно распределение в другое».

Ключевая особенность: **зависит от масштаба feature**. Для `income` в рублях расстояние 5000 может быть нормой, для `age` в годах — катастрофой. Это означает, что global threshold для `wasserstein_distance` практически бесполезен — метрика требует feature-level override или автокалибровки.

**`kstest`** — Kolmogorov-Smirnov statistic. Важно: экспортируется **D-statistic, а не p-value**.

Это сознательный выбор. D-statistic — размер эффекта, максимальное расхождение эмпирических CDF, ограничен и не зависит от размера выборки. P-value же на больших окнах становится бесполезным: при `WINDOW_SIZE=1000` статистически значимым окажется любое микроскопическое отличие.

Поскольку экспортируется D-statistic, направление обычное: больше = хуже.

### Только categorical

**`unseen_category_rate`** — доля строк в current window с категориями, отсутствующими в reference. Прямой сигнал о том, что модель получает на вход значения, которых не видела при обучении.

**`cardinality_ratio`** — изменение числа уникальных категорий относительно reference. Ловит случаи, когда состав категорий формально тот же, но их количество изменилось — например, схлопнулся справочник или, наоборот, размножился из-за смены формата идентификаторов.

**`chi2`** — chi-square test **p-value**. Единственная метрика с обратным направлением, подробно разобрана ниже.

**`cramer_v` (Cramér's V)** — нормированная мера силы связи, производная от chi-square, ограничена. В отличие от `chi2` не зависит от размера выборки, поэтому thresholds для неё стабильнее.

Практический вывод: `cramer_v` обычно надёжнее `chi2` для realtime-мониторинга при больших окнах.

**`category_churn`** — изменение состава категорий: появление и исчезновение значений. Дополняет `unseen_category_rate`, который смотрит только на новые категории, тогда как churn учитывает и выпавшие.

---

## `chi2` и обратное направление

`chi2` возвращает p-value, поэтому **меньше = хуже**: малое p-value означает, что распределения статистически различаются.

Для этой метрики направление thresholds инвертировано:

```text
обычные метрики:  warning < critical
chi2:             warning > critical
```

```yaml
chi2:
  warning: 0.05
  critical: 0.01
```

Читается так: «p-value упало ниже 0.05 → warning; упало ниже 0.01 → critical».

Практические замечания:

**В HTML report** значение подписано как `χ² p-value`, чтобы `0` или очень малое число не воспринималось как хороший результат по шкале «больше = хуже».

**Обратный порядок в YAML нельзя испортить.** Если задать `warning: 0.01, critical: 0.05`, конфиг упадет на валидации.
**На больших окнах `chi2` быстро вырождается.** При `WINDOW_SIZE=1000` даже незначительное отличие в распределении категорий даёт p-value близкое к нулю. Если `chi2` постоянно в critical, а `cramer_v` и `psi` спокойны — скорее всего это артефакт размера выборки, а не реальный drift. По этой причине один `chi2` не может поставить все окно в `critical`. 

---

## Global и local thresholds

### Global

Применяются ко всем features, у которых нет собственного override:

```yaml
thresholds:
  psi:
    warning: 0.10
    critical: 0.25
  js_divergence:
    warning: 0.10
    critical: 0.20
  missing_rate:
    warning: 0.05
    critical: 0.15
```

### Feature-level override

```yaml
features:
  age:
    type: numeric
    metrics: [missing_rate, psi]
    thresholds:
      psi:
        warning: 0.05
        critical: 0.12
```

### Приоритет

```text
feature local threshold > global threshold
```

Override работает **на уровне отдельной метрики**, а не всего блока. Если у feature переопределён только `psi`, то `missing_rate` продолжит использовать global threshold.

### Когда нужен override

Три типичных случая:

1. **Масштабозависимые метрики.** `wasserstein_distance` практически требует per-feature настройки — единый global threshold не имеет смысла.
2. **Бизнес-критичность.** Для feature, от которой сильно зависит модель, имеет смысл ужесточить порог.
3. **Изначально нестабильная feature.** Признак с высокой естественной волатильностью будет постоянно шуметь на global threshold — порог нужно ослабить, иначе alert fatigue.

### Resolved thresholds в Prometheus

Парсер конфига формирует resolved thresholds — итоговые значения с учётом приоритета. Exporter публикует оба уровня:

```text
drift_threshold{metric,level}                          # global
drift_resolved_threshold{feature,type,metric,level}    # фактически применённый
```

Наличие второй series позволяет строить в Grafana панели, где линия порога совпадает с той, по которой реально считался статус конкретной feature. Для диагностики конфига это же полезно: сравнив `drift_threshold` и `drift_resolved_threshold`, видно, какие override фактически подхватились.

---

## Автокалибровка thresholds

Генератор конфига по умолчанию калибрует thresholds bootstrap-окнами reference dataset:

```text
auto_thresholds.enabled = True
```

Идея: нарезать reference на псевдо-окна, посчитать распределение метрики в ситуации, когда drift'а заведомо нет, и поставить порог выше естественного шума.

Это особенно важно для масштабозависимых метрик — калибровка решает проблему, для которой иначе пришлось бы вручную подбирать override по каждой feature.

Детали реализации и опции — [docs/configuration.md](configuration.md).

---

## Status codes и агрегация

Каждая пара feature × metric получает статус:

```text
-1 = insufficient_data / not configured
 0 = ok
 1 = warning
 2 = critical
```

Уровни агрегации:

```text
drift_status{feature,type,metric}   # метрика конкретной feature
drift_status_feature{feature,type}  # худший статус среди метрик feature
drift_overall_status                # худший статус по всем features
drift_active_alerts                 # число input features в critical
```

`drift_status_feature` и `drift_overall_status` работают по принципу worst-case: одна critical-метрика делает critical всю feature, одна critical-feature — весь отчёт.

Практическое следствие: `drift_overall_status = 2` сам по себе не говорит, что данные развалились. Он может означать единственную шумную метрику на единственной feature. Для оценки масштаба смотрите `drift_active_alerts`.

---

## Prediction drift

Мониторинг предсказаний настраивается отдельным блоком:

```yaml
prediction_metrics:
  enabled: true
  score_column: prediction_score
  type: numeric
  metrics:
    - psi
```

По умолчанию генератор конфига оставляет `prediction_enabled = False`— блок нужно включить явно.

### Почему отдельно от input features

Prediction **не учитывается как обычная input feature** в `drift_active_alerts`: этот счётчик считает только input features в critical. Prediction status экспортируется отдельной series с label `feature="prediction"`.

Разделение содержательное. Input drift и prediction drift — разные события с разными выводами:

| Что видно | Вероятная причина |
|---|---|
| input drift есть, prediction drift нет | модель устойчива к сдвигу; наблюдаем, но не срочно |
| input drift нет, prediction drift есть | проблема не в данных — версия модели, feature engineering, баг в пайплайне |
| оба | классический сценарий деградации |
| prediction drift без input drift и без релиза | стоит проверить целостность самого inference-пути |

Если смешать prediction с input features в одном счётчике, эта диагностическая рамка теряется.

### Исключение из AV

Если prediction column настроена, realtime AV исключает её из feature set. Причина: классификатор почти наверняка вытащил бы `prediction_score` в топ drivers (предсказания меняются вместе со входом), и ranking перестал бы говорить что-либо об input features.

В offline API это делается явным аргументом:

```python
av_report = analyzer.run_av(current_df, prediction_col="prediction_score")
```

Подробнее — [docs/adversarial-validation.md](adversarial-validation.md).

### В HTML report

Prediction drift выводится отдельным блоком со status-card и карточками prediction metrics, не смешиваясь с таблицами input features. См. [docs/offline.md](offline.md).

---

## Выбор набора метрик

Практические соображения, если настраиваете конфиг вручную.

**Не включайте всё сразу.** Десять метрик на feature дают десять потенциальных источников шума. Для большинства случаев достаточно 2–3 на feature.

**Разумный минимум:**

```yaml
# numeric
metrics: [missing_rate, psi]

# categorical
metrics: [missing_rate, psi, unseen_category_rate]
```

**Что добавлять по необходимости:**

| Задача | Метрика |
|---|---|
| поймать сдвиг формы распределения, который PSI сглаживает | `kstest` |
| отследить сдвиг в исходных единицах feature | `wasserstein_distance` + override |
| контроль справочника категорий | `cardinality_ratio`, `category_churn` |
| устойчивая альтернатива `chi2` на больших окнах | `cramer_v` |

**Метрики коррелируют.** `psi` и `js_divergence` часто срабатывают вместе — обе основаны на бинированных распределениях. Включать обе стоит, если нужна перекрёстная проверка, но для alerting это скорее дублирование.

---

## Связанные документы

- [docs/configuration.md](configuration.md) — генерация конфига, автокалибровка, defaults
- [docs/prometheus-contract.md](prometheus-contract.md) — series, labels, status codes
- [docs/adversarial-validation.md](adversarial-validation.md) — multivariate drift
- [docs/realtime.md](realtime.md) — окна, stream health thresholds
- [docs/troubleshooting.md](troubleshooting.md) — «PSI меняется, но остаётся OK» и другие кейсы