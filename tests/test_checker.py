from datetime import datetime

import pandas as pd
import pytest

from drift_guardian.data_quality_checker.checker import SchemaChecker


@pytest.fixture
def reference_df():
    return pd.DataFrame(
        {
            "user_id": pd.Series([1, 2], dtype="int64"),
            "score": pd.Series([1.5, 2.5], dtype="float64"),
            "name": pd.Series(["Alice", "Bob"], dtype="object"),
            "is_active": pd.Series([True, False], dtype="bool"),
        }
    )


@pytest.fixture
def checker(reference_df):
    return SchemaChecker(
        reference_df=reference_df,
        required_cols={"user_id", "score"},
    )


def test_check_event_valid_event(checker):
    event_time = datetime(2026, 9, 19, 8, 0, 0)

    event = {
        "user_id": 1,
        "score": 10.5,
        "name": "Alice",
        "is_active": True,
        "event_time": event_time,
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is True
    assert error is None
    assert validated == event


def test_check_event_missing_optional_column_is_valid(checker):
    event = {
        "user_id": 1,
        "score": 10.5,
        "event_time": datetime(2026, 9, 19, 8, 0, 0),
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is True
    assert error is None
    assert validated["user_id"] == 1
    assert validated["score"] == 10.5
    assert validated["event_time"] == datetime(2026, 9, 19, 8, 0, 0)

    # optional-поля есть в model_dump, но заполнены None
    assert validated["name"] is None
    assert validated["is_active"] is None


def test_check_event_missing_required_column_is_invalid(checker):
    event = {
        "score": 10.5,
        "name": "Alice",
        "is_active": True,
        "event_time": datetime(2026, 9, 19, 8, 0, 0),
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is False
    assert validated is None
    assert "REQUIRED columns missing" in error
    assert "user_id" in error


def test_check_event_invalid_type_is_invalid(checker):
    event = {
        "user_id": "not-an-int",
        "score": 10.5,
        "name": "Alice",
        "is_active": True,
        "event_time": datetime(2026, 9, 19, 8, 0, 0),
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is False
    assert validated is None
    assert error is not None


def test_check_event_extra_column_is_ignored_by_pydantic_model(checker):
    event = {
        "user_id": 1,
        "score": 10.5,
        "name": "Alice",
        "is_active": True,
        "event_time": datetime(2026, 9, 19, 8, 0, 0),
        "unknown_field": "extra",
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is True
    assert error is None
    assert "unknown_field" not in validated


def test_check_event_missing_time_column_is_invalid(checker):
    event = {
        "user_id": 1,
        "score": 10.5,
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is False
    assert validated is None
    assert "event_time" in error


def test_check_event_non_datetime_time_column_is_invalid(checker):
    event = {
        "user_id": 1,
        "score": 10.5,
        "event_time": "2026-09-19 08:00:00",
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is False
    assert validated is None
    assert "event_time" in error


def test_check_df_valid_dataframe_passes(checker, reference_df):
    checker.check_df(reference_df)


def test_check_df_missing_optional_column_passes(checker, reference_df):
    df = reference_df.drop(columns=["name"])

    checker.check_df(df)


def test_check_df_missing_required_column_raises(checker, reference_df):
    df = reference_df.drop(columns=["user_id"])

    with pytest.raises(ValueError, match="REQUIRED columns missing"):
        checker.check_df(df)


def test_check_df_required_dtype_mismatch_raises(checker, reference_df):
    df = reference_df.copy()
    df["user_id"] = df["user_id"].astype("float64")
    df["user_id"] = [1.5, 2.5]  # реальные дробные значения, не 1.0/2.0

    with pytest.raises(ValueError, match="dtype mismatch"):
        checker.check_df(df)


def test_check_df_optional_dtype_mismatch_does_not_raise(checker, reference_df):
    df = reference_df.copy()
    df["name"] = pd.Series([1, 2], dtype="int64")

    checker.check_df(df)


def test_check_df_required_dtype_mismatch_does_not_raise_when_disabled(reference_df):
    checker = SchemaChecker(
        reference_df=reference_df,
        required_cols={"user_id"},
        raise_on_missing_required=False,
        raise_on_dtype_mismatch=False,
    )

    df = reference_df.copy()
    df["user_id"] = [1.5, 2.5]

    checker.check_df(df)


def test_check_df_missing_required_does_not_raise_when_disabled(reference_df):
    checker = SchemaChecker(
        reference_df=reference_df,
        required_cols={"user_id"},
        raise_on_missing_required=False,
    )

    df = reference_df.drop(columns=["user_id"])

    checker.check_df(df)


def test_init_raises_for_nullable_int_dtype():
    reference_df = pd.DataFrame(
        {
            "user_id": pd.Series([1, 2], dtype="Int64"),
        }
    )

    with pytest.raises(ValueError, match="unsupported nullable dtype"):
        SchemaChecker(reference_df=reference_df)


def test_init_raises_for_nullable_boolean_dtype():
    reference_df = pd.DataFrame(
        {
            "flag": pd.Series([True, False], dtype="boolean"),
        }
    )

    with pytest.raises(ValueError, match="unsupported nullable dtype"):
        SchemaChecker(reference_df=reference_df)


def test_init_raises_for_any_datetime_column_in_reference():
    reference_df = pd.DataFrame(
        {
            "created_at": pd.to_datetime(
                ["2026-09-19 08:00:00", "2026-09-19 08:01:00"]
            ),
        }
    )

    with pytest.raises(ValueError, match="must not contain datetime columns"):
        SchemaChecker(reference_df=reference_df)


def test_init_raises_for_datetime_column_matching_time_column_name():
    # Даже если имя колонки совпадает с time_column — это всё равно
    # datetime-колонка в reference_df, а значит нарушение контракта.
    reference_df = pd.DataFrame(
        {
            "event_time": pd.to_datetime(
                ["2026-09-19 08:00:00", "2026-09-19 08:01:00"]
            ),
        }
    )

    with pytest.raises(ValueError, match="must not contain datetime columns"):
        SchemaChecker(reference_df=reference_df, time_column="event_time")


def test_init_raises_for_unmapped_dtype():
    reference_df = pd.DataFrame(
        {
            "period_col": pd.period_range("2026-01", periods=2, freq="M"),
        }
    )

    with pytest.raises(ValueError, match="unmapped dtype"):
        SchemaChecker(reference_df=reference_df)


def test_string_extension_dtype_is_treated_as_str():
    reference_df = pd.DataFrame(
        {
            "country": pd.Series(["GB", "DE"], dtype="string"),
        }
    )

    checker = SchemaChecker(reference_df=reference_df)
    event = {
        "country": "GB",
        "event_time": datetime(2026, 9, 19, 8, 0, 0),
    }
    is_valid, validated, error = checker.check_event(event)

    assert is_valid is True
    assert error is None
    assert validated["country"] == "GB"


def test_category_dtype_is_treated_as_str():
    reference_df = pd.DataFrame(
        {
            "segment": pd.Series(["a", "b"], dtype="category"),
        }
    )

    checker = SchemaChecker(reference_df=reference_df)

    event = {
        "segment": "a",
        "event_time": datetime(2026, 9, 19, 8, 0, 0),
    }
    is_valid, validated, error = checker.check_event(event)

    assert is_valid is True
    assert error is None
    assert validated["segment"] == "a"


def test_check_event_missing_time_column_is_invalid(checker):
    event = {
        "user_id": 1,
        "score": 10.5,
        "name": "Alice",
        "is_active": True,
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is False
    assert validated is None
    assert "event_time" in error


def test_check_event_non_datetime_time_column_is_invalid(checker):
    event = {
        "user_id": 1,
        "score": 10.5,
        "name": "Alice",
        "is_active": True,
        "event_time": "2026-09-19 08:00:00",
    }

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is False
    assert validated is None
    assert "event_time" in error


def test_check_event_time_column_survives_even_when_not_in_reference():
    # time_column не входит в reference_df/schema_model, поэтому
    # pydantic отбросит его как лишнее поле — check_event должен
    # вернуть его в validated_dict вручную.
    reference_df = pd.DataFrame(
        {
            "user_id": pd.Series([1, 2], dtype="int64"),
        }
    )
    checker = SchemaChecker(reference_df=reference_df)

    event_time = datetime(2026, 9, 19, 8, 0, 0)
    event = {"user_id": 1, "event_time": event_time}

    is_valid, validated, error = checker.check_event(event)

    assert is_valid is True
    assert error is None
    assert validated["event_time"] == event_time


def test_check_df_category_reference_accepts_object_input():
    reference_df = pd.DataFrame(
        {
            "segment": pd.Series(["a", "b"], dtype="category"),
        }
    )
    checker = SchemaChecker(reference_df=reference_df)

    df = pd.DataFrame({"segment": ["a", "c", None]})

    result = checker.check_df(df)

    values = result["segment"].tolist()
    assert values[0] == "a"
    assert values[1] == "c"
    assert pd.isna(values[2])


def test_check_df_category_reference_rejects_non_string_input():
    reference_df = pd.DataFrame(
        {
            "segment": pd.Series(["a", "b"], dtype="category"),
        }
    )
    checker = SchemaChecker(reference_df=reference_df, required_cols={"segment"})

    df = pd.DataFrame({"segment": [1, 2, 3]})

    with pytest.raises(ValueError, match="dtype mismatch"):
        checker.check_df(df)


def test_check_df_reference_without_nan_current_with_nan_in_required_passes():
    reference_df = pd.DataFrame(
        {
            "user_id": pd.Series([1, 2], dtype="int64"),
            "score": pd.Series([1.5, 2.5], dtype="float64"),
        }
    )
    checker = SchemaChecker(
        reference_df=reference_df,
        required_cols={"user_id", "score"},
    )

    # user_id придёт как float64 с NaN — типичный артефакт сборки
    # DataFrame из dict'ов с None.
    df = pd.DataFrame(
        {
            "user_id": [1.0, None, 3.0],
            "score": [1.1, None, 3.3],
        }
    )

    result = checker.check_df(df)

    # NaN не бросает ValueError — только warning, данные проходят как есть.
    assert result["user_id"].isna().sum() == 1
    assert result["score"].isna().sum() == 1


def test_check_df_reference_without_nan_current_with_nan_in_optional_passes():
    reference_df = pd.DataFrame(
        {
            "user_id": pd.Series([1, 2], dtype="int64"),
            "name": pd.Series(["Alice", "Bob"], dtype="object"),
        }
    )
    checker = SchemaChecker(
        reference_df=reference_df,
        required_cols={"user_id"},  # "name" — optional
    )

    df = pd.DataFrame(
        {
            "user_id": [1, 2, 3],
            "name": ["Alice", None, "Carol"],
        }
    )

    result = checker.check_df(df)

    assert result["name"].isna().sum() == 1
