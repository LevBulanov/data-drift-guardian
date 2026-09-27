import json
import sys

import pandas as pd

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


def test_av_status_uses_documented_offline_boundaries():
    assert offline_report._av_status(0.59) == "passed"
    assert offline_report._av_status(0.60) == "warning"
    assert offline_report._av_status(0.75) == "critical"
