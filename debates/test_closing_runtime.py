import io
import json
from datetime import timedelta

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import User
from debates.models import Category, Debate, DebateStatistics


class ClosingCommandTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('closing@example.invalid', 'Closing')
        self.category = Category.objects.create(name='Closing')
        self.now = timezone.now()

    def debate(self, **overrides):
        fields = dict(creator=self.user, category=self.category, type='QUICK',
                      title='Closing test', content='Topic', duration_minutes=5,
                      status='OPEN', started_at=self.now - timedelta(minutes=6),
                      ends_at=self.now - timedelta(minutes=1))
        fields.update(overrides)
        return Debate.objects.create(**fields)

    def close(self):
        output = io.StringIO()
        call_command('close_debates', stdout=output)
        return json.loads(output.getvalue())['closed']

    def test_persists_deadline_and_does_not_overwrite_on_retry(self):
        debate = self.debate()
        self.assertEqual(self.close(), 1)
        debate.refresh_from_db()
        self.assertEqual((debate.status, debate.closed_at, debate.ends_at),
                         ('CLOSED', self.now - timedelta(minutes=1), self.now - timedelta(minutes=1)))
        self.assertEqual(self.close(), 0)
        debate.refresh_from_db()
        self.assertEqual(debate.closed_at, self.now - timedelta(minutes=1))

    def test_closes_legacy_deadlines_for_both_types(self):
        quick = self.debate(ends_at=None)
        endless = self.debate(type='ENDLESS', duration_minutes=None, ends_at=None,
                              started_at=self.now - timedelta(hours=73))
        self.assertEqual(self.close(), 2)
        quick.refresh_from_db()
        endless.refresh_from_db()
        self.assertEqual(quick.closed_at, quick.started_at + timedelta(minutes=5))
        self.assertEqual(endless.closed_at, endless.started_at + timedelta(hours=72))

    def test_does_not_change_future_draft_canceled_or_closed_debates(self):
        rows = [self.debate(ends_at=self.now + timedelta(minutes=5)),
                self.debate(status='DRAFT'), self.debate(status='CANCELED'),
                self.debate(status='CLOSED', closed_at=self.now - timedelta(seconds=10))]
        before = list(Debate.objects.filter(pk__in=[d.pk for d in rows]).values())
        self.assertEqual(self.close(), 0)
        self.assertEqual(list(Debate.objects.filter(pk__in=[d.pk for d in rows]).values()), before)

    def test_backlog_is_processed_in_bounded_batches(self):
        for _ in range(101):
            self.debate()
        self.assertEqual(self.close(), 100)
        self.assertEqual(self.close(), 1)
        self.assertEqual(self.close(), 0)

    def test_locked_debate_is_retried_on_next_run(self):
        locked = self.debate()
        ready = self.debate()
        other = connection.copy(alias='closing-lock-check')
        try:
            other.set_autocommit(False)
            with other.cursor() as cursor:
                cursor.execute('SELECT id FROM debates WHERE id = %s FOR UPDATE', [locked.pk])
            self.assertEqual(self.close(), 1)
            ready.refresh_from_db()
            locked.refresh_from_db()
            self.assertEqual((ready.status, locked.status), ('CLOSED', 'OPEN'))
        finally:
            other.rollback()
            other.close()
        self.assertEqual(self.close(), 1)

    def test_closed_debate_is_picked_up_by_existing_result_recovery(self):
        debate = self.debate()
        self.assertEqual(self.close(), 1)
        output = io.StringIO()
        call_command('recover_results', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['completed'], 1)
        self.assertTrue(DebateStatistics.objects.filter(debate=debate).exists())
