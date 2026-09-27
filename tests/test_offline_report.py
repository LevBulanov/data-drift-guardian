import json
import sys

import pandas as pd
import pytest

from drift_guardian.reporting import offline_report


CSS = ".summary-card { display: flex; }"


def metric(value, warning, critical, status):
    return {
        "value": value,
        "warning": warning,
        "critical": critical,
        "status": status,
    }


def complete_report():
    return {
        "timestamp": "2026-09-27T00:00:00Z",
        "window_size": 100,
        "overall_status": "critical",
        "active_alerts": 1,
        "features": {
            "age": {
                "type": "numeric",
                "status": "critical",
                "metrics": {
                    "psi": metric(0.31, 0.1, 0.25, "critical"),
                    "kstest": metric(0.08, 0.02, 0.05, "critical"),
                },
            },
            "country": {
                "type": "categorical",
                "status": "warning",
                "metrics": {
                    "psi": metric(0.15, 0.1, 0.25, "warning"),
                    "unseen_category_rate": metric(0.01, 0.005, 0.02, "warning"),
                },
            },
        },
        "prediction": {
            "type": "numeric",
            "status": "warning",
            "metrics": {
                "psi": metric(0.14, 0.1, 0.25, "warning"),
                "kstest": metric(0.03, 0.02, 0.05, "warning"),
            },
        },
    }


def test_render_report_contains_numeric_categorical_prediction_and_av_sections():
    importance = pd.DataFrame(
        {
            "rank": range(1, 13),
            "feature": [f"feature_{index}" for index in range(1, 13)],
            "importance": [1 / index for index in range(1, 13)],
            "importance_std": [0.01] * 12,
        }
    )

    html = offline_report.render_report_html(
        complete_report(),
        CSS,
        dataset_name="Customer <Churn>",
        av_report=(0.76, importance),
    )

    assert "Customer &lt;Churn&gt;" in html
    assert "numeric features" in html
    assert "categorical features" in html
    assert "Prediction Drift" in html
    assert "Adversarial Validation" in html
    assert "ROC AUC" in html
    assert "Importance std." in html
    assert "feature_10" in html
    assert "feature_11" not in html
    assert "metric-thresholds-table" in html
    assert "metric-thresholds-card" in html


def test_render_report_uses_metadata_dataset_name_and_escapes_report_content():
    report = complete_report()
    report["metadata"] = {"dataset_name": "Loans <production>"}
    report["features"]["unsafe <feature>"] = {
        "type": "custom <type>",
        "status": "unexpected",
        "metrics": {"custom <metric>": "value <script>"},
    }

    html = offline_report.render_report_html(report, CSS)

    assert "Loans &lt;production&gt;" in html
    assert "unsafe &lt;feature&gt;" in html
    assert "custom &lt;type&gt; features" in html
    assert "Custom &lt;metric&gt;" in html
    assert "value &lt;script&gt;" in html
    assert "status-dot-unknown" in html


def test_render_report_without_prediction_and_av_omits_both_sections():
    report = complete_report()
    report.pop("prediction")

    html = offline_report.render_report_html(report, CSS)

    assert "Prediction Drift" not in html
    assert "Adversarial Validation" not in html
    assert "numeric features" in html
    assert "categorical features" in html


def test_render_report_tolerates_missing_empty_and_legacy_metric_values():
    report = {
        "overall_status": "ok",
        "features": {
            "with_mapping": {
                "type": "numeric",
                "status": "ok",
                "metrics": {"psi": metric(0.01, 0.1, 0.25, "ok")},
            },
            "with_missing_metric": {
                "type": "numeric",
                "status": "unknown",
                "metrics": {"kstest": None},
            },
            "with_legacy_scalar": {
                "type": "numeric",
                "status": "unknown",
                "metrics": {"psi": 0.02},
            },
            "with_no_metrics": {
                "type": "categorical",
                "status": "unknown",
                "metrics": {},
            },
        },
        "prediction": {"type": "numeric", "status": "unknown", "metrics": {}},
    }

    html = offline_report.render_report_html(report, CSS)

    assert "with_mapping" in html
    assert "with_missing_metric" in html
    assert "with_legacy_scalar" in html
    assert "with_no_metrics" in html
    assert "Prediction Drift" in html
    assert "0.020" in html
    assert "—" in html


def test_render_report_without_features_shows_empty_state_and_zero_counts():
    html = offline_report.render_report_html(
        {"overall_status": None, "features": {}},
        CSS,
    )

    assert "UNKNOWN" in html
    assert "No evaluated features" in html
    assert "of 0 evaluated" in html


@pytest.mark.parametrize(
    ("av_report", "expected_status", "expected_value"),
    [
        ((0.59, pd.DataFrame()), "status-passed", "0.590"),
        ((0.60, pd.DataFrame()), "status-warning", "0.600"),
        ((0.75, pd.DataFrame()), "status-critical", "0.750"),
        ((float("nan"), pd.DataFrame()), "status-unknown", "nan"),
    ],
)
def test_render_report_uses_av_boundary_statuses(
    av_report,
    expected_status,
    expected_value,
):
    html = offline_report.render_report_html(
        complete_report(),
        CSS,
        av_report=av_report,
    )

    assert expected_status in html
    assert expected_value in html


@pytest.mark.parametrize("av_report", [0.7, (0.7,), (0.7, object(), "extra")])
def test_render_report_rejects_malformed_av_report(av_report):
    with pytest.raises(TypeError, match="av_report must be"):
        offline_report.render_report_html(
            complete_report(),
            CSS,
            av_report=av_report,
        )


@pytest.mark.parametrize(
    "importance",
    [
        None,
        pd.DataFrame(),
        pd.DataFrame({"feature": ["age"], "importance": [1.0]}),
    ],
)
def test_render_report_handles_unavailable_feature_importance(importance):
    html = offline_report.render_report_html(
        complete_report(),
        CSS,
        av_report=(0.7, importance),
    )

    assert "Feature importance is unavailable" in html


def test_generate_html_report_reads_json_and_writes_standalone_html(tmp_path):
    report_path = tmp_path / "report.json"
    css_path = tmp_path / "report.css"
    output_path = tmp_path / "report.html"
    report_path.write_text(json.dumps(complete_report()), encoding="utf-8")
    css_path.write_text(CSS, encoding="utf-8")

    result = offline_report.generate_html_report(
        report_path=report_path,
        output_path=output_path,
        css_path=css_path,
        dataset_name="Generated report",
    )

    assert result == output_path
    html = output_path.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "Generated report" in html
    assert CSS in html


def test_generate_html_report_accepts_mapping_and_creates_parent_directory(tmp_path):
    css_path = tmp_path / "report.css"
    output_path = tmp_path / "nested" / "report.html"
    css_path.write_text(CSS, encoding="utf-8")

    result = offline_report.generate_html_report(
        complete_report(),
        output_path=output_path,
        css_path=css_path,
    )

    assert result == output_path
    assert output_path.is_file()


@pytest.mark.parametrize(
    ("kwargs", "error_type", "message"),
    [
        ({}, ValueError, "report or report_path is required"),
        ({"report": complete_report()}, ValueError, "output_path is required"),
        (
            {"report": complete_report(), "report_path": "report.json", "output_path": "out.html"},
            ValueError,
            "provide report or report_path, not both",
        ),
        ({"report": [], "output_path": "out.html"}, TypeError, "report must be a mapping"),
    ],
)
def test_generate_html_report_validates_arguments(kwargs, error_type, message):
    with pytest.raises(error_type, match=message):
        offline_report.generate_html_report(**kwargs)


def test_cli_passes_output_as_keyword_only_argument(tmp_path, monkeypatch, capsys):
    report_path = tmp_path / "report.json"
    output_path = tmp_path / "report.html"
    report_path.write_text(json.dumps(complete_report()), encoding="utf-8")
    calls = {}

    def fake_generate(report, *, output_path, dataset_name="", **kwargs):
        calls.update(
            report=report,
            output_path=output_path,
            dataset_name=dataset_name,
        )
        return output_path

    monkeypatch.setattr(offline_report, "generate_html_report", fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["drift-guardian-report", str(report_path), str(output_path), "--dataset-name", "Demo"],
    )

    offline_report.main()

    assert calls == {
        "report": report_path,
        "output_path": output_path,
        "dataset_name": "Demo",
    }
    assert f"Report written to {output_path}" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.59, "passed"),
        (0.60, "warning"),
        (0.749, "warning"),
        (0.75, "critical"),
        (float("nan"), "unknown"),
        (float("inf"), "unknown"),
        (None, "unknown"),
        ("0.75", "unknown"),
    ],
)
def test_av_status_uses_documented_offline_boundaries(value, expected):
    assert offline_report._av_status(value) == expected
