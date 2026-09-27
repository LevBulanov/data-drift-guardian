from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ValidationError, create_model

logger = logging.getLogger(__name__)


class SchemaChecker:
    """
    Валидатор событий из Kafka и батчей (окон) против референсного DataFrame.

    Политика: null — это данные. Значения None/NaN проходят проверку
    и уезжают в анализ как есть (обрабатываются ниже по потоку).
    Чекер ловит только нарушения КОНТРАКТА:
      - отсутствующая required-колонка;
      - несовместимый dtype (дробные в int-колонке, строки в числах,
        не-bool в bool-колонке);
      - structurally битые события (не dict);
      - отсутствующий/невалидный time_column в event.

    required_cols означает "колонка обязана присутствовать",
    но НЕ "значения обязаны быть не-null".

    ВРЕМЯ: reference_df/df НЕ должны содержать никаких datetime-колонок
    вообще. Время существует только как time_column внутри отдельного
    event (Kafka-сообщение) и проверяется вручную в check_event, а не
    через pydantic-схему, построенную из reference_df.
    """

    PANDAS_TO_PY: dict[str, type] = {
        "int64": int,
        "int32": int,
        "int16": int,
        "int8": int,
        "uint64": int,
        "uint32": int,
        "uint16": int,
        "uint8": int,
        "float64": float,
        "float32": float,
        "bool": bool,
        "object": str,
        "string": str,
        "str": str,  # pandas >= 2.1 с future.infer_string, дефолт в 3.0
        # datetime64[ns] сюда сознательно не входит: в reference_df/df
        # временных колонок быть не должно вообще.
    }

    # Nullable extension dtypes запрещены ТОЛЬКО в референсе: их наличие
    # означает, что пропуски замаскированы под тип. Входные данные могут
    # иметь любые dtypes — они нормализуются или честно отбраковываются.
    UNSUPPORTED_DTYPES = (
        pd.Int8Dtype,
        pd.Int16Dtype,
        pd.Int32Dtype,
        pd.Int64Dtype,
        pd.UInt8Dtype,
        pd.UInt16Dtype,
        pd.UInt32Dtype,
        pd.UInt64Dtype,
        pd.BooleanDtype,
        pd.Float32Dtype,
        pd.Float64Dtype,
    )

    def __init__(
        self,
        reference_df: pd.DataFrame,
        required_cols: Optional[set[str]] = None,
        time_column: str = "event_time",
        raise_on_missing_required: bool = True,
        raise_on_dtype_mismatch: bool = True,
    ) -> None:
        self.reference_df = reference_df
        self.required_cols = set(required_cols) if required_cols else set()
        self.time_column = time_column
        self.raise_on_missing_required = raise_on_missing_required
        self.raise_on_dtype_mismatch = raise_on_dtype_mismatch

        self.reference_columns: set[str] = set(reference_df.columns)
        self.reference_dtypes: dict[str, Any] = dict(reference_df.dtypes)

        self.schema_model = self._build_schema_model()

    # ------------------------------------------------------------------
    # Построение схемы
    # ------------------------------------------------------------------

    def _resolve_py_type(self, dtype: Any, col: str) -> type:
        dtype_str = str(dtype)

        for unsupported in self.UNSUPPORTED_DTYPES:
            if isinstance(dtype, unsupported):
                raise ValueError(
                    f"Column '{col}' has unsupported nullable dtype "
                    f"'{dtype_str}'. Nullable extension dtypes mask real "
                    f"gaps as data — use plain numpy dtypes in the "
                    f"reference DataFrame."
                )

        # reference_df/df НЕ должны содержать datetime-колонки вообще.
        if pd.api.types.is_datetime64_any_dtype(dtype):
            raise ValueError(
                f"Column '{col}' has datetime dtype '{dtype_str}', but "
                f"reference_df/df must not contain datetime columns at "
                f"all. Only the configured time_column "
                f"('{self.time_column}') is allowed to carry time, and "
                f"only inside individual events."
            )

        # StringDtype во всех вариантах хранения (python/pyarrow):
        # str(dtype) даёт "string", "string[pyarrow]" или "str" в
        # зависимости от версии и бэкенда — надёжнее проверять типом.
        if isinstance(dtype, pd.StringDtype):
            return str

        # CategoricalDtype — сам по себе не входит в numpy/pandas dtype-map
        # по имени ('category' не является числовым/строковым именем),
        # поэтому проверяем через isinstance, а не через PANDAS_TO_PY.get.
        # Категории считаем str: если реальные категории не строковые
        # (например, числовые бины) — это отдельный, более редкий кейс,
        # который здесь не поддерживаем явно.
        if isinstance(dtype, pd.CategoricalDtype):
            return str

        py_type = self.PANDAS_TO_PY.get(dtype_str)
        if py_type is None:
            raise ValueError(
                f"Reference column '{col}' has unmapped dtype "
                f"'{dtype_str}'. Supported: {sorted(self.PANDAS_TO_PY)}"
            )
        return py_type

    def _build_schema_model(self) -> type[BaseModel]:
        """
        Строит pydantic-модель по референсу.

        Все поля Optional: null — это данные, pydantic не должен
        отбраковывать события из-за None. Nullability отслеживается
        отдельными warning'ами, а не валидацией.

        time_column в эту модель не входит — он проверяется отдельно
        в check_event, т.к. reference_df не должен содержать datetime.
        """
        fields: dict[str, tuple[Any, Any]] = {}
        for col, dtype in self.reference_dtypes.items():
            py_type = self._resolve_py_type(dtype, col)
            fields[col] = (Optional[py_type], None)
        return create_model("EventSchema", **fields)

    # ------------------------------------------------------------------
    # Проверка покрытия колонок
    # ------------------------------------------------------------------

    def _check_columns_coverage(
        self,
        present_columns: set[str],
        source_desc: str,
    ) -> None:
        """
        Отсутствие required-колонки — сломанный контракт -> ValueError.
        Отсутствие optional-колонки — warning.
        """
        missing_required = self.required_cols - present_columns
        if missing_required:
            msg = (
                f"REQUIRED columns missing in {source_desc}: "
                f"{sorted(missing_required)}"
            )
            logger.error(msg)
            if self.raise_on_missing_required:
                raise ValueError(msg)

        missing_optional = self.reference_columns - present_columns - missing_required
        if missing_optional:
            logger.warning(
                f"Missing optional columns in {source_desc}: "
                f"{sorted(missing_optional)} (excluded from drift calc)"
            )

    # ------------------------------------------------------------------
    # Совместимость dtypes
    # ------------------------------------------------------------------

    def _dtypes_compatible(
            self,
            got: Any,
            ref: Any,
            col: str,
            df: pd.DataFrame,
    ) -> bool:
        """
        Логическая совместимость dtype входа с референсным.

        Ключевые случаи, которые считаются СОВМЕСТИМЫМИ:
          - числовой вход поверх числового референса (float64 из-за
            None -> np.nan при сборке df из dict'ов — норма), при
            условии что в int-референс не приехали дробные значения;
          - object с True/None поверх bool-референса (bool + None
            не даёт float64, pandas даёт object);
          - object/string/category-вход поверх category-референса,
            при условии что значения в принципе строковые (сама природа
            категорий — конечный набор строковых меток).

        Несовместимые: дробные значения в int-референсе, строки в
        числовых колонках, не-bool значения в bool-колонке, не-строковые
        значения в category-колонке.
        """
        # category-референс: вход может быть object/string/category —
        # не коэрсим (см. _coerce_to_reference), поэтому проверяем
        # совместимость по факту, что не-null значения — строки.
        if isinstance(ref, pd.CategoricalDtype):
            if isinstance(got, pd.CategoricalDtype):
                return True
            if not (pd.api.types.is_string_dtype(got) or pd.api.types.is_object_dtype(got)):
                return False
            values = df[col].dropna()
            if values.empty:
                return True
            return values.map(lambda v: isinstance(v, str)).all()

        if pd.api.types.is_string_dtype(ref) and pd.api.types.is_string_dtype(got):
            return True

        # Числовой вход поверх числового референса.
        if pd.api.types.is_numeric_dtype(got) and pd.api.types.is_numeric_dtype(ref):
            # В int-референс не должны приезжать дробные: 25.0 — это int,
            # а 25.5 — реальное нарушение контракта.
            if pd.api.types.is_integer_dtype(ref):
                values = df[col].dropna()
                if not values.empty and not (values % 1 == 0).all():
                    return False
            return True

        # bool-референс, object-вход (смесь True/None). Проверяем, что
        # все не-null значения — настоящие bool, а не строки "True"/"false":
        # astype(bool) на строках молча даёт True — тихая подмена данных.
        if pd.api.types.is_bool_dtype(ref):
            values = df[col].dropna()
            if values.empty:
                return True
            return values.map(lambda v: isinstance(v, (bool, np.bool_))).all()

        return False

    # ------------------------------------------------------------------
    # Нормализация DataFrame
    # ------------------------------------------------------------------

    def _coerce_to_reference(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Приводит dtypes к референсным, где это безопасно.

        Снимает ложные mismatch'и вида float64-vs-int64 для колонок
        БЕЗ пропусков. Колонки с NaN остаются как есть — их пропустит
        _dtypes_compatible (null — это данные).

        bool-референс не коэрсится вовсе: astype("bool") на None даёт
        False, на строках "false" — True. Оба случая — тихая подмена
        данных, поэтому bool-вход проверяется только через
        _dtypes_compatible.

        category-референс тоже не коэрсится: astype(CategoricalDtype(...))
        молча превращает в NaN любое значение входа, не входящее в набор
        категорий референса (например, новую категорию, появившуюся в
        продакшене после того, как reference_df был зафиксирован) — это
        тихая потеря данных, а не безопасная нормализация типа.
        """
        df = df.copy()
        for col, ref_dtype in self.reference_dtypes.items():
            if col not in df.columns:
                continue
            if pd.api.types.is_dtype_equal(df[col].dtype, ref_dtype):
                continue
            if pd.api.types.is_bool_dtype(ref_dtype):
                continue
            if isinstance(ref_dtype, pd.CategoricalDtype):
                continue

            # float -> int коэрсим только если все значения целые
            # (1.0, 2.0, NaN). Реальные дробные (1.5) — нарушение
            # контракта, а не то, что можно тихо округлить astype'ом.
            if pd.api.types.is_integer_dtype(ref_dtype) and pd.api.types.is_float_dtype(df[col].dtype):
                values = df[col].dropna()
                if not values.empty and not (values % 1 == 0).all():
                    continue

            try:
                df[col] = df[col].astype(ref_dtype)
            except (ValueError, TypeError):
                pass
        return df

    def _check_nulls_df(self, df: pd.DataFrame) -> None:
        """
        Информирует о пропусках. Данные НЕ модифицирует:
        null — это данные, обрабатываются ниже по потоку.
        """
        cols = [c for c in self.reference_columns if c in df.columns]
        if not cols:
            return

        null_counts = df[cols].isna().sum()
        bad = null_counts[null_counts > 0]
        if bad.empty:
            return

        bad_required = sorted(set(bad.index) & self.required_cols)
        bad_optional = sorted(set(bad.index) - self.required_cols)
        if bad_required:
            logger.warning(
                f"NULLs in REQUIRED columns (passed through, "
                f"handled downstream): {null_counts[bad_required].to_dict()}"
            )
        if bad_optional:
            logger.warning(
                f"NULLs in optional columns: {null_counts[bad_optional].to_dict()}"
            )

    def _check_dtypes_df(self, df: pd.DataFrame) -> None:
        """
        Проверяет логическую совместимость dtypes с референсом.
        """
        for col, ref_dtype in self.reference_dtypes.items():
            if col not in df.columns:
                continue
            if self._dtypes_compatible(df[col].dtype, ref_dtype, col, df):
                continue

            msg = (
                f"Column '{col}' dtype mismatch: expected '{ref_dtype}', "
                f"got '{df[col].dtype}'"
            )
            if col in self.required_cols:
                logger.error(msg)
                if self.raise_on_dtype_mismatch:
                    raise ValueError(msg)
            else:
                logger.warning(msg)

    # ------------------------------------------------------------------
    # Публичный API: батч
    # ------------------------------------------------------------------

    def check_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Валидирует DataFrame (окно событий). Null'ы проходят как данные.

        Порядок:
          1. покрытие колонок (отсутствие required -> ValueError);
          2. коэрсинг dtypes, где безопасно (чистит float64-vs-int64
             без пропусков);
          3. warning о null'ах (данные не модифицируются);
          4. проверка логической совместимости dtypes.

        :return: DataFrame (может быть копией после коэрсинга —
                 используйте возвращаемое значение).
        :raises ValueError: отсутствует required-колонка либо
                            несовместимый dtype required-колонки
                            (при raise_on_* = True).
        """
        self._check_columns_coverage(set(df.columns), source_desc="DataFrame")

        df = self._coerce_to_reference(df)
        self._check_nulls_df(df)
        self._check_dtypes_df(df)
        return df

    # ------------------------------------------------------------------
    # Публичный API: одно событие
    # ------------------------------------------------------------------

    def check_event(
        self,
        event: Any,
    ) -> tuple[bool, Optional[dict[str, Any]], Optional[str]]:
        """
        Валидирует одно событие (dict из Kafka). Null'ы проходят как данные.

        time_column обязателен всегда (даже если не входит в
        required_cols) и должен быть настоящим datetime — это
        единственное место, где время в принципе допускается в контракте.

        :return: (is_valid, validated_dict, error_message)
        """
        # Tombstone / неудачная десериализация: event=None.
        if not isinstance(event, dict):
            msg = f"Event must be a dict, got {type(event).__name__} (tombstone?)"
            logger.warning(msg)
            return False, None, msg

        if self.time_column not in event:
            msg = f"Missing time_column '{self.time_column}' in event"
            logger.error(msg)
            return False, None, msg

        time_value = event[self.time_column]
        if not isinstance(time_value, (pd.Timestamp, datetime)):
            msg = (
                f"time_column '{self.time_column}' must be datetime, "
                f"got {type(time_value).__name__}: {time_value!r}"
            )
            logger.error(msg)
            return False, None, msg

        try:
            self._check_columns_coverage(set(event.keys()), source_desc="event")

            # Null'ы — только информирование, семантика совпадает с check_df.
            null_cols = {k for k, v in event.items() if v is None}
            null_required = sorted(null_cols & self.required_cols)
            if null_required:
                logger.warning(
                    f"Null REQUIRED fields in event (passed through): {null_required}"
                )
            null_optional = sorted(null_cols - self.required_cols)
            if null_optional:
                logger.warning(f"Null optional fields in event: {null_optional}")

            validated = self.schema_model(**event)
        except (ValidationError, ValueError, TypeError) as exc:
            logger.warning(f"Schema mismatch: {exc}")
            return False, None, str(exc)

        validated_dict = validated.model_dump()
        # time_column не входит в schema_model (reference_df без datetime),
        # поэтому pydantic его отбросит как лишнее поле — докладываем вручную.
        validated_dict[self.time_column] = time_value
        return True, validated_dict, None

