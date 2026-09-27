# Reference dataset

Analyzer сравнивает каждое analysis window с фиксированной baseline-выборкой — reference dataset. Документ описывает, как её подготовить, какие требования к ней предъявляются и как загрузить данные по URL.

Генерация конфига под конкретный датасет — в [docs/configuration.md](configuration.md).

---

## Что это и зачем

Reference dataset задаёт «норму». Все drift-метрики (PSI, KS, JS divergence) считаются как расхождение между current window и этим baseline; adversarial validation обучает классификатор различать reference и current.

Из этого следует главное практическое требование: **reference должен представлять то состояние данных, которое вы считаете правильным** — обычно это обучающая выборка модели или продакшен-данные периода, когда модель работала корректно.

Если в reference попали уже искажённые данные, drift относительно них ничего полезного не покажет.

---

## Локальный файл

Analyzer читает CSV по пути из переменной окружения:

```env
REFERENCE_DATA_PATH=/app/data/reference.csv
```

Это значение Compose использует по умолчанию. На хосте файлу соответствует:

```text
data/reference.csv
```

Папка `data/` находится в `.gitignore`, поэтому reference-файла в свежем клоне репозитория **нет** — его нужно подготовить самостоятельно.

> Файл должен существовать **до запуска analyzer**. При его отсутствии контейнер не стартует с ошибкой `reference dataset not found`.

### Вариант 1: свой CSV

```bash
mkdir -p data
cp /path/to/your.csv data/reference.csv
```

<details>
<summary>PowerShell</summary>

```powershell
New-Item -ItemType Directory -Force -Path .\data | Out-Null
Copy-Item C:\path\to\your.csv .\data\reference.csv
```

</details>

После этого сгенерируйте конфиг под свои колонки — checked-in `config/config.yaml` рассчитан на `age`, `income`, `country` и `prediction_score` [[1]](file://README.md):

```bash
uv run python tools/build_config.py
```

### Вариант 2: другой путь

Если держать данные в `data/reference.csv` неудобно, переопределите переменную в `.env`:

```env
REFERENCE_DATA_PATH=/app/data/my_baseline.csv
```

Путь указывается **внутри контейнера**, поэтому файл должен попадать туда через тот же volume-маппинг, что и `data/`.

---

## Загрузка по URL

Для demo-сценариев есть скрипт, забирающий датасет по ссылке:

```bash
uv run python tools/get_demo_data.py
```

Скрипт читает `DATASET_URL` из локального `.env`, при необходимости создаёт папку `data/` и сохраняет результат как `data/reference.csv` [[1]](file://README.md).

### Прямая ссылка

```env
DATASET_URL=https://example.org/reference.csv
```

### Google Drive

Поддерживается стандартный share-формат [[1]](file://README.md):

```env
DATASET_URL=https://drive.google.com/file/d/<FILE_ID>/view
```

Файл должен быть доступен по ссылке без авторизации — скрипт не выполняет OAuth-вход.

### Архивы

Скрипт распознаёт ZIP и GZIP и извлекает данные в `data/reference.csv` [[1]](file://README.md). Отдельного флага для этого не нужно — тип определяется автоматически.

### Проверка результата

```bash
ls -lh data/reference.csv
head -n 3 data/reference.csv
```

<details>
<summary>PowerShell</summary>

```powershell
Get-Item .\data\reference.csv | Select-Object Name, Length
Get-Content .\data\reference.csv -TotalCount 3
```

</details>

---

## Требования к содержимому

Жёсткой схемы у analyzer нет — набор колонок определяется вашим `config/config.yaml`. Но есть зависимости, которые стоит учитывать.

### Колонки должны совпадать с конфигом

Analyzer считает метрики только для features, перечисленных в конфиге. Колонка, которой нет в reference, не будет мониториться, даже если она присутствует в realtime-событиях.

Обратная ситуация тоже важна: **demo-producer применяет drift-правила только к features, присутствующим в reference dataset** [[1]](file://README.md). Правило для `feature_1` при датасете с колонкой `age` будет молча пропущено. См. [docs/demo-producer.md](demo-producer.md).

### Prediction column

Если включён prediction monitoring, колонка из `prediction_metrics.score_column` должна быть в reference:

```yaml
prediction_metrics:
  enabled: true
  score_column: prediction_score
```

Без неё не с чем сравнивать распределение предсказаний. См. [docs/metrics.md](metrics.md).

### Размер выборки

Два ограничения снизу:

- **для AV** — reference должен содержать достаточно строк для `n_splits` cross-validation. При недостатке AV не запустится, и `drift_av_available` останется `0` [[1]](file://README.md);
- **для калибровки thresholds** — генератор конфига по умолчанию нарезает reference на bootstrap-окна [[1]](file://README.md). На маленьком датасете калибровка даст шумные пороги.

Сверху ограничение есть только у AV: параметр `max_samples` (по умолчанию `50000`) подрезает выборку перед обучением классификатора. На сами drift-метрики это не влияет — они считаются по всему reference.

### Categorical features

Состав категорий в reference определяет, что будет считаться «новым значением» для `unseen_category_rate` и `category_churn`. Если reference не покрывает редкие категории, они будут выглядеть как drift при первом появлении в потоке.

---

## Reference и realtime-события

Reference читается из CSV один раз при старте analyzer, current window собирается из Kafka-событий. Формат при этом разный — CSV против JSON — но **имена и типы полей должны соответствовать** друг другу и конфигу.

Несовпадение имени поля в Kafka-событии проявится не как ошибка, а как отсутствующая метрика: analyzer просто не найдёт колонку в окне.

Подробнее про валидацию событий — [docs/realtime.md](realtime.md).

---

## Обновление reference

Встроенного механизма ротации baseline нет: analyzer читает файл при старте. Чтобы сменить reference:

```bash
cp /path/to/new_baseline.csv data/reference.csv
docker compose --profile realtime --profile local-kafka restart analyzer
```

Если у нового датасета изменился набор колонок, перед рестартом перегенерируйте конфиг:

```bash
uv run python tools/build_config.py
docker compose --profile realtime --profile local-kafka restart analyzer
```

> Смена reference сбрасывает сопоставимость истории: метрики до и после относятся к разным baseline. В Grafana это выглядит как разрыв тренда, поэтому момент обновления стоит фиксировать.

---

## Диагностика

**`reference dataset not found` при старте analyzer.**

```bash
ls -l data/reference.csv
```

<details>
<summary>PowerShell</summary>

```powershell
Test-Path .\data\reference.csv
```

</details>

Если файла нет — положите свой CSV или запустите `tools/get_demo_data.py` [[1]](file://README.md). Если файл есть, но analyzer его не видит, проверьте `REFERENCE_DATA_PATH` и volume-маппинг `data/` в Compose.

**Метрики для части features не появляются.** Имя колонки в reference, в конфиге и в Kafka-событиях должно совпадать до символа. Проверьте заголовок CSV:

```bash
head -n 1 data/reference.csv
```

**PSI меняется, но статус остаётся `OK`.** Ожидаемо, если current window статистически близок к reference [[1]](file://README.md). Для демонстрации переходов `OK → Warning → Critical` настройте drift-сценарий producer'а под реальные колонки датасета — см. [docs/demo-producer.md](demo-producer.md).

**`drift_av_available = 0` не меняется.** Среди причин — недостаточный размер reference для `n_splits` [[1]](file://README.md). Полный чеклист — [docs/adversarial-validation.md](adversarial-validation.md).

Остальные сценарии — [docs/troubleshooting.md](troubleshooting.md).

---

## Связанные документы

- [docs/configuration.md](configuration.md) — `build_config.py`, автокалибровка thresholds
- [docs/metrics.md](metrics.md) — drift-метрики и prediction drift
- [docs/demo-producer.md](demo-producer.md) — drift-сценарии для локального producer
- [docs/adversarial-validation.md](adversarial-validation.md) — требования AV к размеру выборок
- [docs/realtime.md](realtime.md) — валидация событий, окна