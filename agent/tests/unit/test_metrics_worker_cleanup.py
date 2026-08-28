"""Tests for removal of per-worker metric label series when a worker goes offline.

Prometheus client never expires label series on its own. In Kubernetes, the
Celery worker `hostname` embeds the pod name, so every rollout/scale event
permanently adds new label combinations unless we explicitly remove the old
worker's series when it reports offline.
"""

import os
import sys
import unittest
from datetime import UTC, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from prometheus_client import CollectorRegistry

from constants import EventType
from metrics import MetricsCollector
from models import TaskEvent, WorkerEvent


def _task_event(task_id: str, task_name: str, event_type: str, worker: str, runtime: float | None = None) -> TaskEvent:
    return TaskEvent(
        task_id=task_id,
        task_name=task_name,
        event_type=event_type,
        timestamp=datetime.now(UTC),
        hostname=worker,
        runtime=runtime,
    )


def _worker_event(worker: str, event_type: str, active: int | None = None) -> WorkerEvent:
    return WorkerEvent(hostname=worker, event_type=event_type, timestamp=datetime.now(UTC), active=active)


def _sample_label_tuples(collector: MetricsCollector, metric_name: str) -> set[tuple]:
    for metric_family in collector.registry.collect():
        if metric_family.name != metric_name:
            continue
        return {tuple(sample.labels.items()) for sample in metric_family.samples}
    return set()


class TestMetricsWorkerCleanup(unittest.TestCase):
    def setUp(self):
        self.registry = CollectorRegistry()
        self.collector = MetricsCollector(registry=self.registry)

    def test_worker_offline_removes_worker_status_series(self):
        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_ONLINE.value))
        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_OFFLINE.value))

        labels = _sample_label_tuples(self.collector, "kanchi_worker_status")
        self.assertEqual(labels, set())

    def test_worker_offline_removes_worker_active_tasks_series(self):
        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_ONLINE.value, active=1))
        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_OFFLINE.value))

        labels = _sample_label_tuples(self.collector, "kanchi_worker_active_tasks")
        self.assertEqual(labels, set())

    def test_worker_offline_removes_task_events_total_series_for_that_worker(self):
        self.collector.record_task_event(
            _task_event("t1", "tasks.do_thing", EventType.TASK_RECEIVED.value, "worker-a")
        )

        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_OFFLINE.value))

        labels = _sample_label_tuples(self.collector, "kanchi_task_events")
        self.assertEqual(labels, set())

    def test_worker_offline_removes_execution_duration_and_queue_wait_series(self):
        self.collector.record_task_event(
            _task_event("t1", "tasks.do_thing", EventType.TASK_RECEIVED.value, "worker-a")
        )
        self.collector.record_task_event(_task_event("t1", "tasks.do_thing", EventType.TASK_STARTED.value, "worker-a"))
        self.collector.record_task_event(
            _task_event("t1", "tasks.do_thing", EventType.TASK_SUCCEEDED.value, "worker-a", runtime=1.5)
        )

        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_OFFLINE.value))

        self.assertEqual(_sample_label_tuples(self.collector, "kanchi_task_queue_wait_seconds"), set())
        self.assertEqual(_sample_label_tuples(self.collector, "kanchi_task_execution_duration_seconds_count"), set())

    def test_worker_offline_removes_prefetch_series_for_that_worker(self):
        self.collector.record_task_event(
            _task_event("t1", "tasks.do_thing", EventType.TASK_RECEIVED.value, "worker-a")
        )

        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_OFFLINE.value))

        labels = _sample_label_tuples(self.collector, "kanchi_worker_prefetch_count")
        self.assertEqual(labels, set())

    def test_worker_offline_does_not_affect_other_workers_series(self):
        self.collector.record_task_event(
            _task_event("t1", "tasks.do_thing", EventType.TASK_RECEIVED.value, "worker-a")
        )
        self.collector.record_task_event(
            _task_event("t2", "tasks.do_thing", EventType.TASK_RECEIVED.value, "worker-b")
        )

        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_OFFLINE.value))

        labels = _sample_label_tuples(self.collector, "kanchi_task_events")
        self.assertEqual(
            labels,
            {(("task_name", "tasks.do_thing"), ("event_type", EventType.TASK_RECEIVED.value), ("worker", "worker-b"))},
        )

    def test_worker_reappearing_after_offline_tracks_series_again(self):
        self.collector.record_task_event(
            _task_event("t1", "tasks.do_thing", EventType.TASK_RECEIVED.value, "worker-a")
        )
        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_OFFLINE.value))

        self.collector.record_worker_event(_worker_event("worker-a", EventType.WORKER_ONLINE.value))
        self.collector.record_task_event(
            _task_event("t2", "tasks.do_thing", EventType.TASK_RECEIVED.value, "worker-a")
        )

        labels = _sample_label_tuples(self.collector, "kanchi_task_events")
        self.assertEqual(
            labels,
            {(("task_name", "tasks.do_thing"), ("event_type", EventType.TASK_RECEIVED.value), ("worker", "worker-a"))},
        )


if __name__ == "__main__":
    unittest.main()
