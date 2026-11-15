from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy_events.metrics import Health, Metrics


def test_metrics_counters_and_reset() -> None:
    metrics = Metrics()
    metrics.notifications_received = 2
    metrics.handler_failures = 1
    metrics.record_error(RuntimeError('boom'))

    data = metrics.as_dict()

    assert data['notifications_received'] == 2
    assert data['handler_failures'] == 1
    assert data['last_error'] == 'boom'
    assert data['last_error_at'] is not None

    metrics.reset()

    assert metrics.notifications_received == 0
    assert metrics.last_error is None
    assert metrics.last_error_at is None


def test_metrics_prometheus_output() -> None:
    metrics = Metrics()
    metrics.events_dispatched = 3

    output = metrics.prometheus()

    assert 'sqlalchemy_events_events_dispatched 3' in output
    assert 'last_error' not in output


def test_metrics_prometheus_empty_metrics() -> None:
    metrics = Metrics()

    assert metrics.prometheus()


def test_health_as_dict() -> None:
    timestamp = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    health = Health(
        healthy=True,
        connected=True,
        last_notification_at=timestamp,
        details={'schema': 'public'},
    )

    data = health.as_dict()

    assert data['healthy']
    assert data['last_notification_at'] == timestamp.isoformat()
    assert data['details'] == {'schema': 'public'}
