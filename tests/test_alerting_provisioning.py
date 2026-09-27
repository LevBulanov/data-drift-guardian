from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ALERTING_DIR = ROOT / "monitoring" / "grafana" / "provisioning" / "alerting"
PROMETHEUS_UID = "cfxnbdacrd728d"
RECEIVER = "Drift Guardian Alerts Telegram"


EXPECTED_RULES = {
    "AV Drift – Warning": {
        "uid": "dfz3zg8qn3oxse",
        "group": "Adversarial Validation",
        "severity": "warning",
        "domain": "adversarial_validation",
        "owner": "ml_monitoring",
        "scope": None,
        "refs": {"av_status", "roc_auc", "worst_fold_auc", "cv_std", "C"},
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "AV Drift – Critical": {
        "uid": "cfz40uwii8a9sd",
        "group": "Adversarial Validation",
        "severity": "critical",
        "domain": "adversarial_validation",
        "owner": "ml_monitoring",
        "scope": None,
        "refs": {"av_status", "roc_auc", "worst_fold_auc", "cv_std", "C"},
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "Stream Health – Warning": {
        "uid": "streamwarn01",
        "group": "Stream Health",
        "severity": "warning",
        "domain": "stream_health",
        "owner": "data_engineering",
        "scope": "list",
        "refs": {
            "stream_status",
            "metric_value",
            "warning_threshold",
            "critical_threshold",
            "C",
        },
        "for": "5m",
        "states": ("KeepLast", "KeepLast"),
    },
    "Stream Health – Critical": {
        "uid": "streamcrit01",
        "group": "Stream Health",
        "severity": "critical",
        "domain": "stream_health",
        "owner": "data_engineering",
        "scope": "list",
        "refs": {
            "stream_status",
            "metric_value",
            "warning_threshold",
            "critical_threshold",
            "C",
        },
        "for": "1m",
        "states": ("KeepLast", "KeepLast"),
    },
    "Prediction Drift Metrics – Warning": {
        "uid": "efz3u94rcw6bke",
        "group": "Prediction Drift",
        "severity": "warning",
        "domain": "prediction_drift",
        "owner": "ml_monitoring",
        "scope": "list",
        "refs": {"metric_status", "metric_value", "C"},
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "Prediction Drift Status – Warning": {
        "uid": "afz3v51xspjpca",
        "group": "Prediction Drift",
        "severity": "warning",
        "domain": "prediction_drift",
        "owner": "ml_monitoring",
        "scope": None,
        "refs": {"prediction_status", "metric_count", "C"},
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "Prediction Drift Metrics – Critical": {
        "uid": "cfz3xhezruigwd",
        "group": "Prediction Drift",
        "severity": "critical",
        "domain": "prediction_drift",
        "owner": "ml_monitoring",
        "scope": "list",
        "refs": {"metric_status", "metric_value", "C"},
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "Prediction Drift Status – Critical": {
        "uid": "dfz3xmztsy8lcd",
        "group": "Prediction Drift",
        "severity": "critical",
        "domain": "prediction_drift",
        "owner": "ml_monitoring",
        "scope": None,
        "refs": {"prediction_status", "metric_count", "C"},
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "Drift Exporter – Unavailable": {
        "uid": "exporterdown01",
        "group": "System Health",
        "severity": "critical",
        "domain": "system_health",
        "owner": "data_engineering",
        "scope": None,
        "refs": {"exporter_down", "C"},
        "for": "1m",
        "states": ("Alerting", "Alerting"),
    },
    "Drift Analysis – Stale": {
        "uid": "analysisstale01",
        "group": "System Health",
        "severity": "critical",
        "domain": "system_health",
        "owner": "data_engineering",
        "scope": None,
        "refs": {"analysis_stale", "C"},
        "for": "1m",
        "states": ("Alerting", "Alerting"),
    },
    "Input Data Drift – Critical": {
        "uid": "cfz1chowchhq8c",
        "group": "Window Drift",
        "severity": "critical",
        "domain": "input_drift",
        "owner": "ml_monitoring",
        "scope": None,
        "refs": {
            "overall_status",
            "drifted_features",
            "evaluated_features",
            "critical_metrics",
            "warning_metrics",
            "C",
        },
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "Input Data Drift – Warning": {
        "uid": "dfz3caqbwckqob",
        "group": "Window Drift",
        "severity": "warning",
        "domain": "input_drift",
        "owner": "ml_monitoring",
        "scope": None,
        "refs": {
            "overall_status",
            "warning_features",
            "evaluated_features",
            "warning_metrics",
            "C",
        },
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "Feature Drift – Critical": {
        "uid": "efz3e3h2h3gn4b",
        "group": "Window Drift",
        "severity": "critical",
        "domain": "input_drift",
        "owner": "ml_monitoring",
        "scope": "list",
        "refs": {"feature_status", "metric_count", "C"},
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
    "Feature Drift – Warning": {
        "uid": "cfz45ainayk1sc",
        "group": "Window Drift",
        "severity": "warning",
        "domain": "input_drift",
        "owner": "ml_monitoring",
        "scope": "list",
        "refs": {"feature_status", "metric_count", "C"},
        "for": "0s",
        "states": ("KeepLast", "KeepLast"),
    },
}


def _load(name: str) -> dict:
    with (ALERTING_DIR / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _rules() -> list[dict]:
    config = _load("alert-rules.yaml")
    return [rule for group in config["groups"] for rule in group["rules"]]


def _rule(title: str) -> dict:
    return next(rule for rule in _rules() if rule["title"] == title)


def _query(rule: dict, ref_id: str) -> dict:
    return next(query for query in rule["data"] if query["refId"] == ref_id)


def test_all_alerting_files_have_supported_api_version() -> None:
    for path in ALERTING_DIR.glob("*.yaml"):
        assert _load(path.name)["apiVersion"] == 1


def test_complete_rule_contract() -> None:
    config = _load("alert-rules.yaml")
    actual = {
        rule["title"]: (group["name"], rule)
        for group in config["groups"]
        for rule in group["rules"]
    }
    assert set(actual) == set(EXPECTED_RULES)

    for title, expected in EXPECTED_RULES.items():
        group_name, rule = actual[title]
        labels = rule["labels"]
        assert group_name == expected["group"]
        assert rule["uid"] == expected["uid"]
        assert rule["condition"] == "C"
        assert rule["for"] == expected["for"]
        assert (rule["noDataState"], rule["execErrState"]) == expected["states"]
        assert rule["isPaused"] is False
        assert {query["refId"] for query in rule["data"]} == expected["refs"]
        assert labels == {
            "domain": expected["domain"],
            "owner": expected["owner"],
            **({"scope": expected["scope"]} if expected["scope"] else {}),
            "service": "drift_guardian",
            "severity": expected["severity"],
        }


def test_every_rule_has_valid_queries_condition_and_annotations() -> None:
    config = _load("alert-rules.yaml")
    for group in config["groups"]:
        assert group["orgId"] == 1
        assert group["folder"] == "Drift Guardian"
        assert group["interval"] == "10s"
        for rule in group["rules"]:
            refs = [query["refId"] for query in rule["data"]]
            assert len(refs) == len(set(refs))

            condition = _query(rule, "C")
            assert condition["datasourceUid"] == "__expr__"
            assert condition["queryType"] == "expression"
            assert condition["model"]["type"] == "threshold"
            source_ref = condition["model"]["expression"]
            assert source_ref in refs and source_ref != "C"

            for query in rule["data"]:
                assert query["model"]["refId"] == query["refId"]
                if query["refId"] != "C":
                    assert query["datasourceUid"] == PROMETHEUS_UID
                    assert query["model"]["expr"].strip()
                    assert query["model"]["instant"] is True
                    assert query["model"]["range"] is False

            annotations = rule["annotations"]
            assert {"summary", "action"} <= annotations.keys()
            assert all(value.strip() for value in annotations.values())
            if rule["labels"].get("scope") == "list":
                assert {"list_label", "list_title", "list_value"} <= annotations.keys()
            else:
                assert "description" in annotations


def test_alert_rules_have_stable_unique_identity_and_expected_groups() -> None:
    config = _load("alert-rules.yaml")
    rules = _rules()

    assert {group["name"] for group in config["groups"]} == {
        "Adversarial Validation",
        "Stream Health",
        "Prediction Drift",
        "System Health",
        "Window Drift",
    }
    assert len(rules) == 14
    assert len({rule["uid"] for rule in rules}) == len(rules)
    assert len({rule["title"] for rule in rules}) == len(rules)


def test_rules_use_policy_tree_and_consistent_owners() -> None:
    for rule in _rules():
        labels = rule["labels"]
        assert "notification_settings" not in rule
        assert "method" not in labels
        assert labels["service"] == "drift_guardian"
        assert labels["severity"] in {"warning", "critical"}
        expected_owner = (
            "data_engineering"
            if labels["domain"] in {"stream_health", "system_health"}
            else "ml_monitoring"
        )
        assert labels["owner"] == expected_owner


def test_policy_routes_all_alerts_to_one_receiver() -> None:
    policy = _load("policies.yaml")["policies"][0]
    routes = policy["routes"]

    assert policy["orgId"] == 1
    assert policy["receiver"] == RECEIVER
    assert policy["group_by"] == ["grafana_folder", "alertname"]
    assert policy["group_wait"] == "30s"
    assert policy["group_interval"] == "5m"
    assert policy["repeat_interval"] == "4h"
    assert len(routes) == 2
    assert {route["receiver"] for route in routes} == {RECEIVER}
    by_severity = {
        next(
            matcher[2]
            for matcher in route["object_matchers"]
            if matcher[0] == "severity"
        ): route
        for route in routes
    }
    assert set(by_severity) == {"warning", "critical"}
    for severity, route in by_severity.items():
        assert route["object_matchers"] == [
            ["service", "=", "drift_guardian"],
            ["severity", "=", severity],
        ]
    assert by_severity["warning"]["active_time_intervals"] == ["working-hours"]
    assert "active_time_intervals" not in by_severity["critical"]
    for route in routes:
        assert route["group_wait"] == "1m"
        assert route["group_interval"] == "1m"
        assert route["repeat_interval"] == "4h"


def test_mute_intervals_and_policy_references_are_valid() -> None:
    mute_times = _load("mute-timings.yaml")["muteTimes"]
    intervals = {item["name"]: item for item in mute_times}

    assert set(intervals) == {"working-hours", "working-hours-morning"}
    assert intervals["working-hours"]["time_intervals"][0]["times"] == [
        {"start_time": "10:00", "end_time": "18:00"}
    ]
    assert intervals["working-hours-morning"]["time_intervals"][0]["times"] == [
        {"start_time": "09:00", "end_time": "13:00"}
    ]
    for interval in intervals.values():
        assert interval["orgId"] == 1
        schedule = interval["time_intervals"][0]
        assert schedule["weekdays"] == ["monday:friday"]
        assert schedule["location"] == "Europe/Moscow"

    referenced = {
        name
        for route in _load("policies.yaml")["policies"][0]["routes"]
        for name in route.get("active_time_intervals", [])
    }
    assert referenced == {"working-hours"}
    assert referenced <= intervals.keys()


def test_telegram_contact_point_uses_the_provisioned_template() -> None:
    contact_points = _load("contact-points.yaml")["contactPoints"]
    assert len(contact_points) == 1
    contact_point = contact_points[0]
    assert contact_point["orgId"] == 1
    assert contact_point["name"] == RECEIVER
    assert len(contact_point["receivers"]) == 1

    receiver = contact_point["receivers"][0]
    assert receiver["uid"] == "bfz1bo1hlidxcc"
    assert receiver["type"] == "telegram"
    assert receiver["disableResolveMessage"] is False
    assert receiver["settings"] == {
        "bottoken": "$TELEGRAM_BOT_TOKEN",
        "chatid": "-5433199058",
        "disable_notifications": False,
        "disable_web_page_preview": False,
        "message": '{{ template "drift_guardian.message" . }}',
        "parse_mode": "HTML",
        "protect_content": False,
    }


def test_notification_template_covers_all_alert_branches() -> None:
    templates = _load("templates.yaml")["templates"]
    assert len(templates) == 1
    provisioned = templates[0]
    assert provisioned["orgId"] == 1
    assert provisioned["name"] == "drift_guardian"

    template = provisioned["template"]
    required_fragments = {
        '{{ define "drift_guardian.message" }}',
        'eq (index .CommonLabels "alertname") "DatasourceNoData"',
        'eq (index .CommonLabels "scope") "list"',
        ".Alerts.Firing",
        ".Alerts.Resolved",
        'index .Labels "severity"',
        'index .Annotations "summary"',
        'index .Annotations "description"',
        'index .Annotations "action"',
        "NO DATA",
        "DATA RESTORED",
        "SEVERITY CONDITION CLEARED",
    }
    for fragment in required_fragments:
        assert fragment in template

    message = _load("contact-points.yaml")["contactPoints"][0]["receivers"][0][
        "settings"
    ]["message"]
    assert 'template "drift_guardian.message"' in message


def test_av_uses_only_four_result_streaks() -> None:
    for title in ("AV Drift – Warning", "AV Drift – Critical"):
        expression = _query(_rule(title), "av_status")["model"]["expr"]
        assert "drift_av_status_streak" in expression
        assert ">= 4" in expression
        assert "[5m:]" not in expression
        assert "offset 5m" not in expression


def test_warning_to_critical_transition_has_no_silent_gap() -> None:
    bridge_rules = {
        "AV Drift – Warning": "av_status",
        "Prediction Drift Metrics – Warning": "metric_status",
        "Prediction Drift Status – Warning": "prediction_status",
        "Input Data Drift – Warning": "overall_status",
        "Feature Drift – Warning": "feature_status",
    }
    for title, ref_id in bridge_rules.items():
        expression = _query(_rule(title), ref_id)["model"]["expr"]
        assert "unless" in expression
        assert 'status="critical"' in expression

    stream_condition = _query(_rule("Stream Health – Warning"), "C")
    evaluator = stream_condition["model"]["conditions"][0]["evaluator"]
    assert evaluator == {"params": [1], "type": "eq"}
    stream_expression = _query(_rule("Stream Health – Warning"), "stream_status")[
        "model"
    ]["expr"]
    assert "unless on(instance, metric)" in stream_expression
    assert "[1m:]" in stream_expression


def test_stream_queries_match_thresholds_by_instance_and_metric() -> None:
    for title in ("Stream Health – Warning", "Stream Health – Critical"):
        rule = _rule(title)
        status_expression = _query(rule, "stream_status")["model"]["expr"]
        value_expression = _query(rule, "metric_value")["model"]["expr"]
        warning_threshold = _query(rule, "warning_threshold")["model"]["expr"]
        critical_threshold = _query(rule, "critical_threshold")["model"]["expr"]

        assert "on(instance, metric)" in status_expression
        assert "on(metric)" not in status_expression
        assert '{job="drift-exporter"}' in status_expression
        assert '{job="drift-exporter"}' in value_expression
        assert "max by (instance, metric)" in warning_threshold
        assert "max by (instance, metric)" in critical_threshold


def test_scalar_counts_have_zero_fallbacks() -> None:
    for rule in _rules():
        for query in rule["data"]:
            expression = query.get("model", {}).get("expr", "")
            if "count(" in expression and "count by" not in expression:
                assert "or vector(0)" in expression, (
                    f"{rule['title']} query {query['refId']} needs a zero fallback"
                )


def test_system_health_covers_exporter_and_stale_analysis() -> None:
    exporter = _rule("Drift Exporter – Unavailable")
    stale = _rule("Drift Analysis – Stale")

    assert (
        'up{job="drift-exporter"}' in _query(exporter, "exporter_down")["model"]["expr"]
    )
    assert (
        "drift_report_timestamp_seconds"
        in _query(stale, "analysis_stale")["model"]["expr"]
    )
    for rule in (exporter, stale):
        assert rule["for"] == "1m"
        assert rule["noDataState"] == "Alerting"
        assert rule["execErrState"] == "Alerting"
        assert rule["labels"]["severity"] == "critical"
