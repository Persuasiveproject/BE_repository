import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import ConnectionError

from .models import Debate, DebateStatistics
from .result_state import (
    RETRY_SECONDS, STATE_TTL_SECONDS, _key, _redis,
    clear_result_state, get_result_state, set_result_state,
)
from .results import compute_debate_result, save_debate_statistics
from .tasks.results import compute_pending_results
from .test_results import ResultFixture


class ResultRecoveryTests(ResultFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.debate.status = Debate.Status.CLOSED
        self.debate.save(update_fields=["status"])

    def status(self):
        response = self.client.get(reverse("debate-result", args=[self.debate.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "RESULT_NOT_READY")
        return response.data["error"]["details"]["status"]

    def fail(self):
        def fail_after_insert(debate):
            save_debate_statistics(debate)
            raise RuntimeError("Failure after partial write")
        with patch("debates.results.save_debate_statistics", side_effect=fail_after_insert):
            with self.assertLogs("debates.results", level="ERROR"):
                self.assertFalse(compute_debate_result(self.debate.pk))
        self.assertFalse(DebateStatistics.objects.filter(debate=self.debate).exists())

    def test_retry_wait_then_retry_due_and_processing_status(self):
        self.assertEqual(self.status(), "PENDING")
        self.fail()
        self.assertEqual(self.status(), "FAILED")
        self.assertGreater(_redis().ttl(_key(self.debate.pk)), RETRY_SECONDS)
        self.assertLessEqual(_redis().ttl(_key(self.debate.pk)), STATE_TTL_SECONDS)
        self.assertFalse(compute_debate_result(self.debate.pk))
        state = get_result_state(self.debate.pk)
        state["retry_after"] = timezone.now().timestamp() - 1
        _redis().set(_key(self.debate.pk), json.dumps(state), ex=60)
        self.assertEqual(self.status(), "FAILED")

        def assert_processing(debate):
            self.assertEqual(self.status(), "PENDING")
            save_debate_statistics(debate)
        with patch("debates.results.save_debate_statistics", side_effect=assert_processing):
            self.assertTrue(compute_debate_result(self.debate.pk))
        self.assertIsNone(get_result_state(self.debate.pk))
        self.assertFalse(compute_debate_result(self.debate.pk))
        self.assertEqual(DebateStatistics.objects.count(), 1)

    def test_actual_redis_expiry_recovers_missing_result(self):
        self.fail()
        _redis().pexpire(_key(self.debate.pk), 1)
        time.sleep(0.02)
        self.assertIsNone(get_result_state(self.debate.pk))
        self.assertEqual(self.status(), "PENDING")
        self.assertEqual(compute_pending_results(), 1)

    def test_stale_processing_hint_after_worker_loss_does_not_block(self):
        set_result_state(self.debate.pk, "PENDING")
        self.assertEqual(self.status(), "PENDING")
        self.assertEqual(compute_pending_results(), 1)

    def test_corrupt_or_implausible_state_is_unknown_and_recovers(self):
        for raw in ["{", "[]", '{"status":"FAILED"}',
                    json.dumps({"status": "FAILED", "retry_after": 10 ** 400}),
                    '{"status":"FAILED","retry_after":1e999}',
                    json.dumps({"status": "FAILED", "retry_after": timezone.now().timestamp() + 86400})]:
            _redis().set(_key(self.debate.pk), raw)
            self.assertIsNone(get_result_state(self.debate.pk))
            self.assertEqual(self.status(), "PENDING")
        self.assertEqual(compute_pending_results(), 1)

    def test_redis_outage_does_not_report_unobserved_failure_or_stop_work(self):
        with patch("debates.result_state._redis", side_effect=ConnectionError("Unavailable")):
            self.assertEqual(self.status(), "PENDING")
            self.fail()
            self.assertEqual(self.status(), "PENDING")
            self.assertEqual(compute_pending_results(), 1)

    def test_completed_db_result_takes_precedence_over_stale_failed_hint(self):
        self.assertTrue(compute_debate_result(self.debate.pk))
        set_result_state(self.debate.pk, "FAILED")
        self.assertEqual(self.client.get(reverse("debate-result", args=[self.debate.pk])).status_code, 200)

    def test_transaction_abort_for_one_debate_does_not_stop_sweep(self):
        other = Debate.objects.create(
            creator=self.other, category=self.category, type=Debate.Type.ENDLESS,
            title="Other", content="Topic", status=Debate.Status.CLOSED,
        )
        self.addCleanup(clear_result_state, other.pk)
        original = compute_debate_result
        def attempt(debate_id):
            if debate_id == self.debate.pk:
                raise RuntimeError("Transaction aborted")
            return original(debate_id)
        with patch("debates.tasks.results.compute_debate_result", side_effect=attempt):
            with self.assertLogs("debates.tasks.results", level="ERROR"):
                self.assertEqual(compute_pending_results(), 1)
        self.assertEqual(compute_pending_results(), 1)

    def test_hundred_cooling_down_results_do_not_starve_later_work(self):
        blocked = [self.debate]
        blocked += Debate.objects.bulk_create([
            Debate(creator=self.other, category=self.category, type=Debate.Type.ENDLESS,
                   title=f"Waiting {i}", content="Topic", status=Debate.Status.CLOSED)
            for i in range(100)
        ])
        for debate in blocked:
            clear_result_state(debate.pk)
            self.addCleanup(clear_result_state, debate.pk)
            set_result_state(debate.pk, "FAILED")
        ready = Debate.objects.create(
            creator=self.other, category=self.category, type=Debate.Type.ENDLESS,
            title="Ready", content="Topic", status=Debate.Status.CLOSED,
        )
        clear_result_state(ready.pk)
        self.addCleanup(clear_result_state, ready.pk)
        self.assertEqual(compute_pending_results(), 1)
        self.assertTrue(DebateStatistics.objects.filter(debate=ready).exists())

    def test_postgresql_lock_skips_duplicate_without_waiting(self):
        self.check_concurrent_attempts()

    def test_processing_wins_over_stale_failure_when_redis_write_fails(self):
        self.check_concurrent_attempts(stale_failure=True)

    def check_concurrent_attempts(self, stale_failure=False):
        self.assertEqual(connection.vendor, "postgresql")
        if stale_failure:
            _redis().set(_key(self.debate.pk), json.dumps({
                "status": "FAILED", "retry_after": 0,
            }), ex=60)
        entered, release = threading.Event(), threading.Event()
        def slow_save(debate):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Timed out waiting for competing attempt")
            save_debate_statistics(debate)
        def worker():
            close_old_connections()
            try:
                return compute_debate_result(self.debate.pk)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with patch("debates.results.save_debate_statistics", side_effect=slow_save), patch(
                "debates.results.set_result_state",
                side_effect=lambda pk, status: None if stale_failure else set_result_state(pk, status),
            ):
                future = executor.submit(worker)
                try:
                    self.assertTrue(entered.wait(10))
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '500ms'")
                        self.assertFalse(compute_debate_result(self.debate.pk))
                    self.assertEqual(self.status(), "PENDING")
                finally:
                    release.set()
                self.assertTrue(future.result(timeout=10))
        self.assertEqual(DebateStatistics.objects.count(), 1)
