import io
import json
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import User
from debates.models import Category, Debate, DebateParticipant, DebateStatistics
from debates.result_state import clear_result_state, get_result_state, set_result_state


class RecoveryCommandTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='runtime@example.invalid', nickname='runtime')
        self.category = Category.objects.create(name='runtime')
        self.ids = []

    def tearDown(self):
        for debate_id in self.ids:
            clear_result_state(debate_id)

    def debate(self, status=Debate.Status.CLOSED):
        now = timezone.now()
        debate = Debate.objects.create(creator=self.user, category=self.category, type='ENDLESS',
            title='Runtime check', content='test', status=status,
            started_at=now-timedelta(hours=73), ends_at=now-timedelta(hours=1),
            closed_at=now if status == Debate.Status.CLOSED else None)
        DebateParticipant.objects.create(debate=debate, user=self.user, anonymous_no=1,
            initial_stance='PRO', current_stance='CON')
        self.ids.append(debate.pk)
        return debate

    def run_sweep(self, **options):
        output = io.StringIO()
        call_command('recover_results', stdout=output, **options)
        return json.loads(output.getvalue())

    def test_missing_closed_result_is_aggregated(self):
        debate = self.debate()
        self.assertEqual(self.run_sweep()['completed'], 1)
        result = DebateStatistics.objects.get(debate=debate)
        self.assertEqual((result.total_participants, result.final_con, result.stance_changed_count), (1, 1, 1))

    def test_open_expired_debate_is_not_closed(self):
        debate = self.debate(Debate.Status.OPEN)
        self.assertEqual(self.run_sweep()['examined'], 0)
        debate.refresh_from_db()
        self.assertEqual(debate.status, Debate.Status.OPEN)
        self.assertFalse(DebateStatistics.objects.filter(debate=debate).exists())

    def test_completed_result_is_not_overwritten(self):
        debate = self.debate()
        self.run_sweep()
        timestamp = DebateStatistics.objects.get(debate=debate).computed_at
        self.assertEqual(self.run_sweep()['examined'], 0)
        self.assertEqual(DebateStatistics.objects.get(debate=debate).computed_at, timestamp)

    def test_cooling_failure_does_not_starve_later_result(self):
        cooling = self.debate()
        ready = self.debate()
        set_result_state(cooling.pk, 'FAILED')
        self.assertEqual(get_result_state(cooling.pk)['status'], 'FAILED')
        result = self.run_sweep(batch_size=1)
        self.assertEqual((result['completed'], result['deferred']), (1, 1))
        self.assertTrue(DebateStatistics.objects.filter(debate=ready).exists())
        self.assertFalse(DebateStatistics.objects.filter(debate=cooling).exists())

    def test_missing_redis_hint_does_not_prevent_recovery(self):
        debate = self.debate()
        set_result_state(debate.pk, 'FAILED')
        clear_result_state(debate.pk)
        self.assertEqual(self.run_sweep()['completed'], 1)

    def test_parallel_sweep_is_skipped(self):
        self.debate()
        other = connection.copy(alias='recovery-lock-check')
        try:
            with other.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_lock(%s)', [1927410315])
            result = self.run_sweep()
            self.assertTrue(result['skippedLocked'])
            self.assertEqual(result['completed'], 0)
        finally:
            with other.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [1927410315])
            other.close()

    def test_invalid_limits_rejected(self):
        with self.assertRaises(CommandError):
            self.run_sweep(batch_size=0)
