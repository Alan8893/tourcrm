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
