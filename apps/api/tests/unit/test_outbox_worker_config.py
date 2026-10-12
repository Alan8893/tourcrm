"""Pure unit tests for the outbox worker's configuration and retry backoff
(Issue #325, ADR-0046 §5.4) — no database."""

from datetime import timedelta

import pytest

from app.outbox.worker import (
    WorkerConfig,
    WorkerConfigurationError,
    default_worker_id,
    retry_delay,
)


def test_retry_delay_is_deterministic_exponential_and_capped() -> None:
    config = WorkerConfig(
        worker_id="w", retry_base=timedelta(seconds=60), retry_max=timedelta(seconds=300)
    )
    assert [retry_delay(attempt, config).total_seconds() for attempt in range(1, 6)] == [
        60,
        120,
        240,
        300,
        300,
    ]


def test_defaults_are_bounded() -> None:
    config = WorkerConfig(worker_id="w")
    assert config.max_attempts == 5
    assert config.batch_size == 1
    assert config.lease == timedelta(minutes=5)


@pytest.mark.parametrize(
    "overrides",
    [
        {"worker_id": " "},
        {"batch_size": 0},
        {"max_attempts": 0},
        {"lease": timedelta(0)},
        {"poll_interval": timedelta(seconds=-1)},
        {"retry_base": timedelta(seconds=10), "retry_max": timedelta(seconds=5)},
    ],
)
def test_invalid_configuration_is_rejected(overrides: dict[str, object]) -> None:
    fields: dict[str, object] = {"worker_id": "w", **overrides}
    with pytest.raises(WorkerConfigurationError):
        WorkerConfig(**fields)  # type: ignore[arg-type]


def test_from_env_reads_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_WORKER_ID", "worker-7")
    monkeypatch.setenv("OUTBOX_WORKER_POLL_INTERVAL_SECONDS", "2")
    monkeypatch.setenv("OUTBOX_WORKER_BATCH_SIZE", "4")
    monkeypatch.setenv("OUTBOX_WORKER_LEASE_SECONDS", "120")
    monkeypatch.setenv("OUTBOX_WORKER_MAX_ATTEMPTS", "8")
    monkeypatch.setenv("OUTBOX_WORKER_RETRY_BASE_SECONDS", "15")
    monkeypatch.setenv("OUTBOX_WORKER_RETRY_MAX_SECONDS", "900")
    config = WorkerConfig.from_env()
    assert config == WorkerConfig(
        worker_id="worker-7",
        poll_interval=timedelta(seconds=2),
        batch_size=4,
        lease=timedelta(seconds=120),
        max_attempts=8,
        retry_base=timedelta(seconds=15),
        retry_max=timedelta(seconds=900),
    )


@pytest.mark.parametrize("value", ["0", "-3", "abc"])
def test_from_env_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OUTBOX_WORKER_MAX_ATTEMPTS", value)
    with pytest.raises(WorkerConfigurationError):
        WorkerConfig.from_env()


def test_default_worker_id_is_unique_per_call() -> None:
    assert default_worker_id() != default_worker_id()


# --- Process resilience and startup diagnostics (Issue #342 follow-up) -------------


def test_a_database_error_while_claiming_does_not_end_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import threading

    from sqlalchemy.exc import OperationalError

    from app.outbox.worker import OutboxWorker

    stop = threading.Event()
    calls = {"count": 0}

    class _Worker(OutboxWorker):
        def run_once(self, stop_event=None):  # type: ignore[no-untyped-def]
            calls["count"] += 1
            if calls["count"] == 1:
                raise OperationalError(
                    "SELECT 1", {}, Exception("connection to db:5432 password=hunter2 failed")
                )
            stop.set()
            return []

    worker = _Worker(
        session_factory=None,  # type: ignore[arg-type]
        handlers={},
        config=WorkerConfig(worker_id="w", poll_interval=timedelta(milliseconds=10)),
    )
    with caplog.at_level("INFO", logger="tourcrm.worker"):
        worker.run(stop)

    assert calls["count"] == 2  # survived the error and claimed again
    assert "error_code=database_error" in caplog.text
    assert "hunter2" not in caplog.text and "db:5432" not in caplog.text
    assert "outbox worker stopped" in caplog.text


def test_invalid_configuration_exits_2_with_one_safe_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.cli import run_outbox_worker

    # No connection is opened before the configuration is validated.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://user:s3cret@crm.example.org")
    with caplog.at_level("ERROR", logger="tourcrm.worker"):
        assert run_outbox_worker.main() == run_outbox_worker.EXIT_CONFIGURATION
    assert "outbox worker configuration invalid: APP_PUBLIC_BASE_URL" in caplog.text
    assert "s3cret" not in caplog.text

    monkeypatch.delenv("APP_PUBLIC_BASE_URL")
    monkeypatch.setenv("OUTBOX_WORKER_BATCH_SIZE", "0")
    assert run_outbox_worker.main() == run_outbox_worker.EXIT_CONFIGURATION


def test_startup_reports_each_key_ring_state_without_key_material(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.cli.run_outbox_worker import log_key_ring_status

    key = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="  # test-only bytes 0..31
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEYS", f"k1:{key}")
    monkeypatch.delenv("AUTH_DELIVERY_ENCRYPTION_KEYS", raising=False)
    with caplog.at_level("INFO", logger="tourcrm.worker"):
        log_key_ring_status()
    assert "key_ring=SETTINGS_ENCRYPTION_KEYS state=available" in caplog.text
    assert "key_ring=AUTH_DELIVERY_ENCRYPTION_KEYS state=missing" in caplog.text
    assert key not in caplog.text

    caplog.clear()
    monkeypatch.setenv("AUTH_DELIVERY_ENCRYPTION_KEYS", "not-a-key-ring")
    with caplog.at_level("INFO", logger="tourcrm.worker"):
        log_key_ring_status()
    assert "key_ring=AUTH_DELIVERY_ENCRYPTION_KEYS state=invalid" in caplog.text
    assert "not-a-key-ring" not in caplog.text
