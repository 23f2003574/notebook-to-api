from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
)
from backend.observability.deployment_governance_metrics_repository import (
    InMemoryGovernanceIntegrityMetricsRepository,
    SQLiteGovernanceIntegrityMetricsRepository,
)


def _sample_metrics(**overrides) -> GovernanceIntegrityMetrics:
    fields = {
        "total_dispatches": 3,
        "successful_dispatches": 2,
        "failed_dispatches": 1,
        "retry_dispatches": 1,
        "average_duration_ms": 42.5,
    }

    fields.update(overrides)

    return GovernanceIntegrityMetrics(**fields)


class TestInMemoryGovernanceIntegrityMetricsRepository:

    def test_load_returns_none_when_empty(self):
        repository = InMemoryGovernanceIntegrityMetricsRepository()

        assert repository.load() is None

    def test_save_then_load_round_trips(self):
        repository = InMemoryGovernanceIntegrityMetricsRepository()

        metrics = _sample_metrics()

        repository.save(metrics)

        assert repository.load() == metrics

    def test_save_overwrites_previous_snapshot(self):
        repository = InMemoryGovernanceIntegrityMetricsRepository()

        repository.save(_sample_metrics())

        second = _sample_metrics(
            total_dispatches=10,
            successful_dispatches=10,
            failed_dispatches=0,
            retry_dispatches=0,
        )

        repository.save(second)

        assert repository.load() == second

    def test_reset_clears_storage(self):
        repository = InMemoryGovernanceIntegrityMetricsRepository()

        repository.save(_sample_metrics())

        repository.reset()

        assert repository.load() is None


class TestSQLiteGovernanceIntegrityMetricsRepository:

    def test_load_returns_none_when_empty(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(
                database_path=tmp_path / "metrics.db"
            )
        )

        repository = SQLiteGovernanceIntegrityMetricsRepository(
            database
        )

        assert repository.load() is None

    def test_save_then_load_round_trips(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(
                database_path=tmp_path / "metrics.db"
            )
        )

        repository = SQLiteGovernanceIntegrityMetricsRepository(
            database
        )

        metrics = _sample_metrics()

        repository.save(metrics)

        assert repository.load() == metrics

    def test_save_upserts_the_single_row(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(
                database_path=tmp_path / "metrics.db"
            )
        )

        repository = SQLiteGovernanceIntegrityMetricsRepository(
            database
        )

        repository.save(_sample_metrics())

        second = _sample_metrics(
            total_dispatches=10,
            successful_dispatches=10,
            failed_dispatches=0,
            retry_dispatches=0,
        )

        repository.save(second)

        assert repository.load() == second

    def test_reset_clears_storage(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(
                database_path=tmp_path / "metrics.db"
            )
        )

        repository = SQLiteGovernanceIntegrityMetricsRepository(
            database
        )

        repository.save(_sample_metrics())

        repository.reset()

        assert repository.load() is None

    def test_persists_and_survives_reload(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database_path = tmp_path / "metrics.db"

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=database_path)
        )

        repository = SQLiteGovernanceIntegrityMetricsRepository(
            database
        )

        metrics = _sample_metrics()

        repository.save(metrics)

        reloaded_database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=database_path)
        )

        reloaded_repository = (
            SQLiteGovernanceIntegrityMetricsRepository(
                reloaded_database
            )
        )

        assert reloaded_repository.load() == metrics
