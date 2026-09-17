import pytest

from backend.observability.anomaly_detection import AnomalyDetectionEngine
from backend.observability.metrics_storage import MetricsStorageEngine


@pytest.fixture
def engine():
    return AnomalyDetectionEngine()


class TestBaselineGeneration:
    def test_train_baseline_computes_mean_and_stddev(self, engine):
        baseline = engine.train_baseline("cpu_usage", [10, 20, 30, 40, 50])

        assert baseline.mean == 30
        assert baseline.stddev > 0

    def test_train_baseline_rejects_empty_samples(self, engine):
        with pytest.raises(ValueError):
            engine.train_baseline("cpu_usage", [])

    def test_train_baseline_rejects_unknown_detection_type(self, engine):
        with pytest.raises(ValueError):
            engine.train_baseline("cpu_usage", [1, 2, 3], detection_type="unknown")

    def test_train_baseline_from_storage_values(self, engine):
        storage = MetricsStorageEngine()
        storage.write("cpu_usage", "2026-01-01T00:00:00+00:00", 10)
        storage.write("cpu_usage", "2026-01-01T00:01:00+00:00", 20)
        storage.write("cpu_usage", "2026-01-01T00:02:00+00:00", 30)

        baseline = engine.train_baseline("cpu_usage", storage.values("cpu_usage"))

        assert baseline.mean == 20


class TestScoreCalculation:
    def test_score_returns_zero_at_mean(self, engine):
        engine.train_baseline("cpu_usage", [10, 20, 30])

        assert engine.score("cpu_usage", 20) == 0

    def test_score_scales_with_deviation(self, engine):
        engine.train_baseline("cpu_usage", [10, 20, 30])

        near_score = engine.score("cpu_usage", 22)
        far_score = engine.score("cpu_usage", 90)

        assert far_score > near_score

    def test_score_without_baseline_raises(self, engine):
        with pytest.raises(KeyError):
            engine.score("unknown_metric", 10)

    def test_score_handles_zero_stddev(self, engine):
        engine.train_baseline("cpu_usage", [50, 50, 50])

        assert engine.score("cpu_usage", 50) == 0
        assert engine.score("cpu_usage", 60) == float("inf")


class TestAnomalyDetection:
    def test_detect_returns_none_within_sensitivity(self, engine):
        engine.train_baseline("cpu_usage", [10, 20, 30], sensitivity=3.0)

        assert engine.detect("cpu_usage", 22) is None

    def test_detect_flags_anomaly_beyond_sensitivity(self, engine):
        engine.train_baseline("cpu_usage", [10, 20, 30], sensitivity=1.0)

        event = engine.detect("cpu_usage", 500)

        assert event is not None
        assert event.metric_name == "cpu_usage"
        assert event.value == 500

    def test_detect_without_baseline_raises(self, engine):
        with pytest.raises(KeyError):
            engine.detect("unknown_metric", 10)

    def test_detect_records_event_in_history(self, engine):
        engine.train_baseline("cpu_usage", [10, 20, 30], sensitivity=1.0)

        event = engine.detect("cpu_usage", 500)

        assert event in engine.list_events()


class TestFeedbackProcessing:
    def test_feedback_marks_false_positive(self, engine):
        engine.train_baseline("cpu_usage", [10, 20, 30], sensitivity=1.0)
        event = engine.detect("cpu_usage", 500)

        updated = engine.feedback(event.event_id, is_false_positive=True)

        assert updated.is_false_positive is True

    def test_list_events_excludes_false_positives_when_requested(self, engine):
        engine.train_baseline("cpu_usage", [10, 20, 30], sensitivity=1.0)
        event = engine.detect("cpu_usage", 500)
        engine.feedback(event.event_id, is_false_positive=True)

        assert engine.list_events(exclude_false_positives=True) == []
        assert len(engine.list_events()) == 1

    def test_feedback_unknown_event_raises(self, engine):
        with pytest.raises(KeyError):
            engine.feedback("missing-event", is_false_positive=True)
